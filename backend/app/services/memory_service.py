"""记忆写入服务（记忆插件）。

提供把业务事件自动写入记忆的辅助函数，供 Chat / Team 等模块调用。
记忆插件保持"松耦合"：其他模块只调用这里的函数，不直接操作 MemoryEntry 表。
"""

from sqlalchemy.orm import Session

from .. import models


def record_conversation(
    db: Session,
    *,
    human_no: str,
    employee_no: str,
    content: str,
    trace_id: str | None = None,
) -> models.MemoryEntry:
    """把一次对话写入记忆。

    - 主体：human（用户）
    - 类型：conversation（对话）
    - 关联对方：employee_no（和哪个数字员工聊的）
    - 可见性：personal（个人）
    """
    entry = models.MemoryEntry(
        subject_type="human",
        subject_no=human_no,
        kind="conversation",
        content=content,
        content_type="text",
        related_subject_no=employee_no,
        trace_id=trace_id,
        visibility="personal",
        data_level="L2",
        lifecycle="active",
    )
    db.add(entry)
    db.commit()
    db.refresh(entry)
    return entry
