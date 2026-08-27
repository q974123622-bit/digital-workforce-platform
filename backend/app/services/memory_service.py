"""聊天长期记忆的内部服务契约。

工作线 A 负责实现本模块；工作线 B 只能调用其公开函数，
不直接读写 ``MemoryEntry``。
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session


@dataclass(frozen=True)
class MemoryHit:
    """一次本地记忆召回的最小结果。"""

    memory_id: int
    content: str
    created_at: datetime
    score: float
    kind: str


def capture_turn(
    db: Session,
    *,
    owner_employee_no: str,
    source_type: str,
    source_session_id: str,
    source_ref: str,
    user_text: str,
    assistant_text: str,
    trace_id: str | None = None,
) -> int | None:
    """保存一轮问答记忆；Task 3 实现幂等持久化。"""
    del db, owner_employee_no, source_type, source_session_id
    del source_ref, user_text, assistant_text, trace_id
    return None


def retrieve_for_prompt(
    db: Session,
    *,
    owner_employee_no: str,
    query: str,
    current_session_id: str,
    limit: int = 3,
    max_chars: int = 1200,
) -> list[MemoryHit]:
    """检索当前数字员工的相关旧记忆；Task 4 实现评分与预算。"""
    del db, owner_employee_no, query, current_session_id, limit, max_chars
    return []


def render_prompt_context(
    hits: list[MemoryHit], *, max_chars: int = 1200
) -> str:
    """生成供模型阅读的安全历史资料；Task 5 实现固定模板。"""
    del hits, max_chars
    return ""
