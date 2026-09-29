"""Conversation-scoped document storage, parsing and bounded Harness access."""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

from docx import Document
from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from . import attachment_vision, execution_events, ingest, material_pack

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
MAX_TOTAL_SIZE = 50 * 1024 * 1024
SUPPORTED = {"md", "markdown", "txt", "docx", "pdf", "xlsx", "csv", "doc", "xls"}
# 图片放行：上传即转写（见 attachment_vision），执行阶段模型只读文本
IMAGE_TYPES = attachment_vision.IMAGE_TYPES
SUPPORTED |= IMAGE_TYPES
# 材料包：一次上传一个 zip 或一批文件，按业务号自动分组，单次不限个数
MAX_PACK_FILES = material_pack.PACK_MAX_FILES
MAX_PACK_TOTAL_SIZE = material_pack.PACK_MAX_UNPACKED


def pack_limits() -> tuple[int, int]:
    """材料包配额。放函数里是为了让测试能改环境变量，不用重载模块。"""
    try:
        files = int(os.getenv("DWP_ATTACHMENT_PACK_MAX_FILES", "") or MAX_PACK_FILES)
    except ValueError:
        files = MAX_PACK_FILES
    try:
        total = int(os.getenv("DWP_ATTACHMENT_PACK_MAX_TOTAL_MB", "") or "500") * 1024 * 1024
    except ValueError:
        total = MAX_PACK_TOTAL_SIZE
    return max(1, files), max(1, total)


def attachment_root() -> Path:
    configured = os.getenv("DWP_ATTACHMENT_ROOT", "").strip()
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "attachments"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(filename: str) -> str:
    name = Path((filename or "").replace("\\", "/")).name.strip()
    if not name or name in {".", ".."}:
        raise HTTPException(status_code=422, detail="文件名不合法")
    return name[:240]


def _validate_signature(file_type: str, data: bytes, filename: str = "") -> None:
    if not data:
        raise HTTPException(status_code=422, detail="不能上传空文件")
    if file_type == "pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="文件内容与 PDF 扩展名不符")
    if file_type in {"docx", "xlsx"} and not data.startswith(b"PK"):
        raise HTTPException(status_code=422, detail="文件内容与 Office 扩展名不符")
    if file_type in IMAGE_TYPES:
        # 图片按统一 ingest 的魔数口径校验：后缀改名、损坏文件在这里就拦掉，
        # 不要等进了视觉链路再失败（那时用户已经等了几十秒）。
        try:
            kind, how = ingest.detect_kind_bytes(data, filename or ("x." + file_type))
        except ingest.UnsupportedInput as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        if kind != ingest.KIND_IMAGE:
            raise HTTPException(status_code=422,
                                detail="文件内容与图片扩展名不符（识别为 %s/%s）" % (kind, how))
    if file_type in {"docx", "xlsx"}:
        try:
            with zipfile.ZipFile(io.BytesIO(data)) as archive:
                infos = archive.infolist()
                if len(infos) > 2000 or sum(item.file_size for item in infos) > 100 * 1024 * 1024:
                    raise HTTPException(status_code=422, detail="Office 文件解压后体积异常")
                for item in infos:
                    path = Path(item.filename.replace("\\", "/"))
                    if path.is_absolute() or ".." in path.parts:
                        raise HTTPException(status_code=422, detail="Office 文件包含不安全路径")
        except zipfile.BadZipFile as exc:
            raise HTTPException(status_code=422, detail="Office 文件已损坏") from exc


def create_attachment(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    filename: str, mime_type: str, data: bytes,
) -> models.ConversationAttachment:
    if conversation.owner_human_no != owner_human_no:
        raise HTTPException(status_code=403, detail="无权向该会话上传文件")
    if len(data) > MAX_FILE_SIZE:
        raise HTTPException(status_code=413, detail="单个文件不能超过 20 MiB")
    safe_name = _safe_filename(filename)
    file_type = Path(safe_name).suffix.lower().lstrip(".")
    if file_type not in SUPPORTED:
        raise HTTPException(status_code=422, detail="暂不支持该文件格式")
    _validate_signature(file_type, data, safe_name)
    pending = db.scalars(select(models.ConversationAttachment).where(
        models.ConversationAttachment.conversation_id == conversation.id,
        models.ConversationAttachment.generation == conversation.generation,
        models.ConversationAttachment.message_seq.is_(None),
        models.ConversationAttachment.deleted.is_(False),
    )).all()
    if len(pending) >= MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=422, detail="单次消息最多上传 5 个文件")
    if sum(row.size for row in pending) + len(data) > MAX_TOTAL_SIZE:
        raise HTTPException(status_code=413, detail="单次消息附件总量不能超过 50 MiB")

    attachment_id = f"ATT-{uuid4().hex[:16].upper()}"
    relative = Path(owner_human_no) / conversation.id / attachment_id / safe_name
    target = attachment_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    special = file_type in {"doc", "xls"}
    row = models.ConversationAttachment(
        id=attachment_id, conversation_id=conversation.id, owner_human_no=owner_human_no,
        generation=conversation.generation, filename=safe_name, file_type=file_type,
        mime_type=mime_type or "application/octet-stream", size=len(data),
        sha256=hashlib.sha256(data).hexdigest(), storage_path=relative.as_posix(),
        status="conversion_required" if special else "pending",
        processing_hint="需要测试环境的文档转换 MCP" if special else None,
        error_code="CONVERSION_REQUIRED" if special else "",
    )
    db.add(row)
    db.flush()
    if not special:
        db.add(models.AttachmentProcessingJob(id=f"APJ-{uuid4().hex[:16].upper()}", attachment_id=row.id))
    db.commit()
    db.refresh(row)
    return row


def _split_text(text: str, location: str, *, minimum: int = 1200, maximum: int = 1800) -> list[tuple[str, str, dict]]:
    normalized = re.sub(r"\r\n?", "\n", text).strip()
    if not normalized:
        return []
    blocks, buffer = [], ""
    for paragraph in re.split(r"\n{2,}", normalized):
        paragraph = paragraph.strip()
        if not paragraph:
            continue
        if buffer and len(buffer) + len(paragraph) + 2 > maximum:
            blocks.append((buffer, location, {})); buffer = ""
        while len(paragraph) > maximum:
            take = paragraph[:maximum]
            blocks.append((take, location, {})); paragraph = paragraph[maximum:]
        buffer = f"{buffer}\n\n{paragraph}".strip()
        if len(buffer) >= minimum:
            blocks.append((buffer, location, {})); buffer = ""
    if buffer:
        blocks.append((buffer, location, {}))
    return blocks


def _decode_text(data: bytes) -> str:
    for encoding in ("utf-8-sig", "utf-8", "gb18030"):
        try:
            return data.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise ValueError("文本编码不是 UTF-8 或 GB18030")


def _parse(row: models.ConversationAttachment, path: Path) -> tuple[list[tuple[str, str, dict]], int | None, int | None]:
    chunks: list[tuple[str, str, dict]] = []
    if row.file_type in {"md", "markdown", "txt"}:
        chunks = _split_text(_decode_text(path.read_bytes()), "全文")
    elif row.file_type == "docx":
        document = Document(path)
        paragraphs = [p.text.strip() for p in document.paragraphs if p.text.strip()]
        for table_index, table in enumerate(document.tables, 1):
            paragraphs.append(f"表格 {table_index}")
            paragraphs.extend(" | ".join(cell.text.strip() for cell in line.cells) for line in table.rows)
        chunks = _split_text("\n\n".join(paragraphs), "文档正文")
    elif row.file_type == "pdf":
        return _parse_pdf(row, path)
    elif row.file_type in IMAGE_TYPES:
        return _parse_image(row, path)
    elif row.file_type == "xlsx":
        workbook = load_workbook(path, read_only=True, data_only=True)
        try:
            for sheet in workbook.worksheets:
                for row_no, values in enumerate(sheet.iter_rows(values_only=True), 1):
                    rendered = " | ".join("" if value is None else str(value) for value in values).strip(" |")
                    if rendered:
                        chunks.append((rendered[:4000], f"{sheet.title}!{row_no}", {"sheet": sheet.title, "row": row_no}))
            return chunks, None, len(workbook.sheetnames)
        finally:
            workbook.close()
    elif row.file_type == "csv":
        text = _decode_text(path.read_bytes())
        try:
            dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
        except csv.Error:
            dialect = csv.excel
        for row_no, values in enumerate(csv.reader(io.StringIO(text), dialect), 1):
            rendered = " | ".join(values).strip(" |")
            if rendered:
                chunks.append((rendered[:4000], f"表格!{row_no}", {"sheet": "表格", "row": row_no}))
        return chunks, None, 1
    return chunks, None, None


def _split_pdf_pages(text_lines: list[str]) -> list[tuple[str, str, dict]]:
    """把 ingest 输出的转写行按 `[[第N页]]` 标记切回逐页块。"""
    chunks: list[tuple[str, str, dict]] = []
    page_no, buffer = None, []
    for line in text_lines:
        marker = re.match(r"^\[\[第(\d+)页\]\]$", line)
        if marker:
            if buffer:
                chunks.extend(_split_text("\n".join(buffer), "第 %s 页" % page_no))
            page_no, buffer = marker.group(1), []
            continue
        buffer.append(line)
    if buffer:
        chunks.extend(_split_text("\n".join(buffer), "第 %s 页" % page_no))
    return chunks


def _parse_pdf(row: models.ConversationAttachment, path: Path):
    """PDF 逐页判文本层：电子页直读、扫描页渲染后走视觉转写。

    支持「电子页 + 扫描页混排」的件；单页渲染失败只跳过该页，不整份失败。
    """
    page_dir = path.parent / (path.stem + ".pages")
    plan = ingest.ingest(path, page_dir, path.stem)
    chunks = _split_pdf_pages(plan["text_lines"])
    if plan["image_pages"]:
        if not attachment_vision.vision_enabled():
            raise RuntimeError("OCR_REQUIRED")
        page_paths = [Path(item["path"]) for item in plan["image_pages"]]
        lines, regions = attachment_vision.transcribe_pages(page_paths)
        text = attachment_vision.transcript_text(lines, regions)
        attachment_vision.write_transcript_file(path, text)
        chunks.extend(_split_text(text, "扫描页转写"))
    if not chunks:
        raise RuntimeError("OCR_REQUIRED")
    return chunks, len(plan["pages"]) or None, None


def _parse_image(row: models.ConversationAttachment, path: Path):
    """图片：先自适应归一化（方向/色彩/多帧/尺寸），再视觉转写，产物落盘可人工核对。"""
    if not attachment_vision.vision_enabled():
        raise RuntimeError("OCR_REQUIRED")
    work = path.parent / (path.stem + ".normalized")
    frames = ingest.normalize_image(path, work, path.stem)
    page_paths = [Path(item["path"]) for item in frames]
    lines, regions = attachment_vision.transcribe_pages(page_paths)
    text = attachment_vision.transcript_text(lines, regions)
    attachment_vision.write_transcript_file(path, text)
    chunks = _split_text(text, "图像转写")
    if not chunks:
        raise RuntimeError("OCR_REQUIRED")
    return chunks, len(page_paths), None


def create_pack_attachment(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    filename: str, data: bytes,
) -> list[models.ConversationAttachment]:
    """材料包上传（一个 zip）：摊成多份附件，单次不限个数。

    与单文件上传的区别只有三点：不受「单次 5 个」限制、每份附件带 pack_id 与
    business_no（业务号只是提示，不再决定分笔）、发起执行时物化到工作区。
    """
    if conversation.owner_human_no != owner_human_no:
        raise HTTPException(status_code=403, detail="无权向该会话上传文件")
    max_files, max_total = pack_limits()
    if len(data) > max_total:
        raise HTTPException(status_code=413,
                            detail="材料包不能超过 %.0f MiB" % (max_total / 1024 / 1024))
    pack_id = "PACK-%s" % uuid4().hex[:12].upper()
    pack_dir = attachment_root() / owner_human_no / conversation.id / pack_id
    try:
        groups = material_pack.unpack_zip(data, pack_dir / "_unpacked",
                                          max_files=max_files, max_unpacked=max_total)
    except material_pack.PackError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _persist_pack_rows(db, conversation=conversation, owner_human_no=owner_human_no,
                              pack_id=pack_id, groups=groups)


def create_pack_files_attachment(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    files: list[tuple[str, bytes]],
) -> list[models.ConversationAttachment]:
    """一批文件直接上传（没有 zip）：与 zip 通道同一条落库/物化链路，只是入口不同。"""
    if conversation.owner_human_no != owner_human_no:
        raise HTTPException(status_code=403, detail="无权向该会话上传文件")
    max_files, max_total = pack_limits()
    if len(files) > max_files:
        raise HTTPException(status_code=422, detail="单次最多上传 %d 份材料" % max_files)
    total = sum(len(payload) for _name, payload in files)
    if total > max_total:
        raise HTTPException(status_code=413,
                            detail="材料总量不能超过 %.0f MiB" % (max_total / 1024 / 1024))
    pack_id = "PACK-%s" % uuid4().hex[:12].upper()
    pack_dir = attachment_root() / owner_human_no / conversation.id / pack_id
    try:
        groups = material_pack.unpack_files(files, pack_dir / "_unpacked",
                                            max_files=max_files, max_unpacked=max_total)
    except material_pack.PackError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _persist_pack_rows(db, conversation=conversation, owner_human_no=owner_human_no,
                              pack_id=pack_id, groups=groups)


def _persist_pack_rows(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    pack_id: str, groups: dict[str, list],
) -> list[models.ConversationAttachment]:
    """把摊开的材料逐份登记成附件。

    `business_no` 只记录提示；落盘布局由 `DWP_MATERIAL_FLAT_LAYOUT` 决定（默认平铺，
    不再按业务号建子目录 —— 分笔交给 AI 员工按材料内容判断）。
    """
    flat = material_pack.flat_layout()
    rows: list[models.ConversationAttachment] = []
    for business_no, paths in sorted(groups.items()):
        for source in paths:
            file_type = source.suffix.lower().lstrip(".")
            if file_type not in SUPPORTED:
                continue
            payload = source.read_bytes()
            if len(payload) > MAX_FILE_SIZE:
                raise HTTPException(
                    status_code=413,
                    detail="材料 %s 超过单文件上限 20 MiB" % source.name)
            _validate_signature(file_type, payload, source.name)
            attachment_id = f"ATT-{uuid4().hex[:16].upper()}"
            parts = [owner_human_no, conversation.id, pack_id]
            if not flat:
                parts.append(business_no)
            parts.append(source.name)
            relative = Path(*parts)
            target = attachment_root() / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(payload)
            special = file_type in {"doc", "xls"}
            row = models.ConversationAttachment(
                id=attachment_id, conversation_id=conversation.id,
                owner_human_no=owner_human_no, generation=conversation.generation,
                filename=source.name, file_type=file_type,
                mime_type="application/octet-stream", size=len(payload),
                sha256=hashlib.sha256(payload).hexdigest(), storage_path=relative.as_posix(),
                status="conversion_required" if special else "pending",
                processing_hint="需要测试环境的文档转换 MCP" if special else None,
                error_code="CONVERSION_REQUIRED" if special else "",
                pack_id=pack_id, business_no=business_no,
            )
            db.add(row)
            db.flush()
            if not special:
                db.add(models.AttachmentProcessingJob(
                    id=f"APJ-{uuid4().hex[:16].upper()}", attachment_id=row.id))
            rows.append(row)
    if not rows:
        raise HTTPException(status_code=422, detail="材料包里没有可用的材料文件")
    db.commit()
    for row in rows:
        db.refresh(row)
    return rows


def process_job(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(models.AttachmentProcessingJob, job_id)
        if not job or job.status == "completed":
            return
        row = db.get(models.ConversationAttachment, job.attachment_id)
        if not row or row.deleted:
            job.status = "failed"; job.error_summary = "附件已不存在"; db.commit(); return
        job.status = "processing"; job.attempts += 1; job.updated_at = datetime.now()
        row.status = "processing"; row.updated_at = datetime.now(); db.commit()
        try:
            chunks, pages, sheets = _parse(row, attachment_root() / row.storage_path)
            if not chunks:
                raise ValueError("未提取到有效内容")
            # 解析期间用户可能清空/删除会话：写回前复检附件行是否仍有效，避免留下孤儿解析片段。
            db.expire_all()
            fresh = db.get(models.ConversationAttachment, row.id)
            if fresh is None or fresh.deleted:
                fresh_job = db.get(models.AttachmentProcessingJob, job_id)
                if fresh_job is not None:
                    fresh_job.status = "failed"
                    fresh_job.error_summary = "附件已被清除"
                    fresh_job.updated_at = datetime.now()
                    db.commit()
                return
            row = fresh
            db.execute(delete(models.AttachmentChunk).where(models.AttachmentChunk.attachment_id == row.id))
            for index, (content, location, metadata) in enumerate(chunks):
                db.add(models.AttachmentChunk(
                    attachment_id=row.id, chunk_no=index, content=content,
                    location=location, metadata_json=metadata,
                ))
            row.status = "ready"; row.error_code = ""; row.processing_hint = None
            row.page_count = pages; row.sheet_count = sheets; row.chunk_count = len(chunks)
            job.status = "completed"; job.error_summary = ""
        except RuntimeError as exc:
            if str(exc) == "OCR_REQUIRED":
                row.status = "ocr_required"; row.error_code = "OCR_REQUIRED"
                row.processing_hint = "需要测试环境的 OCR MCP"
                job.status = "completed"
            else:
                raise
        except Exception as exc:  # noqa: BLE001
            row.status = "failed"; row.error_code = "PARSE_FAILED"
            row.processing_hint = "文件解析失败，可重新尝试"
            job.status = "failed"; job.error_summary = exc.__class__.__name__
        row.updated_at = datetime.now(); job.updated_at = datetime.now(); db.commit()
    finally:
        db.close()


def process_jobs(job_ids: list[str]) -> None:
    """并发处理一次上传产生的全部解析任务。

    为什么需要批量入口：Starlette 的 ``BackgroundTasks`` 是顺序 ``await`` 的，
    逐个 ``add_task(process_job, ...)`` 会让 N 份材料退化成 N 次串行视觉调用
    （``attachment_vision`` 里的全局信号量与线程池在「每份附件只有 1 张图」
    时完全空转）。并发度直接取视觉层的全局信号量上限 —— 它本来就是按
    「网关 16 路安全、24 路 502」的实测边界设的，不新增第二套限流。

    并发安全：``process_job`` 每任务自开 ``SessionLocal``（session 不跨线程），
    长耗时的视觉调用发生在 ``db.commit()`` 之后、不在事务里，SQLite 已开 WAL +
    ``busy_timeout``，只剩短写竞争。

    单份失败只影响该份附件状态（``PARSE_FAILED``），不中断其余份。
    """
    ids = list(dict.fromkeys(job_id for job_id in job_ids if job_id))
    if not ids:
        return
    started = time.monotonic()
    workers = 1
    if len(ids) == 1:
        # 单份不建池：并发池只为「一次上传多份」存在，省掉一层线程开销。
        process_job(ids[0])
    else:
        workers = min(len(ids), attachment_vision._max_inflight())
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(process_job, job_id) for job_id in ids]
            for future in as_completed(futures):
                try:
                    future.result()
                except Exception as exc:  # noqa: BLE001 - 单份异常不拖垮整批
                    logger.warning("附件解析任务异常：%s", exc)
    logger.info("附件解析批次完成：%d 份，并发 %d，耗时 %.1fs",
                len(ids), workers, time.monotonic() - started)


def recover_pending_jobs() -> None:
    db = SessionLocal()
    try:
        rows = db.scalars(select(models.AttachmentProcessingJob).where(
            models.AttachmentProcessingJob.status.in_(["pending", "processing"])
        )).all()
        ids = [row.id for row in rows]
        for row in rows:
            row.status = "pending"
        db.commit()
    finally:
        db.close()
    # 重启补跑同样走批量入口：否则恢复出来的任务又是一条长串行链。
    process_jobs(ids)


def bind_to_message(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    message_seq: int, attachment_ids: list[str],
) -> list[models.ConversationAttachment]:
    if len(set(attachment_ids)) != len(attachment_ids):
        raise HTTPException(status_code=422, detail="附件数量不合法")
    max_files, max_total = pack_limits()
    if len(attachment_ids) > max_files:
        raise HTTPException(status_code=422, detail="附件数量不合法")
    rows = []
    for attachment_id in attachment_ids:
        row = db.get(models.ConversationAttachment, attachment_id)
        if not row or row.deleted or row.conversation_id != conversation.id or row.owner_human_no != owner_human_no or row.generation != conversation.generation:
            raise HTTPException(status_code=404, detail="附件不存在或不属于当前会话")
        if row.status != "ready":
            raise HTTPException(status_code=409, detail=f"附件 {row.filename} 尚未解析完成")
        if row.message_seq is not None:
            raise HTTPException(status_code=409, detail=f"附件 {row.filename} 已被其他消息使用")
        rows.append(row)
    # 材料包（一次一笔业务、动辄几十份材料）不受「单次 5 个」限制，
    # 但仍受包配额约束；普通零散上传维持原来的 5 个 / 50 MiB 口径。
    pack_rows = [row for row in rows if getattr(row, "pack_id", None)]
    plain_rows = [row for row in rows if not getattr(row, "pack_id", None)]
    if len(plain_rows) > MAX_FILES_PER_MESSAGE:
        raise HTTPException(status_code=422, detail="单次消息最多上传 5 个文件")
    limit = max_total if pack_rows else MAX_TOTAL_SIZE
    if sum(row.size for row in rows) > limit:
        raise HTTPException(
            status_code=413,
            detail="附件总量不能超过 %.0f MiB" % (limit / 1024 / 1024))
    for row in rows:
        row.message_seq = message_seq; row.updated_at = datetime.now()
    db.commit()
    return rows


def attachments_for_trace(db: Session, trace_id: str) -> tuple[models.AgentExecution | None, list[models.ConversationAttachment]]:
    execution = execution_events.execution_for_trace(db, trace_id)
    if not execution:
        return None, []
    trigger = db.scalar(select(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == execution.conversation_id,
        models.ConversationMessage.generation == execution.conversation_generation,
        models.ConversationMessage.seq == execution.trigger_message_seq,
        models.ConversationMessage.role == "user",
    ))
    selected_ids = list(trigger.attachment_ids or []) if trigger is not None else []
    rows = db.scalars(select(models.ConversationAttachment).where(
        models.ConversationAttachment.conversation_id == execution.conversation_id,
        models.ConversationAttachment.generation == execution.conversation_generation,
        models.ConversationAttachment.id.in_(selected_ids),
        models.ConversationAttachment.deleted.is_(False),
        models.ConversationAttachment.status == "ready",
    )).all()
    return execution, rows


def attachment_catalog(db: Session, trace_id: str) -> list[dict]:
    _, rows = attachments_for_trace(db, trace_id)
    return [{
        "attachment_id": row.id, "filename": row.filename, "file_type": row.file_type,
        "page_count": row.page_count, "sheet_count": row.sheet_count, "chunk_count": row.chunk_count,
        # 材料包附件带上包与业务号：模型据此知道 /workspace/materials/<包>/<业务号>/ 的布局
        **({"pack_id": row.pack_id} if row.pack_id else {}),
        **({"business_no": row.business_no} if row.business_no else {}),
    } for row in rows]


def _authorized_attachment(db: Session, trace_id: str, requester: str, attachment_id: str) -> tuple[models.AgentExecution, models.ConversationAttachment]:
    execution, rows = attachments_for_trace(db, trace_id)
    row = next((item for item in rows if item.id == attachment_id), None)
    if not execution or not row or row.owner_human_no != requester:
        raise HTTPException(status_code=403, detail="附件不在当前任务授权范围内")
    return execution, row


def search_attachment(db: Session, *, trace_id: str, requester: str, employee_id: str, attachment_id: str, query: str, top_k: int = 5) -> dict:
    execution, row = _authorized_attachment(db, trace_id, requester, attachment_id)
    execution_events.emit(db, execution.id, event_type="attachment_search_started", stage="attachment", status="running", actor_employee_id=employee_id, title=f"正在检索附件 {row.filename}", detail="正在定位与问题相关的文件内容")
    terms = [term.lower() for term in re.findall(r"[\w\u4e00-\u9fff]+", query) if term.strip()]
    chunks = db.scalars(select(models.AttachmentChunk).where(models.AttachmentChunk.attachment_id == row.id)).all()
    ranked = sorted(chunks, key=lambda item: sum(item.content.lower().count(term) for term in terms), reverse=True)
    hits = [{"chunk_id": item.id, "location": item.location, "text": item.content} for item in ranked[:max(1, min(top_k, 8))] if not terms or any(term in item.content.lower() for term in terms)]
    if not hits:
        hits = [{"chunk_id": item.id, "location": item.location, "text": item.content} for item in ranked[:max(1, min(top_k, 3))]]
    execution_events.emit(db, execution.id, event_type="attachment_search_completed", stage="attachment", status="running", actor_employee_id=employee_id, title=f"附件 {row.filename} 检索完成", detail=f"找到 {len(hits)} 段可用内容", hit_count=len(hits))
    return {"attachment_id": row.id, "filename": row.filename, "hits": hits}


def read_attachment(db: Session, *, trace_id: str, requester: str, employee_id: str, attachment_id: str, chunk_ids: list[int]) -> dict:
    execution, row = _authorized_attachment(db, trace_id, requester, attachment_id)
    ids = list(dict.fromkeys(chunk_ids))[:8]
    chunks = db.scalars(select(models.AttachmentChunk).where(models.AttachmentChunk.attachment_id == row.id, models.AttachmentChunk.id.in_(ids))).all() if ids else []
    execution_events.emit(db, execution.id, event_type="attachment_read", stage="attachment", status="running", actor_employee_id=employee_id, title=f"已读取附件 {row.filename}", detail=f"读取 {len(chunks)} 个文件片段")
    return {"attachment_id": row.id, "filename": row.filename, "chunks": [{"chunk_id": item.id, "location": item.location, "text": item.content} for item in chunks]}


def read_spreadsheet_range(db: Session, *, trace_id: str, requester: str, employee_id: str, attachment_id: str, sheet: str, cell_range: str) -> dict:
    execution, row = _authorized_attachment(db, trace_id, requester, attachment_id)
    if row.file_type != "xlsx":
        raise HTTPException(status_code=422, detail="该附件不是 XLSX 文件")
    min_col, min_row, max_col, max_row = range_boundaries(cell_range.upper())
    if (max_col - min_col + 1) * (max_row - min_row + 1) > 200:
        raise HTTPException(status_code=422, detail="单次最多读取 200 个单元格")
    workbook = load_workbook(attachment_root() / row.storage_path, read_only=True, data_only=True)
    try:
        if sheet not in workbook.sheetnames:
            raise HTTPException(status_code=404, detail="工作表不存在")
        worksheet = workbook[sheet]
        values = [[cell.value for cell in cells] for cells in worksheet.iter_rows(min_row=min_row, max_row=max_row, min_col=min_col, max_col=max_col)]
    finally:
        workbook.close()
    execution_events.emit(db, execution.id, event_type="attachment_read", stage="attachment", status="running", actor_employee_id=employee_id, title=f"已读取 {row.filename} 的 {sheet}!{cell_range}", detail=f"读取 {(max_col-min_col+1)*(max_row-min_row+1)} 个单元格")
    return {"attachment_id": row.id, "filename": row.filename, "sheet": sheet, "range": cell_range, "values": values}


def _auto_extract_fields(employee_id: str, pack_root) -> dict:
    """物化后自动跑一次材料级抽值（容器内 `review.py extract-all`）。

    抽取规则与实现都在技能包里，平台只按次调用 —— 在这里再实现一份必然与规则表漂移。
    失败只告警不阻断：材料已经物化好了，模型仍可用分步命令自己补抽。
    """
    from . import runtime_manager

    skills_root = runtime_manager.skills_root(employee_id)
    try:
        scripts = sorted(path for path in skills_root.glob("*/scripts/review.py")
                         if path.is_file())
    except OSError as exc:
        logger.warning("技能目录读取失败 employee=%s: %s", employee_id, exc)
        return {}
    if not scripts:
        return {}
    container_name = runtime_manager.container_name(employee_id)
    materials_dir = "/workspace/materials/%s" % pack_root.name
    report: dict = {}
    for script in scripts:
        slug = script.relative_to(skills_root).parts[0]
        command = ["python3", "/workspace/skills/%s/scripts/review.py" % slug,
                   "extract-all", "--materials", materials_dir]
        try:
            proc = runtime_manager.exec_in_container(container_name, command, timeout=180)
        except RuntimeError as exc:
            logger.warning("自动抽值未执行（%s）：%s", slug, exc)
            continue
        if getattr(proc, "returncode", 1) != 0:
            logger.warning("自动抽值失败（%s）rc=%s：%s", slug,
                           getattr(proc, "returncode", "?"),
                           (getattr(proc, "stderr", "") or "")[-300:])
            continue
        try:
            report[slug] = json.loads((getattr(proc, "stdout", "") or "").strip())
        except ValueError:
            logger.warning("自动抽值输出无法解析（%s）：%s", slug,
                           (getattr(proc, "stdout", "") or "")[:200])
    return report


def materialize_materials(db: Session, *, employee_id: str, trace_id: str) -> dict:
    """把本轮附件物化到目标 AI 员工的 ``/workspace/materials/<材料包>/``。

    容器内看到的就是这些 JSON 文件，模型据此清点材料、自己判类型、抽字段。
    平台**不预设材料类型**（`material_type` 留空由 agent 判断），只给事实层 `text_lines`。

    默认**平铺**（`DWP_MATERIAL_FLAT_LAYOUT=1`）：一个材料包一层目录，不按业务号分子目录，
    分笔由 AI 员工按材料内容判断（`review.py --codes` 圈定）。`hint_business_no` 只是
    按文件名/目录名推测的提示，可能不准；置 `0` 回到「按业务号建子目录」的旧行为。

    幂等：内容 sha 不变就不重写；只清理本次物化的目录，不动容器里的其他文件。
    """
    from . import runtime_manager

    _, rows = attachments_for_trace(db, trace_id)
    if not rows:
        return {"materials": 0, "root": None, "businesses": [],
                "layout": "flat" if material_pack.flat_layout() else "by_business_no",
                "catalog": []}
    flat = material_pack.flat_layout()
    pack = next((row.pack_id for row in rows if row.pack_id), None) or "PACK-DEFAULT"
    root = runtime_manager.materials_root(employee_id) / pack
    root.mkdir(parents=True, exist_ok=True)

    manifest_materials = []
    # 平铺后同名材料会撞车：按稳定顺序分配唯一代号（重名追加 _2/_3），代号是模型定位材料的锚点
    seen: dict[str, int] = {}
    for row in sorted(rows, key=lambda item: (item.business_no or "", item.filename, item.id)):
        hint = row.business_no or material_pack.UNGROUPED
        code = Path(row.filename).stem
        seen[code] = seen.get(code, 0) + 1
        if seen[code] > 1:
            code = "%s_%d" % (code, seen[code])
        target_dir = root if flat else root / hint
        target_dir.mkdir(parents=True, exist_ok=True)
        chunks = db.scalars(select(models.AttachmentChunk).where(
            models.AttachmentChunk.attachment_id == row.id
        ).order_by(models.AttachmentChunk.chunk_no)).all()
        lines, regions = [], []
        for chunk in chunks:
            for line in (chunk.content or "").split("\n"):
                line = line.strip()
                if not line:
                    continue
                if line.startswith("[[UNREADABLE]]"):
                    regions.append({"desc": line.replace("[[UNREADABLE]]", "").strip()})
                else:
                    lines.append(line)
        payload = {
            "material_code": code,
            "material_type": None,        # 由 agent 判断，平台不预设
            "source_file": row.filename,
            "file_type": row.file_type,
            "kind": "image" if row.file_type in IMAGE_TYPES else row.file_type,
            # 只是按文件名/目录名推测的提示，可能与材料内容不符
            "hint_business_no": hint,
            "text_lines": lines,
            "fields": [],
            "unreadable_regions": regions,
        }
        target = target_dir / ("%s.json" % code)
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        digest = hashlib.sha256(body.encode("utf-8")).hexdigest()
        marker = target_dir / (".%s.sha" % code)
        if not (target.exists() and marker.exists()
                and marker.read_text(encoding="utf-8").strip() == digest):
            target.write_text(body, encoding="utf-8")
            marker.write_text(digest, encoding="utf-8")
        manifest_materials.append({
            "code": code, "hint_business_no": hint,
            "material_type": None, "source_file": row.filename,
            "file_type": row.file_type, "attachment_id": row.id,
            # 转写行数随清单一起交给模型：它据此知道每份材料有多少可读内容，
            # 不必先跑一次 inspect 去清点。
            "transcript_lines": len(lines),
        })

    manifest = {
        "pack_id": pack,
        "layout": "flat" if flat else "by_business_no",
        "note": ("hint_business_no 仅由文件名/目录名推测，仅供参考，可能与实际业务不符；"
                 "哪些材料属于同一笔业务请按材料内容自行判断，"
                 "再用 review.py --codes 圈定本轮要核的材料。"
                 "fields 已由平台在物化后自动抽好（含系统录入侧截图）——"
                 "系统录入侧是比对的基准方，它没有取值时逐字段比对会退化成客户材料互相印证。"),
        "material_count": len(manifest_materials),
        "businesses": sorted({item["hint_business_no"] for item in manifest_materials}),
        "materials": manifest_materials,
    }
    (root / "materials.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    # 上传即抽取：物化完成就地跑一次技能包的确定性抽值，模型不必记得先跑 extract。
    # 失败只告警不阻断（材料已就绪，模型仍可用分步命令补抽）。
    extraction: dict = {}
    if material_pack.auto_extract():
        extraction = _auto_extract_fields(employee_id, root)
    return {"materials": len(manifest_materials), "root": str(root),
            "businesses": manifest["businesses"], "pack_id": pack,
            "layout": manifest["layout"], "catalog": manifest_materials,
            "extraction": extraction}


def cleanup_orphans(db: Session) -> int:
    cutoff = datetime.now() - timedelta(hours=24)
    rows = db.scalars(select(models.ConversationAttachment).where(
        models.ConversationAttachment.message_seq.is_(None),
        models.ConversationAttachment.created_at < cutoff,
        models.ConversationAttachment.deleted.is_(False),
    )).all()
    for row in rows:
        row.deleted = True; row.status = "deleted"
    db.commit()
    return len(rows)
