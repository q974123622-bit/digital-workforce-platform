"""AI员工产物的预览与下载接口。

产物列表本身随 ``/runs/history`` 一起返回（挂在执行下），所以这里只提供**内容**接口：
模型在答复里说「核对表已保存至 /workspace/output/核对表.md」，用户点一下就能看到、
能下载 —— 而不是拿到一个容器内的路径。

两条路由都要走会话归属校验：产物是按员工共享工作区产出的，只有会话本人能读。
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import run_outputs
from ..services.auth import current_account

router = APIRouter(prefix="/conversations/{conversation_id}/artifacts", tags=["artifacts"])


def _conversation(db: Session, conversation_id: str, account: models.Account) -> models.Conversation:
    conv = db.get(models.Conversation, conversation_id)
    if conv is None:
        raise HTTPException(status_code=404, detail="会话不存在")
    if conv.owner_human_no != account.human_employee_no:
        raise HTTPException(status_code=403, detail="无权查看该会话")
    return conv


def _artifact(db: Session, conversation: models.Conversation,
              artifact_id: str) -> models.ExecutionArtifact:
    row = db.get(models.ExecutionArtifact, artifact_id)
    if row is None or row.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="产物不存在")
    return row


@router.get("", response_model=list[schemas.ExecutionArtifactOut])
def list_artifacts(
    conversation_id: str,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """会话内全部产物（按执行归属在前端分组；正常渲染走 /runs/history 即可）。"""
    conversation = _conversation(db, conversation_id, account)
    return run_outputs.for_conversation(db, conversation.id)


@router.get("/{artifact_id}/content")
def get_artifact_content(
    conversation_id: str,
    artifact_id: str,
    download: bool = False,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """回产物内容。

    默认 ``inline``：Markdown 直接读文本渲染，HTML 交给 iframe（前端加 sandbox，
    脚本一律不执行）；``?download=1`` 改 ``attachment`` 触发另存。
    ETag 用内容 sha256：同一份快照重复打开零成本。
    """
    conversation = _conversation(db, conversation_id, account)
    row = _artifact(db, conversation, artifact_id)
    try:
        path = run_outputs.resolve_path(row)
    except FileNotFoundError as exc:
        raise HTTPException(status_code=404, detail="产物文件不存在") from exc
    if not path.is_file():
        raise HTTPException(status_code=404, detail="产物文件不存在")
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
