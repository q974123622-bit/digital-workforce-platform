"""知识库独占分配：AI员工 × 知识库授权的唯一写入口。

背景：``AgentKnowledgeGrant`` 是知识授权的唯一真相（``gateway.search_knowledge``、
``knowledge_registry.accessible_knowledge_bases``、``routers/internal.py`` 都读它），
但此前**只有 seed 会写**，没有任何管理端接口。

分配口径（用户确认）：「每个 AI 员工的知识库集合两两不相交」—— 允许一个员工持有多库，
但同一个知识库不得出现在两个员工的授权里。因此写入前先做互斥校验，冲突直接拒绝；
写入按「整体替换」语义在事务内先删后插，避免出现交叉的中间态。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

ACTION_READ = "read"
STATUS_ACTIVE = "active"


class KnowledgeGrantConflict(ValueError):
    """目标知识库已被其他 AI 员工占用。"""

    def __init__(self, conflicts: list[dict[str, str]]):
        self.conflicts = conflicts
        detail = "、".join(
            f"{item['knowledge_base_id']}（已被 {item['employee_id']} 占用）" for item in conflicts
        )
        super().__init__(f"知识库独占校验未通过：{detail}")


class KnowledgeGrantInvalid(ValueError):
    """入参不合法（员工/知识库不存在，或知识库已停用）。"""


def active_grants(db: Session, employee_id: str) -> list[str]:
    rows = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.employee_id == employee_id,
            models.AgentKnowledgeGrant.action == ACTION_READ,
            models.AgentKnowledgeGrant.status == STATUS_ACTIVE,
        )
    ).all()
    return sorted({row.knowledge_base_id for row in rows})


def occupancy(db: Session) -> dict[str, str]:
    """``{knowledge_base_id: employee_id}``：当前每个知识库被谁占用。"""
    rows = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.action == ACTION_READ,
            models.AgentKnowledgeGrant.status == STATUS_ACTIVE,
        )
    ).all()
    result: dict[str, str] = {}
    for row in rows:
        # 历史数据可能出现同一库多员工（旧 seed 允许交叉）：保留最先出现的一个，
        # 不静默改写存量，交给管理员在分配矩阵里收敛。
        result.setdefault(row.knowledge_base_id, row.employee_id)
    return result


def conflicts_for(db: Session, *, employee_id: str, knowledge_base_ids: list[str]) -> list[dict[str, str]]:
    """返回这些知识库中已被**其他**员工占用的部分。"""
    if not knowledge_base_ids:
        return []
    rows = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.knowledge_base_id.in_(knowledge_base_ids),
            models.AgentKnowledgeGrant.employee_id != employee_id,
            models.AgentKnowledgeGrant.action == ACTION_READ,
            models.AgentKnowledgeGrant.status == STATUS_ACTIVE,
        )
    ).all()
    return [
        {"knowledge_base_id": row.knowledge_base_id, "employee_id": row.employee_id}
        for row in rows
    ]


def replace_grants(
    db: Session,
    *,
    employee_id: str,
    knowledge_base_ids: list[str],
) -> list[str]:
    """整体替换某 AI 员工的知识库授权集合。

    - 员工不存在 / 知识库不存在或已停用 → ``KnowledgeGrantInvalid``；
    - 任一知识库已被其他员工占用 → ``KnowledgeGrantConflict``（调用方转 409）；
    - 事务内先删后插，保证不出现交叉的中间态。
    """
    employee = db.get(models.DigitalEmployee, employee_id)
    if employee is None:
        raise KnowledgeGrantInvalid(f"AI员工不存在：{employee_id}")

    clean: list[str] = []
    seen: set[str] = set()
    for raw in knowledge_base_ids:
        kb_id = str(raw or "").strip()
        if not kb_id or kb_id in seen:
            continue
        seen.add(kb_id)
        clean.append(kb_id)

    for kb_id in clean:
        kb = db.get(models.KnowledgeBase, kb_id)
        if kb is None:
            raise KnowledgeGrantInvalid(f"知识库不存在：{kb_id}")
        if kb.status != "active":
            raise KnowledgeGrantInvalid(f"知识库已停用，无法授权：{kb_id}")

    conflicts = conflicts_for(db, employee_id=employee_id, knowledge_base_ids=clean)
    if conflicts:
        raise KnowledgeGrantConflict(conflicts)

    db.query(models.AgentKnowledgeGrant).filter(
        models.AgentKnowledgeGrant.employee_id == employee_id,
    ).delete(synchronize_session=False)
    for kb_id in clean:
        db.add(models.AgentKnowledgeGrant(
            employee_id=employee_id,
            knowledge_base_id=kb_id,
            action=ACTION_READ,
            status=STATUS_ACTIVE,
        ))
    db.commit()
    return clean


def grant_knowledge_base(db: Session, *, employee_id: str, knowledge_base_id: str) -> list[str]:
    """**增量**加一个知识库（审批通过时调用），保留该员工已有的其他授权。

    与 ``replace_grants`` 的差别只在语义：这里是在现有集合上并集一个新库，供
    「员工申请 → 管理员审批」链路使用（管理员不该因为批了一个申请就把这个人
    原有的知识库清空）。独占冲突校验与整体替换完全同一套。
    """
    return grant_knowledge_bases(db, employee_id=employee_id, knowledge_base_ids=[knowledge_base_id])


def grant_knowledge_bases(
    db: Session, *, employee_id: str, knowledge_base_ids: list[str],
) -> list[str]:
    """增量并集授权：在现有集合上追加若干知识库，冲突/非法一律拒绝且不改数据。"""
    current = active_grants(db, employee_id)
    merged = current + [kb_id for kb_id in knowledge_base_ids if kb_id not in current]
    return replace_grants(db, employee_id=employee_id, knowledge_base_ids=merged)


def matrix(db: Session) -> dict:
    """独占分配矩阵：员工列表 + 知识库列表 + 占用关系。"""
    employees = db.scalars(
        select(models.DigitalEmployee).order_by(models.DigitalEmployee.employee_no)
    ).all()
    grants = db.scalars(
        select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.action == ACTION_READ,
            models.AgentKnowledgeGrant.status == STATUS_ACTIVE,
        )
    ).all()
    by_employee: dict[str, list[str]] = {}
    for row in grants:
        by_employee.setdefault(row.employee_id, []).append(row.knowledge_base_id)

    return {
        "employees": [
            {
                "employee_id": employee.employee_no,
                "employee_name": employee.name,
                "employee_type": employee.type,
                "knowledge_base_ids": sorted(by_employee.get(employee.employee_no, [])),
            }
            for employee in employees
        ],
        "knowledge_bases": db.scalars(
            select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)
        ).all(),
        "occupancy": occupancy(db),
    }
