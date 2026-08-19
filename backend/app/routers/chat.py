"""Chat 会话接口（Sprint 4）：会话历史只读 + 最近会话恢复 + 会话列表。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..services.session import history, latest_session, list_sessions, soft_delete

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/sessions/{session_id}/messages", response_model=list[schemas.ChatMessageOut])
def list_messages(session_id: str, db: Session = Depends(get_db)):
    rows = history(db, session_id)
    if not rows:
        raise HTTPException(status_code=404, detail="会话不存在或暂无消息")
    return rows


@router.delete("/sessions/{session_id}", status_code=204)
def delete_session(session_id: str, db: Session = Depends(get_db)):
    """软删除会话：仅标记隐藏，数据保留供管理回查。"""
    if not soft_delete(db, session_id):
        raise HTTPException(status_code=404, detail="会话不存在")


@router.get("/employees/{employee_no}/latest")
def latest(employee_no: str, db: Session = Depends(get_db)):
    """取某数字员工最近一次会话及其消息，用于退出后恢复对话。"""
    session = latest_session(db, employee_no)
    if session is None:
        return {"session_id": None, "messages": []}
    return {"session_id": session.session_id, "messages": history(db, session.session_id)}


@router.get("/employees/{employee_no}/sessions", response_model=list[schemas.ChatSessionOut])
def list_employee_sessions(employee_no: str, db: Session = Depends(get_db)):
    """列出某数字员工的所有会话（标题 + 时间，用于会话记录面板）。"""
    return list_sessions(db, employee_no)
