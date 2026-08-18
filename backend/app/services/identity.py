"""Employee Identity 解析（Sprint 2）。

每次任务至少携带：employee_id / employee_type / employment_type / department / owner_id。
身份以数据库为准，调用方不得伪造（尤其 employment_type）。
"""

from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from .. import models


@dataclass(frozen=True)
class EmployeeIdentity:
    employee_id: str
    employee_type: str  # twin | virtual | rpa
    employment_type: str  # formal | intern（twin 取真人，virtual/rpa 取 Owner）
    department: str
    owner_id: str
    role_prompt: str = ""  # 数字员工人设（问答 system prompt 注入）
    # 环境绑定配置（Policy 读取，作为 environment 维度；Sandbox 不是权限来源）
    location: str = "remote"
    internet: str = "deny"
    max_data_level: str = "L1"
    allowed_domains: tuple = field(default_factory=tuple)


def resolve_identity(db: Session, employee_id: str) -> EmployeeIdentity | None:
    """按数字员工工号解析身份；找不到返回 None。"""
    emp = db.get(models.DigitalEmployee, employee_id)
    if not emp:
        return None
    # twin 取 source（真人），virtual/rpa 取 owner；找不到真人时按最低权限 intern 处理
    ref_no = emp.source_human_no or emp.owner_human_no
    human = db.get(models.HumanEmployee, ref_no) if ref_no else None
    employment_type = human.employment_type if human else "intern"
    return EmployeeIdentity(
        employee_id=emp.employee_no,
        employee_type=emp.type,
        employment_type=employment_type,
        department=emp.department,
        owner_id=emp.owner_human_no,
        role_prompt=emp.role_prompt or "",
        location=emp.location,
        internet=emp.internet,
        max_data_level=emp.max_data_level,
        allowed_domains=tuple(emp.allowed_domains or []),
    )
