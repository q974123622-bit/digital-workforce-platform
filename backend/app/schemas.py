from datetime import datetime

from pydantic import BaseModel, ConfigDict


class GrantOut(BaseModel):
    plugin_id: str
    name: str = ""
    type: str = ""
    action: str = "read"
    decision_mode: str = "allow"
    data_level: str = ""


class EmployeeCreate(BaseModel):
    name: str
    type: str
    source_human_no: str | None = None
    owner_human_no: str
    department: str = ""
    role_prompt: str = ""
    runtime_type: str = "demo"
    runtime_ref: str | None = None
    location: str = "remote"
    internet: str = "deny"
    max_data_level: str = "L1"
    allowed_domains: list[str] = []


class EmployeeUpdate(BaseModel):
    name: str | None = None
    department: str | None = None
    role_prompt: str | None = None
    status: str | None = None
    runtime_type: str | None = None
    runtime_ref: str | None = None
    location: str | None = None
    internet: str | None = None
    max_data_level: str | None = None
    allowed_domains: list[str] | None = None


class EmployeeOut(BaseModel):
    id: str
    employee_no: str
    name: str
    type: str
    source_human_no: str | None
    owner_human_no: str
    department: str
    role_prompt: str
    status: str
    runtime_type: str
    runtime_ref: str | None
    location: str
    internet: str
    max_data_level: str
    allowed_domains: list[str]
    grants: list[GrantOut] = []


class PluginBase(BaseModel):
    name: str
    type: str
    endpoint_ref: str = "mock://"
    data_level: str = "L1"
    status: str = "active"
    description: str = ""


class PluginCreate(PluginBase):
    id: str | None = None


class PluginUpdate(BaseModel):
    name: str | None = None
    type: str | None = None
    endpoint_ref: str | None = None
    data_level: str | None = None
    status: str | None = None
    description: str | None = None


class PluginOut(PluginBase):
    id: str


class PolicyBase(BaseModel):
    name: str
    effect: str = "allow"
    description: str = ""
    enabled: bool = True
    priority: int = 0


class PolicyCreate(PolicyBase):
    id: str | None = None


class PolicyUpdate(BaseModel):
    name: str | None = None
    effect: str | None = None
    description: str | None = None
    enabled: bool | None = None
    priority: int | None = None


class PolicyOut(PolicyBase):
    id: str


class AuditCreate(BaseModel):
    trace_id: str
    actor: str = ""
    employee_id: str | None = None
    team_id: str | None = None
    plugin_id: str | None = None
    action: str = ""
    decision: str = "deny"
    reason: str | None = None
    result_summary: str | None = None


class AuditOut(AuditCreate):
    model_config = ConfigDict(from_attributes=True)

    id: int
    ts: datetime


class TeamMemberOut(BaseModel):
    employee_id: str
    role: str


class TeamOut(BaseModel):
    id: str
    name: str
    leader_employee_id: str
    description: str = ""
    members: list[TeamMemberOut] = []


class KnowledgeBaseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    name: str
    level: str
    domain: str
    description: str
    status: str
    doc_path: str | None
