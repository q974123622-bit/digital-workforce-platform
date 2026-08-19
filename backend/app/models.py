from datetime import datetime

from sqlalchemy import JSON, DateTime, LargeBinary, String
from sqlalchemy.orm import Mapped, mapped_column

from .database import Base


class HumanEmployee(Base):
    __tablename__ = "human_employee"

    employee_no: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String, default="")
    employment_type: Mapped[str] = mapped_column(String)  # formal | intern
    status: Mapped[str] = mapped_column(String, default="active")


class DigitalEmployee(Base):
    __tablename__ = "digital_employee"

    employee_no: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # twin | virtual | rpa
    source_human_no: Mapped[str | None] = mapped_column(String, nullable=True)
    owner_human_no: Mapped[str] = mapped_column(String)
    department: Mapped[str] = mapped_column(String, default="")
    role_prompt: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    runtime_type: Mapped[str] = mapped_column(String, default="demo")
    runtime_ref: Mapped[str | None] = mapped_column(String, nullable=True)
    location: Mapped[str] = mapped_column(String, default="remote")
    internet: Mapped[str] = mapped_column(String, default="deny")
    max_data_level: Mapped[str] = mapped_column(String, default="L1")
    allowed_domains: Mapped[list] = mapped_column(JSON, default=list)


class Plugin(Base):
    __tablename__ = "plugin"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    type: Mapped[str] = mapped_column(String)  # knowledge | mcp | workflow | rpa | http
    endpoint_ref: Mapped[str] = mapped_column(String, default="mock://")
    data_level: Mapped[str] = mapped_column(String, default="L1")
    status: Mapped[str] = mapped_column(String, default="active")
    description: Mapped[str] = mapped_column(String, default="")


class EmployeePluginGrant(Base):
    __tablename__ = "employee_plugin_grant"

    id: Mapped[int] = mapped_column(primary_key=True)
    employee_id: Mapped[str] = mapped_column(String, index=True)
    plugin_id: Mapped[str] = mapped_column(String, index=True)
    action: Mapped[str] = mapped_column(String, default="read")
    decision_mode: Mapped[str] = mapped_column(String, default="allow")  # allow | deny | approval


class Policy(Base):
    __tablename__ = "policy"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    effect: Mapped[str] = mapped_column(String)  # allow | deny | approval
    description: Mapped[str] = mapped_column(String, default="")
    enabled: Mapped[bool] = mapped_column(default=True)
    priority: Mapped[int] = mapped_column(default=0)


class AuditEvent(Base):
    __tablename__ = "audit_event"

    id: Mapped[int] = mapped_column(primary_key=True)
    trace_id: Mapped[str] = mapped_column(String, index=True)
    ts: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    actor: Mapped[str] = mapped_column(String, default="")
    employee_id: Mapped[str | None] = mapped_column(String, nullable=True)
    team_id: Mapped[str | None] = mapped_column(String, nullable=True)
    plugin_id: Mapped[str | None] = mapped_column(String, nullable=True)
    knowledge_base_id: Mapped[str | None] = mapped_column(String, nullable=True)
    action: Mapped[str] = mapped_column(String, default="")
    decision: Mapped[str] = mapped_column(String, default="deny")
    reason: Mapped[str | None] = mapped_column(String, nullable=True)
    result_summary: Mapped[str | None] = mapped_column(String, nullable=True)


class Team(Base):
    __tablename__ = "team"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    leader_employee_id: Mapped[str] = mapped_column(String)
    description: Mapped[str] = mapped_column(String, default="")


class TeamMember(Base):
    __tablename__ = "team_member"

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[str] = mapped_column(String, index=True)
    employee_id: Mapped[str] = mapped_column(String)
    role: Mapped[str] = mapped_column(String, default="worker")


class KnowledgeBase(Base):
    __tablename__ = "knowledge_base"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    name: Mapped[str] = mapped_column(String)
    level: Mapped[str] = mapped_column(String, default="L1")
    data_level: Mapped[str] = mapped_column(String, default="L1")
    resource_type: Mapped[str] = mapped_column(String, default="knowledge")
    allowed_employment_type: Mapped[list] = mapped_column(JSON, default=list)
    department_scope: Mapped[list] = mapped_column(JSON, default=list)
    domain: Mapped[str] = mapped_column(String, default="")
    description: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="active")
    doc_path: Mapped[str | None] = mapped_column(String, nullable=True)


class KnowledgeChunk(Base):
    """RAG 索引块：kb_chunk 表（id / kb_id / source_file / title / content / embedding BLOB / dims / created_at）。"""

    __tablename__ = "kb_chunk"

    id: Mapped[int] = mapped_column(primary_key=True)
    kb_id: Mapped[str] = mapped_column(String, index=True)
    source_file: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")
    content: Mapped[str] = mapped_column(String, default="")
    embedding: Mapped[bytes | None] = mapped_column(LargeBinary, nullable=True)
    dims: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatSession(Base):
    __tablename__ = "chat_session"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String, unique=True, index=True)
    employee_id: Mapped[str] = mapped_column(String)
    trace_id: Mapped[str] = mapped_column(String, default="")
    title: Mapped[str] = mapped_column(String, default="")  # 会话标题（自动总结主题）
    deleted: Mapped[bool] = mapped_column(default=False)  # 软删除：前端隐藏，后台保留供管理回查
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class ChatMessage(Base):
    __tablename__ = "chat_message"

    id: Mapped[int] = mapped_column(primary_key=True)
    session_id: Mapped[str] = mapped_column(String, index=True)
    role: Mapped[str] = mapped_column(String)  # user | assistant | tool
    content: Mapped[str] = mapped_column(String, default="")
    tool_cards: Mapped[list] = mapped_column(JSON, default=list)


class TaskRun(Base):
    __tablename__ = "task_run"

    id: Mapped[str] = mapped_column(String, primary_key=True)
    team_id: Mapped[str] = mapped_column(String)
    trace_id: Mapped[str] = mapped_column(String, default="")
    request: Mapped[str] = mapped_column(String, default="")
    status: Mapped[str] = mapped_column(String, default="pending")
    subtasks: Mapped[list] = mapped_column(JSON, default=list)
    summary: Mapped[str] = mapped_column(String, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)


class MemoryEntry(Base):
    """记忆插件：一条长期记忆，带 7 组维度标签。

    - 主体（subject_type/subject_no）：这是谁的记忆
    - 类型（kind）：记的是什么
    - 内容（content/content_type）：文字还是结构化
    - 关联（related_subject_no/trace_id/file_ref）：和谁交互、溯源、附件
    - 可见性（visibility）：谁能看
    - 敏感等级（data_level）：多敏感
    - 生命周期（lifecycle）：近期完整还是已压缩摘要
    """

    __tablename__ = "memory_entry"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 主体
    subject_type: Mapped[str] = mapped_column(String, index=True)  # human | twin | virtual | team
    subject_no: Mapped[str] = mapped_column(String, index=True)  # E10281 / DT-E10281 / VE-0001 / TEAM-ONBOARD
    # 类型 + 内容
    kind: Mapped[str] = mapped_column(String, default="fact")  # conversation|decision|fact|attachment|summary|profile|basic_info
    content: Mapped[str] = mapped_column(String, default="")  # 文字 / 摘要 / structured JSON
    content_type: Mapped[str] = mapped_column(String, default="text")  # text | structured
    # 关联
    related_subject_no: Mapped[str | None] = mapped_column(String, nullable=True)  # 和谁交互产生
    trace_id: Mapped[str | None] = mapped_column(String, nullable=True)  # 关联审计/操作
    file_ref: Mapped[str | None] = mapped_column(String, nullable=True)  # 附件路径（kind=attachment）
    # 控制
    visibility: Mapped[str] = mapped_column(String, default="personal")  # public | personal | shared | confidential
    data_level: Mapped[str] = mapped_column(String, default="L1")  # L1 | L2 | L3
    lifecycle: Mapped[str] = mapped_column(String, default="active")  # active | summarized
    # 时间
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now, onupdate=datetime.now)
