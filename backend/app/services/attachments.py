"""Conversation-scoped document storage, parsing and bounded Harness access."""

from __future__ import annotations

import csv
import hashlib
import io
import os
import re
import zipfile
from datetime import datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pdfplumber
from docx import Document
from fastapi import HTTPException
from openpyxl import load_workbook
from openpyxl.utils.cell import range_boundaries
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from . import execution_events

MAX_FILE_SIZE = 20 * 1024 * 1024
MAX_FILES_PER_MESSAGE = 5
MAX_TOTAL_SIZE = 50 * 1024 * 1024
SUPPORTED = {"md", "markdown", "txt", "docx", "pdf", "xlsx", "csv", "doc", "xls"}


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


def _validate_signature(file_type: str, data: bytes) -> None:
    if not data:
        raise HTTPException(status_code=422, detail="不能上传空文件")
    if file_type == "pdf" and not data.startswith(b"%PDF"):
        raise HTTPException(status_code=422, detail="文件内容与 PDF 扩展名不符")
    if file_type in {"docx", "xlsx"} and not data.startswith(b"PK"):
        raise HTTPException(status_code=422, detail="文件内容与 Office 扩展名不符")
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
    _validate_signature(file_type, data)
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
        with pdfplumber.open(path) as pdf:
            for page_no, page in enumerate(pdf.pages, 1):
                chunks.extend(_split_text(page.extract_text() or "", f"第 {page_no} 页"))
            if not chunks:
                raise RuntimeError("OCR_REQUIRED")
            return chunks, len(pdf.pages), None
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
    for job_id in ids:
        process_job(job_id)


def bind_to_message(
    db: Session, *, conversation: models.Conversation, owner_human_no: str,
    message_seq: int, attachment_ids: list[str],
) -> list[models.ConversationAttachment]:
    if len(attachment_ids) > MAX_FILES_PER_MESSAGE or len(set(attachment_ids)) != len(attachment_ids):
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
    if sum(row.size for row in rows) > MAX_TOTAL_SIZE:
        raise HTTPException(status_code=413, detail="附件总量不能超过 50 MiB")
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
