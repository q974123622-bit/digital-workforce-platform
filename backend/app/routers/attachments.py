"""Conversation attachment upload, processing and lifecycle endpoints."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import attachment_preview, attachments
from ..services.auth import current_account

router = APIRouter(prefix="/conversations/{conversation_id}/attachments", tags=["attachments"])


def _conversation(db: Session, conversation_id: str, account: models.Account) -> models.Conversation:
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None or conversation.hidden_at is not None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conversation.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权访问该会话附件")
    return conversation


def _attachment(
    db: Session, conversation: models.Conversation, attachment_id: str,
) -> models.ConversationAttachment:
    row = db.get(models.ConversationAttachment, attachment_id)
    if row is None or row.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="附件不存在")
    return row


def _pending_job_ids(
    db: Session, rows: list[models.ConversationAttachment],
) -> list[str]:
    """挑出这些附件对应的待处理解析任务。"""
    job_ids: list[str] = []
    for row in rows:
        if row.status != "pending":
            continue
        job = db.scalar(select(models.AttachmentProcessingJob).where(
            models.AttachmentProcessingJob.attachment_id == row.id,
            models.AttachmentProcessingJob.status == "pending",
        ))
        if job is not None:
            job_ids.append(job.id)
    return job_ids


def _dispatch_jobs(background_tasks: BackgroundTasks, job_ids: list[str]) -> None:
    """把一次上传产生的全部解析任务合成一条批量任务派发。

    Starlette 的 ``BackgroundTasks`` 是顺序 ``await`` 的：逐个
    ``add_task(process_job, ...)`` 会让 N 份材料退化成 N 次串行视觉调用。
    合并成一条 ``process_jobs`` 后，服务层再用线程池按视觉层的全局信号量
    上限并发跑，并发上限只有一处（见 ``attachment_vision._max_inflight``）。
    """
    pending = [job_id for job_id in job_ids if job_id]
    if not pending:
        return
    background_tasks.add_task(attachments.process_jobs, pending)


@router.post("", response_model=schemas.ConversationAttachmentOut)
async def upload_attachment(
    conversation_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    conversation = _conversation(db, conversation_id, account)
    data = await file.read(attachments.MAX_FILE_SIZE + 1)
    row = attachments.create_attachment(
        db,
        conversation=conversation,
        owner_human_no=account.human_employee_no,
        filename=file.filename or "",
        mime_type=file.content_type or "application/octet-stream",
        data=data,
    )
    _dispatch_jobs(background_tasks, _pending_job_ids(db, [row]))
    return row


@router.post("/pack", response_model=list[schemas.ConversationAttachmentOut])
async def upload_material_pack(
    conversation_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """材料包上传：一个 zip 摊成多份附件，按包内目录/文件名自动分业务号。

    与单文件上传的区别：单次不限个数（受包配额约束），每份附件带 pack_id 与 business_no，
    发起执行时按业务号物化到目标 AI 员工的 workspace。
    """
    conversation = _conversation(db, conversation_id, account)
    max_files, max_total = attachments.pack_limits()
    data = await file.read(max_total + 1)
    if len(data) > max_total:
        raise HTTPException(status_code=413,
                            detail="材料包不能超过 %.0f MiB" % (max_total / 1024 / 1024))
    rows = attachments.create_pack_attachment(
        db,
        conversation=conversation,
        owner_human_no=account.human_employee_no,
        filename=file.filename or "",
        data=data,
    )
    _dispatch_jobs(background_tasks, _pending_job_ids(db, rows))
    return rows


@router.post("/pack/files", response_model=list[schemas.ConversationAttachmentOut])
async def upload_material_files(
    conversation_id: str,
    background_tasks: BackgroundTasks,
    files: list[UploadFile] = File(...),
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """一批文件直接上传（不打包）：与 /pack 只有入口不同，落库与物化完全同路。

    支持「一次多选一堆材料」这个最常见的用法 —— 不必先自己压成 zip，
    也不必为分笔去规范文件名/目录名。
    """
    conversation = _conversation(db, conversation_id, account)
    max_files, max_total = attachments.pack_limits()
    if len(files) > max_files:
        raise HTTPException(status_code=422, detail="单次最多上传 %d 份材料" % max_files)
    payloads: list[tuple[str, bytes]] = []
    total = 0
    for item in files:
        data = await item.read(attachments.MAX_FILE_SIZE + 1)
        if len(data) > attachments.MAX_FILE_SIZE:
            raise HTTPException(
                status_code=413,
                detail="材料 %s 超过单文件上限 20 MiB" % (item.filename or "未命名"))
        total += len(data)
        if total > max_total:
            raise HTTPException(status_code=413,
                                detail="材料总量不能超过 %.0f MiB" % (max_total / 1024 / 1024))
        payloads.append((item.filename or "", data))
    rows = attachments.create_pack_files_attachment(
        db,
        conversation=conversation,
        owner_human_no=account.human_employee_no,
        files=payloads,
    )
    _dispatch_jobs(background_tasks, _pending_job_ids(db, rows))
    return rows


@router.get("", response_model=list[schemas.ConversationAttachmentOut])
def list_attachments(
    conversation_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    conversation = _conversation(db, conversation_id, account)
    attachments.cleanup_orphans(db)
    return db.scalars(select(models.ConversationAttachment).where(
        models.ConversationAttachment.conversation_id == conversation.id,
        models.ConversationAttachment.generation == conversation.generation,
    ).order_by(models.ConversationAttachment.created_at)).all()


@router.get("/{attachment_id}", response_model=schemas.ConversationAttachmentOut)
def get_attachment(
    conversation_id: str,
    attachment_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    return _attachment(db, _conversation(db, conversation_id, account), attachment_id)


@router.get("/{attachment_id}/content")
def get_attachment_content(
    conversation_id: str,
    attachment_id: str,
    download: bool = False,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """回附件原文件字节（图片/PDF 预览与下载共用）。

    ``Content-Disposition`` 默认 ``inline``（浏览器内嵌打开，PDF 走 Range 请求）；
    ``?download=1`` 时改 ``attachment`` 触发另存。缓存走 ETag=sha256 + 私有缓存。
    """
    conversation = _conversation(db, conversation_id, account)
    row = _attachment(db, conversation, attachment_id)
    if row.deleted or row.generation != conversation.generation:
        raise HTTPException(status_code=404, detail="附件已失效")
    try:
        path = attachment_preview.resolve_storage_path(row)
    except attachment_preview.PreviewUnavailable as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="附件原文件不存在")
    return FileResponse(
        path,
        media_type=row.mime_type or "application/octet-stream",
        filename=row.filename,
        content_disposition_type="attachment" if download else "inline",
        headers={
            "Cache-Control": "private, max-age=3600",
            "ETag": '"%s"' % (row.sha256 or ""),
        },
    )


@router.get("/{attachment_id}/thumbnail")
def get_attachment_thumbnail(
    conversation_id: str,
    attachment_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """回缩略图 JPEG（长边 ≤480，EXIF 方向已纠正）。

    命中磁盘缓存直接回文件，未命中才渲染；非图片/PDF 一律 404（前端本就不会请求）。
    渲染失败（加密 PDF、损坏图片、缺 Pillow）转 422 + 中文原因，前端降级回文件卡片。
    """
    conversation = _conversation(db, conversation_id, account)
    row = _attachment(db, conversation, attachment_id)
    if row.deleted or row.generation != conversation.generation:
        raise HTTPException(status_code=404, detail="附件已失效")
    if attachment_preview.preview_kind(row.file_type) == "none":
        raise HTTPException(status_code=404, detail="该附件类型不支持预览")
    try:
        path = attachment_preview.render_thumbnail(row)
    except attachment_preview.PreviewUnavailable as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return FileResponse(
        path,
        media_type="image/jpeg",
        headers={
            "Cache-Control": "private, max-age=86400",
            "ETag": '"%s"' % attachment_preview.thumb_etag(row),
        },
    )


@router.delete("/{attachment_id}", status_code=204)
def delete_attachment(
    conversation_id: str,
    attachment_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    row = _attachment(db, _conversation(db, conversation_id, account), attachment_id)
    row.deleted = True
    row.status = "deleted"
    row.updated_at = datetime.now()
    db.add(row)
    db.commit()


@router.post("/{attachment_id}/retry", response_model=schemas.ConversationAttachmentOut)
def retry_attachment(
    conversation_id: str,
    attachment_id: str,
    background_tasks: BackgroundTasks,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    conversation = _conversation(db, conversation_id, account)
    row = _attachment(db, conversation, attachment_id)
    if row.deleted or row.generation != conversation.generation:
        raise HTTPException(status_code=409, detail="附件已失效")
    if row.status in {"conversion_required", "ocr_required"}:
        raise HTTPException(status_code=409, detail=row.processing_hint or "需要测试环境处理插件")
    if row.status not in {"failed"}:
        raise HTTPException(status_code=409, detail="当前附件不需要重试")
    job = models.AttachmentProcessingJob(
        id=f"APJ-{uuid4().hex[:16].upper()}", attachment_id=row.id,
    )
    row.status = "pending"
    row.error_code = ""
    row.processing_hint = None
    row.updated_at = datetime.now()
    db.add_all([row, job])
    db.commit()
    _dispatch_jobs(background_tasks, [job.id])
    return row
