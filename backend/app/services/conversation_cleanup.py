"""会话内容物理清除（「清空」与「删除会话卡片」共用）。

清除范围：消息、协作任务、执行轨迹（执行行 + 进度事件）、附件（附件行 +
解析片段 + 处理任务 + 磁盘文件）、会话记忆摘要。保留 conversation 行本身，
generation 自增作为防御性隔离。

顺序有隐式依赖，不要调整：
后台编排线程的写回路径是 ``_assert_execution_writable(...)`` 紧接
``_append(...)``，而 ``_append`` 内部**立即 commit**。若先删消息行、最后才抬
generation，一个已通过断言的线程可在 purge 之后把消息落库，留下幽灵行。
因此必须「先取消在途执行并单独提交 generation 自增」，再删各行：抬高
generation 让在途线程后续断言必然失败，随后删行可把窗口内已写入的行一并扫掉。

函数可重复调用（第二次执行时各表已无行，仍会安全自增 generation 并提交）。
"""

import shutil
from datetime import datetime
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from .. import models
from . import attachment_preview, attachments, execution_events, run_outputs


def _collect_attachment_ids(db: Session, conversation_id: str) -> list[str]:
    return list(db.scalars(
        select(models.ConversationAttachment.id).where(
            models.ConversationAttachment.conversation_id == conversation_id,
        )
    ).all())


def _collect_storage_paths(db: Session, conversation_id: str) -> list[str]:
    return [
        path for path in db.scalars(
            select(models.ConversationAttachment.storage_path).where(
                models.ConversationAttachment.conversation_id == conversation_id,
            )
        ).all() if path
    ]


def _purge_attachment_rows(db: Session, attachment_ids: list[str]) -> None:
    if not attachment_ids:
        return
    db.execute(delete(models.AttachmentChunk).where(
        models.AttachmentChunk.attachment_id.in_(attachment_ids),
    ))
    db.execute(delete(models.AttachmentProcessingJob).where(
        models.AttachmentProcessingJob.attachment_id.in_(attachment_ids),
    ))
    db.execute(delete(models.ConversationAttachment).where(
        models.ConversationAttachment.id.in_(attachment_ids),
    ))


def _remove_disk_files(conversation_id: str, owner_human_no: str, storage_paths: list[str]) -> None:
    """删除附件磁盘文件。

    在 DB 提交之后调用：最坏情况只残留孤儿文件（可清理），不会出现
    「行还在、文件没了」的坏状态。所有删除都限制在 attachment_root() 之内。
    """
    try:
        root = attachments.attachment_root().resolve()
    except Exception:  # noqa: BLE001 - 目录不可用时静默跳过
        return
    for relative in storage_paths:
        try:
            target = (root / relative).resolve()
            if target != root and root not in target.parents:
                continue  # 越界防护：storage_path 被篡改时不执行删除
            if target.is_file():
                target.unlink()
            attachment_dir = target.parent
            if attachment_dir != root and attachment_dir.is_dir():
                shutil.rmtree(attachment_dir, ignore_errors=True)
        except Exception:  # noqa: BLE001 - 单个文件失败不影响其余清理
            continue
    try:
        conv_dir = (root / owner_human_no / conversation_id).resolve()
        if conv_dir != root and root in conv_dir.parents and conv_dir.is_dir():
            shutil.rmtree(conv_dir, ignore_errors=True)
    except Exception:  # noqa: BLE001
        pass


def _remove_thumbs(attachment_ids: list[str]) -> None:
    """删除这些附件的缩略图缓存目录（``_thumbs/<attachment_id>/``）。

    缩略图放在独立根目录（见 ``attachment_preview``），不在附件目录内，所以要单独清；
    否则会残留孤儿文件。失败仅静默，最坏只多占一点磁盘。
    """
    if not attachment_ids:
        return
    try:
        root = (attachments.attachment_root() / attachment_preview.THUMB_DIRNAME).resolve()
    except Exception:  # noqa: BLE001 - 目录不可用时静默跳过
        return
    for attachment_id in attachment_ids:
        try:
            target = (root / attachment_id).resolve()
            if target != root and root in target.parents and target.is_dir():
                shutil.rmtree(target, ignore_errors=True)
        except Exception:  # noqa: BLE001 - 单个失败不影响其余清理
            continue


def purge_conversation_content(db: Session, conv: models.Conversation) -> None:
    """物理清除会话全部内容，保留 conversation 行并自增 generation。"""
    conversation_id = conv.id
    owner_human_no = conv.owner_human_no

    attachment_ids = _collect_attachment_ids(db, conversation_id)
    storage_paths = _collect_storage_paths(db, conversation_id)

    # 1. 取消在途执行；该函数内部自行 commit 并终止运行时，不要合并进本事务。
    execution_events.cancel_for_conversation(db, conversation_id)

    # 2. 立刻抬高 generation 并单独提交，让在途后台线程的写回断言必然失败。
    conv.generation += 1
    conv.updated_at = datetime.now()
    db.add(conv)
    db.commit()

    # 3. 附件：行 + 解析片段 + 处理任务。
    _purge_attachment_rows(db, attachment_ids)

    # 4. 执行轨迹：进度事件 + 执行行（先删事件，再删执行）。
    execution_ids = list(db.scalars(
        select(models.AgentExecution.id).where(
            models.AgentExecution.conversation_id == conversation_id,
        )
    ).all())
    if execution_ids:
        db.execute(delete(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id.in_(execution_ids),
        ))
    db.execute(delete(models.AgentExecution).where(
        models.AgentExecution.conversation_id == conversation_id,
    ))

    # 4b. AI员工产物快照（核对表等）：行 + 磁盘文件。文件清理失败只残留孤儿文件。
    run_outputs.delete_for_conversation(db, conversation_id)

    # 5. 消息、协作任务、会话记忆摘要。
    db.execute(delete(models.ConversationMessage).where(
        models.ConversationMessage.conversation_id == conversation_id,
    ))
    db.execute(delete(models.TaskRun).where(
        models.TaskRun.conversation_id == conversation_id,
    ))
    db.execute(delete(models.ConversationMemoryState).where(
        models.ConversationMemoryState.conversation_id == conversation_id,
    ))
    db.commit()

    # 6. 兜底：后台解析线程可能在窗口内把片段写回，提交后再扫一次。
    if attachment_ids:
        _purge_attachment_rows(db, attachment_ids)
        db.commit()

    # 7. 磁盘文件在 DB 提交之后清理，失败仅残留孤儿文件。
    _remove_disk_files(conversation_id, owner_human_no, storage_paths)

    # 8. 缩略图缓存在独立根目录，单独清（不在附件目录内，_remove_disk_files 覆盖不到）。
    _remove_thumbs(attachment_ids)
