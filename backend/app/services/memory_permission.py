"""记忆读写权限（PoC 简化版）。

集中定义"谁能读谁的记忆"，方便后续与老师对齐后修改。
所有规则都在 can_read_memory 里，改一处即可。
"""

from sqlalchemy.orm import Session

from .. import models

# ============ 权限配置（后续可改成配置/角色体系） ============

# 管理员名单：这些真人可读所有记忆（含涉密）。
# PoC 先写死一个（王老师 E10021）做演示；后续接三级权限/白名单。
ADMIN_HUMAN_NOS: set[str] = {"E10021"}


# ============ 权限判定 ============


def resolve_owner(entry: models.MemoryEntry, db: Session) -> str | None:
    """解析记忆主体的 owner（真人编号）。

    - human：owner = 本人（subject_no）
    - twin / virtual：owner = 数字员工的 owner_human_no
    """
    if entry.subject_type == "human":
        return entry.subject_no
    emp = db.get(models.DigitalEmployee, entry.subject_no)
    return emp.owner_human_no if emp else None


def can_read_memory(reader: str | None, entry: models.MemoryEntry, db: Session) -> bool:
    """读者（真人编号）能否读这条记忆。

    规则（命中即返回，顺序从宽到严）：
    1. reader 为空（系统内部调用）→ 允许（不过滤）
    2. 管理员 → 允许（含涉密）
    3. public → 允许（任何人）
    4. confidential → 拒绝（非管理员）
    5. 本人（subject 是 human 且 reader == subject_no）→ 允许
    6. personal / shared → owner 允许

    默认拒绝。
    """
    if reader is None:
        return True  # 系统内部调用，不做过滤
    if reader in ADMIN_HUMAN_NOS:
        return True
    if entry.visibility == "public":
        return True
    if entry.visibility == "confidential":
        return False
    if entry.subject_type == "human" and entry.subject_no == reader:
        return True
    return reader == resolve_owner(entry, db)
