"""把 AI员工在工作区写下的产物（核对表、导出数据）变成前端可预览、可下载的文件。

为什么需要这一层：模型在答复里只会说一句「完整核对表已保存至
``/workspace/output/核对表.md``」。用户看不到、也拿不到那个文件 —— 而它才是这一轮
真正要交付的东西（答复只是摘要）。平台必须把它取出来、快照下来、给一个 URL。

## 为什么要快照，不能按需实时读工作区

工作区是按**员工**共享的（``harness/<EMP>/workspace`` 绑定到容器 ``/workspace``），
同一个 AI 员工跑第二次审计会直接覆盖 ``output/核对表.md``。如果预览时实时读工作区，
用户在旧会话里点开会看到新一轮的内容 —— 对审计场景这是**结论错位**，比看不到更糟。
所以每轮执行结束时把产物复制到 ``run_output_root()`` 下，之后一律只读快照。

## 只暴露「结论类」文本产物

- 后缀白名单：``.md/.markdown/.html/.htm/.txt/.csv``。**不含 ``.json``** ——
  ``findings.json`` 是工具之间的交换格式，几十 KB，贴给业务同事没有意义，
  还会把整份规则表带出来。
- 不读平台自己写的东西（``skills/`` 投放的技能包、``materials/`` 物化的材料）。
- 只读**本轮动过**的文件（按 mtime 过滤），避免把上一轮的旧表当成本轮产物。
"""

from __future__ import annotations

import hashlib
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models
from . import workspace_readback

logger = logging.getLogger(__name__)

# 后缀 → (可预览类型, MIME)。kind 是前端判定的唯一来源。
SUFFIX_KINDS: dict[str, tuple[str, str]] = {
    ".md": ("markdown", "text/markdown; charset=utf-8"),
    ".markdown": ("markdown", "text/markdown; charset=utf-8"),
    ".html": ("html", "text/html; charset=utf-8"),
    ".htm": ("html", "text/html; charset=utf-8"),
    ".csv": ("csv", "text/csv; charset=utf-8"),
    ".txt": ("text", "text/plain; charset=utf-8"),
}

# 每轮最多快照这么多份、单份最大 32 MiB（产物是文本，超过这个量级一定出了问题）
MAX_ARTIFACTS = 20
MAX_ARTIFACT_BYTES = 32 * 1024 * 1024


def run_output_root() -> Path:
    """产物快照根目录。Compose 覆盖为 ``/data/run-outputs``。"""
    configured = os.getenv("DWP_RUN_OUTPUT_ROOT", "").strip()
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "run-outputs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _safe_filename(name: str) -> str:
    cleaned = Path((name or "").replace("\\", "/")).name.strip()
    if not cleaned or cleaned in {".", ".."}:
        return "artifact"
    return cleaned[:200]


def kind_of(filename: str) -> tuple[str, str]:
    """文件名 → (可预览类型, MIME)。未在白名单里的按纯文本处理。"""
    suffix = Path(filename or "").suffix.lower()
    return SUFFIX_KINDS.get(suffix, ("text", "text/plain; charset=utf-8"))


def previewable(kind: str) -> bool:
    """四种类型都能在浏览器里预览（markdown/csv/text 渲染文本，html 走 iframe）。"""
    return kind in {"markdown", "html", "csv", "text"}


def discover(employee_id: str, *, since: float | None = None,
             limit: int = MAX_ARTIFACTS) -> list[Path]:
    """列出本轮模型在工作区写下的产物（绝对路径，按修改时间从新到旧）。

    复用 ``workspace_readback`` 的三条排除口径（平台目录、隐藏目录、mtime 下界），
    只把后缀白名单换成「含 HTML、不含 JSON」。
    """
    root = workspace_readback.workspace_dir(employee_id)
    if not root.is_dir():
        return []
    cutoff = (since - workspace_readback._MTIME_SLACK_SECONDS) if since else None
    found: list[tuple[float, Path]] = []
    for path in root.rglob("*"):
        try:
            if not path.is_file():
                continue
            relative = path.relative_to(root)
        except OSError:
            continue
        if any(part.startswith(".") for part in relative.parts):
            continue
        if relative.parts and relative.parts[0] in workspace_readback.PLATFORM_DIRS:
            continue
        if path.suffix.lower() not in SUFFIX_KINDS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if cutoff is not None and stat.st_mtime < cutoff:
            continue
        if stat.st_size <= 0 or stat.st_size > MAX_ARTIFACT_BYTES:
            continue
        found.append((stat.st_mtime, path))
    found.sort(key=lambda item: item[0], reverse=True)
    return [path for _mtime, path in found[:limit]]


def _relative_name(employee_id: str, path: Path) -> str:
    root = workspace_readback.workspace_dir(employee_id)
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.name


def snapshot_run(
    db: Session, execution: models.AgentExecution, employee_id: str, *,
    since: float | None = None,
) -> list[models.ExecutionArtifact]:
    """把本轮工作区产物快照到独立目录并落库。幂等：同一执行重复调用不会重复入库。

    返回本轮新入库（或已存在）的产物行。任何异常都不应阻断执行链路 —— 调用方负责
    try/except，这里只保证单份失败不影响其余。
    """
    paths = discover(employee_id, since=since)
    if not paths:
        return []
    existing = {
        row.name: row for row in db.scalars(
            select(models.ExecutionArtifact).where(
                models.ExecutionArtifact.execution_id == execution.id,
            )
        ).all()
    }
    saved: list[models.ExecutionArtifact] = []
    for path in paths:
        name = _relative_name(employee_id, path)
        try:
            data = path.read_bytes()
        except OSError as exc:
            logger.warning("读取产物失败 execution=%s name=%s: %s", execution.id, name, exc)
            continue
        digest = hashlib.sha256(data).hexdigest()
        row = existing.get(name)
        if row is not None and row.sha256 == digest:
            saved.append(row)
            continue
        filename = _safe_filename(path.name)
        kind, mime_type = kind_of(filename)
        artifact_id = row.id if row is not None else "ART-%s" % uuid4().hex[:16].upper()
        relative = Path(execution.conversation_id) / execution.id / artifact_id / filename
        target = run_output_root() / relative
        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            # 临时文件 + os.replace：并发读取时不会拿到半截文件
            tmp = target.with_name("%s.%d.tmp" % (target.name, os.getpid()))
            tmp.write_bytes(data)
            os.replace(tmp, target)
        except OSError as exc:
            logger.warning("写入产物失败 execution=%s name=%s: %s", execution.id, name, exc)
            continue
        if row is None:
            row = models.ExecutionArtifact(
                id=artifact_id, execution_id=execution.id,
                conversation_id=execution.conversation_id, employee_id=employee_id,
                created_at=datetime.now(),
            )
            db.add(row)
            existing[name] = row
        row.name = name
        row.filename = filename
        row.suffix = path.suffix.lower()
        row.mime_type = mime_type
        row.kind = kind
        row.size = len(data)
        row.sha256 = digest
        row.storage_path = relative.as_posix()
        saved.append(row)
    if saved:
        db.commit()
    return saved


def grouped_by_execution(
    db: Session, execution_ids: list[str],
) -> dict[str, list[models.ExecutionArtifact]]:
    """按执行 id 取产物，供 ``/runs/history`` 一次性带回（避免前端多一次请求）。"""
    grouped: dict[str, list[models.ExecutionArtifact]] = {item: [] for item in execution_ids}
    if not execution_ids:
        return grouped
    rows = db.scalars(
        select(models.ExecutionArtifact)
        .where(models.ExecutionArtifact.execution_id.in_(execution_ids))
        .order_by(models.ExecutionArtifact.created_at, models.ExecutionArtifact.name)
    ).all()
    for row in rows:
        grouped.setdefault(row.execution_id, []).append(row)
    return grouped


def for_conversation(db: Session, conversation_id: str) -> list[models.ExecutionArtifact]:
    return list(db.scalars(
        select(models.ExecutionArtifact)
        .where(models.ExecutionArtifact.conversation_id == conversation_id)
        .order_by(models.ExecutionArtifact.created_at, models.ExecutionArtifact.name)
    ).all())


def resolve_path(row: models.ExecutionArtifact) -> Path:
    """快照文件的绝对路径。resolve 后必须仍落在根目录内（防路径篡改）。"""
    root = run_output_root().resolve()
    target = (root / (row.storage_path or "")).resolve()
    if target == root or root not in target.parents:
        raise FileNotFoundError("产物存储路径越界")
    return target


def payload(row: models.ExecutionArtifact) -> dict:
    """给事件 payload 与前端用的精简结构。"""
    return {
        "artifact_id": row.id,
        "execution_id": row.execution_id,
        "name": row.name,
        "filename": row.filename,
        "kind": row.kind,
        "mime_type": row.mime_type,
        "size": row.size,
    }


def delete_for_conversation(db: Session, conversation_id: str) -> None:
    """清空会话时删掉产物行与快照文件（在 DB 提交之后调用，失败只残留孤儿文件）。"""
    rows = for_conversation(db, conversation_id)
    paths = []
    for row in rows:
        try:
            paths.append(resolve_path(row))
        except FileNotFoundError:
            continue
    db.execute(delete(models.ExecutionArtifact).where(
        models.ExecutionArtifact.conversation_id == conversation_id,
    ))
    db.commit()
    for path in paths:
        try:
            path.unlink()
        except OSError:
            continue
    # 目录按 <conversation>/<execution>/<artifact>/ 分层，逐层上删空目录
    root = run_output_root()
    for directory in sorted({path.parent for path in paths}, key=lambda p: -len(p.parts)):
        node = directory
        for _ in range(3):
            try:
                node.rmdir()
            except OSError:
                break
            if node == root:
                break
            node = node.parent
    _prune_empty(root)


def _prune_empty(root: Path) -> None:
    """递归删掉根目录下的空目录（只删空目录，不动文件）。"""
    try:
        for path in sorted(root.rglob("*"), key=lambda p: -len(p.parts)):
            if path.is_dir():
                try:
                    path.rmdir()
                except OSError:
                    continue
    except OSError:
        return


def purge_orphans(db: Session) -> int:
    """清理没有 DB 行指向的快照目录（会话行被硬删等极端情况）。返回清理条数。"""
    known = {
        (row.storage_path or "").split("/")[0]
        for row in db.scalars(select(models.ExecutionArtifact)).all()
    }
    removed = 0
    root = run_output_root()
    for child in root.iterdir():
        if not child.is_dir() or child.name in known:
            continue
        shutil.rmtree(child, ignore_errors=True)
        removed += 1
    return removed


__all__ = [
    "SUFFIX_KINDS", "MAX_ARTIFACTS", "run_output_root", "kind_of", "previewable",
    "discover", "snapshot_run", "grouped_by_execution", "for_conversation",
    "resolve_path", "payload", "delete_for_conversation", "purge_orphans",
]
