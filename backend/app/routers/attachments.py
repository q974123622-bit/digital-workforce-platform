"""Conversation attachment upload, processing and lifecycle endpoints."""

from datetime import datetime
from uuid import uuid4

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import attachments
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
    if row.status == "pending":
        job = db.scalar(select(models.AttachmentProcessingJob).where(
            models.AttachmentProcessingJob.attachment_id == row.id,
            models.AttachmentProcessingJob.status == "pending",
        ))
        if job is not None:
            background_tasks.add_task(attachments.process_job, job.id)
    return row


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
    background_tasks.add_task(attachments.process_job, job.id)
    return row
