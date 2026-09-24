"""SkillHub 技能包的转换与导入。

SkillHub 的包结构与平台 ``validate_zip`` 的约定不一致，必须转换而不是直传：

* SkillHub：顶层是 ``<slug>/`` 目录，内含 ``<slug>/SKILL.md``、``<slug>/scripts/**``；
* 平台：要求**根目录**同时存在 ``plugin.yaml`` 与 ``SKILL.md``。

因此转换器做三件事：解包 → 定位 ``<slug>/SKILL.md`` → 打平到根目录并保留
``scripts/**`` 等资源 → 由 SKILL.md 的 front-matter 生成 ``plugin.yaml``。

导入链路复用既有唯一落库口径：``submit_zip`` → 审核通过 → ``publish_version``，
即 ``Plugin(plugin_type='skill')`` + 已发布 ``PluginVersion.manifest['instruction']``，
不新造第二套技能事实来源。导入结果写 ``skillhub_import`` 表做幂等与留痕。
"""

from __future__ import annotations

import hashlib
import io
import logging
import posixpath
import threading
import zipfile
from datetime import datetime
from pathlib import PurePosixPath
from typing import Any
from uuid import uuid4

import yaml
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import skill_intro, skillhub_client
from .llm import LLMProvider
from .plugin_governance import MAX_UNPACKED, MAX_ZIP, publish_version, submit_zip

logger = logging.getLogger(__name__)

# 打平后仍保留的文件数上限：与 validate_zip 的 MAX_FILES 保持一致口径（脚本类技能条目偏多）。
MAX_CONVERTED_FILES = 500
_SKIP_PREFIXES = ("__MACOSX/",)
_SKIP_NAMES = {".DS_Store", "Thumbs.db"}
# 平台 skill manifest 禁止出现的键（网络/凭据），生成 plugin.yaml 时必须避开。
_FORBIDDEN_MANIFEST_KEYS = {"endpoint", "endpoint_ref", "url", "network", "secret_ref"}


class SkillHubImportError(RuntimeError):
    """技能包无法转换（结构不符、超限、非 zip 等）。"""


def _read_member(archive: zipfile.ZipFile, name: str) -> bytes:
    try:
        return archive.read(name)
    except KeyError as exc:  # pragma: no cover - 由调用方先行校验存在性
        raise SkillHubImportError(f"技能包缺少文件：{name}") from exc


def split_front_matter(text: str) -> tuple[dict, str]:
    """拆分 SKILL.md 的 YAML front-matter；无 front-matter 时返回空元信息。"""
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    end = None
    for index in range(1, len(lines)):
        if lines[index].strip() in ("---", "..."):
            end = index
            break
    if end is None:
        return {}, text
    try:
        meta = yaml.safe_load("\n".join(lines[1:end])) or {}
    except yaml.YAMLError:
        meta = {}
    if not isinstance(meta, dict):
        meta = {}
    return meta, "\n".join(lines[end + 1:])


def _first_heading(body: str) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip()
    return ""


def _first_paragraph(body: str, limit: int = 200) -> str:
    for line in body.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("---"):
            continue
        return stripped[:limit]
    return ""


def _normalized_mode(info: zipfile.ZipInfo) -> int:
    """保留包内权限位；缺失时给合理默认（.py 可执行，其余只读）。"""
    mode = (info.external_attr >> 16) & 0o777
    if mode:
        return mode
    return 0o755 if PurePosixPath(info.filename).suffix == ".py" else 0o644


def _resolve_prefix(names: list[str], slug: str) -> tuple[str, list[str]]:
    """定位技能根目录（SKILL.md 所在目录）并返回 ``(前缀, 被丢弃的顶层条目)``。"""
    skill_docs = [name for name in names if PurePosixPath(name).name == "SKILL.md"]
    if not skill_docs:
        raise SkillHubImportError("技能包内未找到 SKILL.md")
    expected = f"{slug}/SKILL.md" if slug else ""
    chosen = expected if expected in skill_docs else min(
        skill_docs, key=lambda name: (name.count("/"), len(name)),
    )
    prefix = posixpath.dirname(chosen)
    if prefix:
        prefix = f"{prefix}/"
    outside = [
        name for name in names
        if not name.startswith(prefix) and name.split("/")[0] not in {prefix.rstrip("/")}
    ]
    return prefix, sorted(outside)


def convert_bundle(slug: str, raw_zip: bytes) -> tuple[bytes, dict]:
    """把 SkillHub 技能包转换成平台可接受的 skill zip。

    返回 ``(转换后 zip 字节, 元信息)``；元信息含 ``name``/``description``/
    ``file_count``/``instruction_chars``/``dropped``/``sha256``。
    """
    if not raw_zip:
        raise SkillHubImportError("技能包内容为空")
    if len(raw_zip) > MAX_ZIP:
        raise SkillHubImportError("技能包超过 20 MiB，无法导入")
    try:
        archive = zipfile.ZipFile(io.BytesIO(raw_zip))
    except zipfile.BadZipFile as exc:
        raise SkillHubImportError("技能包不是有效 ZIP") from exc

    with archive:
        entries: list[zipfile.ZipInfo] = []
        names: list[str] = []
        unpacked = 0
        for info in archive.infolist():
            path = PurePosixPath(info.filename.replace("\\", "/"))
            if path.is_absolute() or ".." in path.parts or not path.parts:
                raise SkillHubImportError(f"技能包包含不安全路径：{info.filename}")
            if info.is_dir():
                continue
            if path.name in _SKIP_NAMES or info.filename.startswith(_SKIP_PREFIXES):
                continue
            unpacked += info.file_size
            entries.append(info)
            names.append(path.as_posix())
        if not entries:
            raise SkillHubImportError("技能包内没有文件")
        if unpacked > MAX_UNPACKED:
            raise SkillHubImportError("技能包解压后超过 100 MiB，无法导入")

        prefix, dropped = _resolve_prefix(names, slug)
        skill_doc = f"{prefix}SKILL.md"
        try:
            skill_text = archive.read(skill_doc).decode("utf-8")
        except UnicodeDecodeError as exc:
            raise SkillHubImportError("SKILL.md 必须是 UTF-8 文本") from exc
        except KeyError as exc:
            raise SkillHubImportError(f"技能包缺少 {skill_doc}") from exc

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
            written = 0
            for info in entries:
                name = info.filename.replace("\\", "/")
                if not name.startswith(prefix):
                    continue
                relative = name[len(prefix):]
                if not relative:
                    continue
                if relative == "plugin.yaml":
                    # 上游若自带 plugin.yaml，一律丢弃：平台 manifest 由本转换器生成，
                    # 避免上游字段（endpoint/url 等）触发 skill 的禁止键校验。
                    continue
                if PurePosixPath(relative).name in _SKIP_NAMES:
                    continue
                written += 1
                if written > MAX_CONVERTED_FILES:
                    raise SkillHubImportError(f"技能包文件数超过 {MAX_CONVERTED_FILES} 个，无法导入")
                entry = zipfile.ZipInfo(relative, date_time=info.date_time)
                entry.external_attr = _normalized_mode(info) << 16
                entry.compress_type = zipfile.ZIP_DEFLATED
                target.writestr(entry, archive.read(info))
            if written == 0:
                raise SkillHubImportError("技能包内没有可用的技能文件")

        meta, body = split_front_matter(skill_text)
        name = str(meta.get("name") or "").strip() or _first_heading(body) or slug
        description = str(meta.get("description") or "").strip() or _first_paragraph(body)
        manifest = {
            "name": name[:120],
            "description": description[:500],
            "slug": slug,
            "source": "skillhub",
        }
        if any(key in manifest for key in _FORBIDDEN_MANIFEST_KEYS):  # pragma: no cover - 防御性
            raise SkillHubImportError("生成的 manifest 含有 skill 禁止声明的键")
        plugin_yaml = yaml.safe_dump(manifest, allow_unicode=True, sort_keys=False).encode("utf-8")
        converted = _repack_with_manifest(buffer.getvalue(), plugin_yaml)

    with zipfile.ZipFile(io.BytesIO(converted)) as result:
        file_count = len([name for name in result.namelist() if not name.endswith("/")])
    return converted, {
        "name": name[:120],
        "description": description[:500],
        "file_count": file_count,
        "instruction_chars": len(skill_text),
        "dropped": dropped,
        "sha256": hashlib.sha256(converted).hexdigest(),
    }


def _repack_with_manifest(flattened: bytes, plugin_yaml: bytes) -> bytes:
    """在打平后的 zip 里补上根目录 ``plugin.yaml``。"""
    source = zipfile.ZipFile(io.BytesIO(flattened))
    buffer = io.BytesIO()
    with source, zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as target:
        for info in source.infolist():
            entry = zipfile.ZipInfo(info.filename, date_time=info.date_time)
            entry.external_attr = info.external_attr
            entry.compress_type = zipfile.ZIP_DEFLATED
            target.writestr(entry, source.read(info))
        manifest_entry = zipfile.ZipInfo("plugin.yaml")
        manifest_entry.external_attr = 0o644 << 16
        manifest_entry.compress_type = zipfile.ZIP_DEFLATED
        target.writestr(manifest_entry, plugin_yaml)
    return buffer.getvalue()


def record_failure(
    db: Session, *, slug: str, actor: str, message: str, version: str = "", source: str = "",
    batch_id: str | None = None,
) -> dict:
    """把「还没拿到目录项就失败」的情况也留痕（管理端与 CLI 共用）。"""
    skill = skillhub_client.SkillHubSkill(
        slug=slug, display_name=slug, summary="", version=version, source=source,
    )
    return record_out(_record(
        db, skill=skill, actor=actor, status="failed", message=message, batch_id=batch_id,
    ))


def import_slug(
    db: Session, *, slug: str, actor: str, generate_intro: bool = True,
    batch_id: str | None = None, intro_provider: LLMProvider | None = None,
) -> dict:
    """按 slug 导入（先取详情再导入）；任何失败都落成 ``status='failed'`` 的记录。

    管理端批量导入与 ``tools/skillhub_sync.py`` 都走这里，保证两条入口的幂等
    判据、留痕结构与错误口径完全一致。
    """
    try:
        skill = skillhub_client.get_skill(slug)
    except skillhub_client.SkillHubError as exc:
        return record_failure(db, slug=slug, actor=actor, message=str(exc), batch_id=batch_id)
    return import_skill(
        db, skill=skill, actor=actor, generate_intro=generate_intro,
        batch_id=batch_id, intro_provider=intro_provider,
    )


def list_imports(
    db: Session, *, status: str | None = None, limit: int = 100,
) -> list[models.SkillHubImport]:
    """导入记录（最新在前）。"""
    query = select(models.SkillHubImport)
    if status:
        query = query.where(models.SkillHubImport.status == status)
    query = query.order_by(models.SkillHubImport.id.desc()).limit(max(1, min(500, limit)))
    return list(db.scalars(query).all())


def _previous_import(db: Session, slug: str) -> models.SkillHubImport | None:
    """同一 slug 的最近一次成功导入（用于续版本与幂等判定）。"""
    return db.scalar(select(models.SkillHubImport).where(
        models.SkillHubImport.slug == slug,
        models.SkillHubImport.status == "imported",
    ).order_by(models.SkillHubImport.id.desc()))


def _record(
    db: Session, *, skill: skillhub_client.SkillHubSkill, actor: str, status: str,
    message: str = "", plugin_id: str | None = None, sha256: str = "",
    file_count: int = 0, instruction_chars: int = 0, row: models.SkillHubImport | None = None,
    batch_id: str | None = None,
) -> models.SkillHubImport:
    record = row or models.SkillHubImport(slug=skill.slug, version=skill.version, source=skill.source)
    record.version = skill.version
    record.source = skill.source
    record.display_name = skill.display_name
    record.category = skill.category
    record.batch_id = batch_id or record.batch_id
    record.plugin_id = plugin_id or record.plugin_id
    record.sha256 = sha256 or record.sha256
    record.file_count = file_count or record.file_count
    record.instruction_chars = instruction_chars or record.instruction_chars
    record.status = status
    record.message = message[:500]
    record.imported_by = actor
    record.updated_at = datetime.now()
    if row is None:
        db.add(record)
    db.commit()
    db.refresh(record)
    return record


def import_skill(
    db: Session, *, skill: skillhub_client.SkillHubSkill, actor: str,
    generate_intro: bool = True, batch_id: str | None = None,
    intro_provider: LLMProvider | None = None,
) -> dict:
    """导入一个 SkillHub 技能：下载 → 转换 → 入库 → 发布 → 按需补中文简介。

    幂等：同一 ``(slug, version)`` 已成功导入时直接跳过；内容 sha256 与历史某次
    导入一致时也跳过（远端同版本重新打包）。失败不抛异常，写入 ``status='failed'``
    与原因，便于批量导入逐条汇报。

    ``generate_intro`` 为真且简介不含中文或超过 200 字时，调模型补一版中文简介；
    模型不可用会降级为原文首句，**不影响导入成功**。
    """
    previous = _previous_import(db, skill.slug)
    if previous is not None and previous.version == skill.version:
        # 新增一条 skipped 记录而不是改写原记录：原记录的 status 是幂等判据本身。
        return record_out(_record(
            db, skill=skill, actor=actor, status="skipped",
            message=f"该版本已导入（{previous.plugin_id or '—'}）",
            plugin_id=previous.plugin_id, batch_id=batch_id,
        ))
    if skill.download_disabled:
        return record_out(_record(
            db, skill=skill, actor=actor, status="failed", message="SkillHub 标记该技能不可下载",
            batch_id=batch_id,
        ))
    if not skill.version:
        return record_out(_record(
            db, skill=skill, actor=actor, status="failed", message="目录项缺少版本号，无法下载",
            batch_id=batch_id,
        ))

    record = _record(db, skill=skill, actor=actor, status="pending", message="下载中", batch_id=batch_id)
    try:
        raw = skillhub_client.download_bundle(
            slug=skill.slug, version=skill.version, source=skill.source,
        )
        converted, meta = convert_bundle(skill.slug, raw)
        duplicate = db.scalar(select(models.SkillHubImport).where(
            models.SkillHubImport.sha256 == meta["sha256"],
            models.SkillHubImport.status == "imported",
        ))
        if duplicate is not None:
            return record_out(_record(
                db, skill=skill, actor=actor, status="skipped",
                message=f"内容与已导入的 {duplicate.slug}@{duplicate.version} 相同",
                row=record, plugin_id=duplicate.plugin_id, sha256=meta["sha256"],
            ))
        # 续版本时必须沿用首次导入者身份，否则会撞上 submit_zip 的「只能为自己提交过的插件续版本」。
        submitter = (previous.imported_by if previous is not None else actor) or actor
        plugin_id = previous.plugin_id if previous is not None else None
        # 远端 category 恒为 null，分类实际来自 tags ∩ 词表；显式给了分类就用显式的。
        category = skill.category or skillhub_client.skill_category_from_tags(skill.tags)
        version_row = submit_zip(
            db, data=converted, filename=f"{skill.slug}-{skill.version}.zip",
            name=meta["name"], plugin_type="skill", scope="shared",
            category=category or None,
            deployment_mode="instruction", data_level="L1", version=skill.version,
            submitter=submitter, target_agent_id=None, plugin_id=plugin_id,
            featured=skillhub_client.is_featured_tags(skill.tags),
        )
        version_row.review_status = "approved"
        version_row.reviewed_by = actor
        version_row.reviewed_at = datetime.now()
        version_row.review_note = "SkillHub 自动导入"
        db.commit()
        publish_version(db, version_row, actor)
    except (skillhub_client.SkillHubError, SkillHubImportError) as exc:
        return record_out(_record(
            db, skill=skill, actor=actor, status="failed", message=str(exc), row=record,
        ))
    except HTTPException as exc:
        detail = exc.detail if isinstance(exc.detail, str) else str(exc.detail)
        return record_out(_record(
            db, skill=skill, actor=actor, status="failed", message=f"平台校验未通过：{detail}", row=record,
        ))

    intro_note = _maybe_generate_intro(
        db, skill=skill, plugin_id=version_row.plugin_id, version_row=version_row,
        enabled=generate_intro, provider=intro_provider,
    )
    _write_import_audit(db, plugin_id=version_row.plugin_id, actor=actor, slug=skill.slug, version=skill.version)
    return record_out(_record(
        db, skill=skill, actor=actor, status="imported",
        message=f"导入成功{intro_note}", plugin_id=version_row.plugin_id, sha256=meta["sha256"],
        file_count=meta["file_count"], instruction_chars=meta["instruction_chars"], row=record,
    ))


def _maybe_generate_intro(
    db: Session, *, skill: skillhub_client.SkillHubSkill, plugin_id: str,
    version_row: models.PluginVersion, enabled: bool, provider: LLMProvider | None,
) -> str:
    """按需补中文简介；返回追加在导入结果后面的一句说明。

    只处理「不含中文或超过 200 字」的简介，中文短简介不浪费模型额度。
    生成失败只降级、不抛异常 —— 简介是体验优化，不能把导入判成失败。
    """
    if not enabled:
        return ""
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None or not skill_intro.needs_intro(plugin.description):
        return ""
    draft = skill_intro.generate_draft(
        name=plugin.name, slug=skill.slug, summary=skill.summary,
        department=skill.department, tags=skill.tags,
        instruction=str((version_row.manifest or {}).get("instruction") or ""),
        provider=provider,
    )
    skill_intro.apply_intro(plugin, draft)
    db.commit()
    return "，已补中文简介" if draft.get("generated") else "，简介回落原文首句"


def _write_import_audit(db: Session, *, plugin_id: str, actor: str, slug: str, version: str) -> None:
    from .gateway import write_audit

    write_audit(
        db, trace_id=f"SKILLHUB-{slug}", employee_id=actor, plugin_id=plugin_id,
        action="skillhub_import", decision="allow",
        reason=f"SkillHub 技能导入 {slug}@{version}", result_summary="publish_status=published",
    )


def record_out(row: models.SkillHubImport) -> dict:
    """导入记录的对外结构（管理端与 CLI 共用）。"""
    return {
        "id": row.id,
        "batch_id": row.batch_id,
        "slug": row.slug,
        "version": row.version,
        "source": row.source,
        "display_name": row.display_name,
        "category": row.category,
        "plugin_id": row.plugin_id,
        "sha256": row.sha256,
        "status": row.status,
        "message": row.message,
        "file_count": row.file_count,
        "instruction_chars": row.instruction_chars,
        "imported_by": row.imported_by,
        "created_at": row.created_at,
        "updated_at": row.updated_at,
    }


def imported_slugs(db: Session) -> dict[str, dict]:
    """已成功导入的 ``{slug: {plugin_id, version}}``，供管理端货架打「已导入」角标。"""
    result: dict[str, dict] = {}
    for row in db.scalars(select(models.SkillHubImport).where(
        models.SkillHubImport.status == "imported",
    ).order_by(models.SkillHubImport.id)).all():
        result[row.slug] = {"plugin_id": row.plugin_id, "version": row.version}
    return result


# ---------------------------------------------------------------------------
# 批量导入批次：同步逐条导入会被网关的 300s 读超时掐断（131 条下载+转换就要数分钟），
# 因此管理端改成「立即返回 batch_id + 后台串行处理 + 前端轮询进度」。
# ---------------------------------------------------------------------------

TERMINAL_STATUSES = frozenset({"imported", "skipped", "failed"})
_BATCH_LOCK = threading.Lock()
# 批次只存内存：进程重启后任务本身也中断了，此时按库里已有记录尽力汇报（status=completed）。
_BATCHES: dict[str, dict[str, Any]] = {}


def new_batch_id() -> str:
    return f"SHB-{uuid4().hex[:12].upper()}"


def _batch_open(batch_id: str, total: int) -> None:
    with _BATCH_LOCK:
        _BATCHES[batch_id] = {
            "total": max(0, int(total)),
            "status": "running",
            "started_at": datetime.now().isoformat(timespec="seconds"),
        }


def _batch_close(batch_id: str) -> None:
    with _BATCH_LOCK:
        entry = _BATCHES.get(batch_id)
        if entry is not None:
            entry["status"] = "completed"
            entry["finished_at"] = datetime.now().isoformat(timespec="seconds")


def run_batch(
    slugs: list[str], *, actor: str, batch_id: str, generate_intro: bool = True,
    intro_provider: LLMProvider | None = None,
) -> None:
    """后台任务入口：串行导入一批技能。

    **自己开 SessionLocal**，不复用请求 session（请求已结束、session 已关闭）。
    单条异常只记一条 failed，不中断整批。
    """
    from ..database import SessionLocal

    db = SessionLocal()
    try:
        for slug in slugs:
            try:
                import_slug(
                    db, slug=slug, actor=actor, generate_intro=generate_intro,
                    batch_id=batch_id, intro_provider=intro_provider,
                )
            except Exception:  # noqa: BLE001 - 单条异常不能拖垮整批
                logger.exception("SkillHub 批量导入单条异常：slug=%s batch=%s", slug, batch_id)
                db.rollback()
                try:
                    record_failure(
                        db, slug=slug, actor=actor, message="导入异常，请查看服务日志",
                        batch_id=batch_id,
                    )
                except Exception:  # noqa: BLE001 - 留痕失败只能记日志
                    logger.exception("SkillHub 导入失败留痕异常：slug=%s", slug)
    finally:
        db.close()
        _batch_close(batch_id)


def batch_progress(db: Session, batch_id: str) -> dict:
    """批次进度：状态 + 已完成条数 + 成功/跳过/失败汇总 + 逐条明细。"""
    rows = list(db.scalars(select(models.SkillHubImport).where(
        models.SkillHubImport.batch_id == batch_id,
    ).order_by(models.SkillHubImport.id)))
    summary = {"imported": 0, "skipped": 0, "failed": 0}
    for row in rows:
        if row.status in summary:
            summary[row.status] += 1
    with _BATCH_LOCK:
        entry = _BATCHES.get(batch_id)
        entry = dict(entry) if entry else None
    if entry is None:
        # 进程重启后批次登记已丢：按库里已有记录尽力汇报，避免前端一直转圈。
        return {
            "batch_id": batch_id,
            "status": "completed" if rows else "unknown",
            "total": len(rows),
            "done": sum(1 for row in rows if row.status in TERMINAL_STATUSES),
            "summary": summary,
            "results": [record_out(row) for row in rows],
        }
    return {
        "batch_id": batch_id,
        "status": entry["status"],
        "total": entry["total"],
        "done": sum(1 for row in rows if row.status in TERMINAL_STATUSES),
        "summary": summary,
        "results": [record_out(row) for row in rows],
    }


def start_batch(slugs: list[str]) -> str:
    """登记批次并返回 ``batch_id``；**不**在这里跑任务（由调用方交给后台任务）。"""
    batch_id = new_batch_id()
    _batch_open(batch_id, len(slugs))
    return batch_id
