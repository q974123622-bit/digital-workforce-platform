"""知识库市场：员工侧货架 + 申请（与插件市场同一套「可见 → 申请 → 审批 → 授权」口径）。

与插件市场的差别只有两点，都是知识库本身的语义决定的：

1. **独占**：知识库是独占资源（``knowledge_grant`` 的口径是「任意两个 AI 员工的
   集合两两不相交」），因此货架上被他人占用的库**照常展示但标注占用方**，申请入口
   置灰并说明原因 —— 让员工知道「这个库存在、但不该我来用」，而不是假装它不存在。
2. **不可达置灰**：数据源不可达的库保留在货架上并标注，与
   ``knowledge_registry.accessible_knowledge_bases``「只从工具清单摘除、目录保留」
   的口径一致。

状态取值（``state``）：

* ``enabled``：已授权给本人分身，可直接用；
* ``pending``：本人分身已提交申请，等待审批；
* ``occupied``：已被其他 AI 员工占用（``occupant`` 给出占用方）；
* ``requestable``：可申请。
"""

from __future__ import annotations

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import knowledge_grant, knowledge_source_health
from .access_request import formal_subject
from .gateway import write_audit

RESOURCE_TYPE = "knowledge_base"


def twin_for(human_employee_no: str) -> str:
    """自然人工号 → 分身工号（知识授权挂在 AI 员工身上，不是自然人）。"""
    return human_employee_no if human_employee_no.startswith("DT-") else f"DT-{human_employee_no}"


def _state(
    kb_id: str, *, granted: set[str], pending: dict[str, int], occupancy: dict[str, str], twin_id: str,
) -> tuple[str, str | None, int | None]:
    if kb_id in granted:
        return "enabled", None, None
    request_id = pending.get(kb_id)
    if request_id is not None:
        return "pending", None, request_id
    occupant = occupancy.get(kb_id)
    if occupant and occupant != twin_id:
        return "occupied", occupant, None
    return "requestable", None, None


def market_knowledge_bases(db: Session, requester_human_no: str) -> list[dict]:
    """全部 active 知识库 + 本人分身的当前状态。

    货架对所有员工全可见（用户口径），因此这里不做 Policy 过滤：能不能用由
    ``state`` 表达，实际能不能读仍由 ``AgentKnowledgeGrant`` + Policy 决定。
    """
    twin_id = twin_for(requester_human_no)
    granted = set(knowledge_grant.active_grants(db, twin_id))
    occupancy = knowledge_grant.occupancy(db)
    pending = {
        row.resource_id: row.id
        for row in db.scalars(select(models.AccessRequest).where(
            models.AccessRequest.applicant_no == twin_id,
            models.AccessRequest.resource_type == RESOURCE_TYPE,
            models.AccessRequest.status == "pending",
        )).all()
    }
    occupant_names = {
        employee.employee_no: employee.name
        for employee in db.scalars(select(models.DigitalEmployee)).all()
    }
    result: list[dict] = []
    for kb in db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all():
        if kb.status != "active":
            continue
        state, occupant, request_id = _state(
            kb.id, granted=granted, pending=pending, occupancy=occupancy, twin_id=twin_id,
        )
        source = knowledge_source_health.active_source_for_kb(db, kb)
        result.append({
            "id": kb.id,
            "name": kb.name,
            "domain": kb.domain,
            "data_level": kb.data_level,
            "description": kb.description,
            "status": kb.status,
            "state": state,
            "request_id": request_id,
            "occupant": occupant,
            "occupant_name": occupant_names.get(occupant or "", occupant or ""),
            "active_source": source,
            "source_available": source is not None,
        })
    return result


def request_knowledge_base(
    db: Session, *, requester_human_no: str, knowledge_base_id: str, reason: str,
) -> models.AccessRequest:
    """提交知识库使用申请；同一知识库已有 pending 申请时 409（幂等）。

    口径沿用敏感资源：仅正式员工的分身可申请（``ACCESS-FORMAL-ONLY``）。
    """
    kb_id = (knowledge_base_id or "").strip()
    kb = db.get(models.KnowledgeBase, kb_id)
    if kb is None or kb.status != "active":
        raise HTTPException(
            status_code=404,
            detail={"message": "知识库不存在或已停用", "resource_id": kb_id},
        )
    twin_id = twin_for(requester_human_no)
    formal_subject(db, twin_id)

    if kb.id in set(knowledge_grant.active_grants(db, twin_id)):
        raise HTTPException(status_code=409, detail={
            "message": "该知识库已授权给你的数字分身，无需申请", "reason": "already_granted",
        })
    occupant = knowledge_grant.occupancy(db).get(kb.id)
    if occupant and occupant != twin_id:
        raise HTTPException(status_code=409, detail={
            "message": f"该知识库已被 {occupant} 占用，无法申请",
            "reason": "knowledge_grant_conflict",
            "conflicts": [{"knowledge_base_id": kb.id, "employee_id": occupant}],
        })
    duplicate = db.scalar(select(models.AccessRequest).where(
        models.AccessRequest.applicant_no == twin_id,
        models.AccessRequest.resource_type == RESOURCE_TYPE,
        models.AccessRequest.resource_id == kb.id,
        models.AccessRequest.status == "pending",
    ))
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "相同资源已有待审批申请", "request_id": duplicate.id},
        )

    request = models.AccessRequest(
        applicant_no=twin_id, resource_type=RESOURCE_TYPE, resource_id=kb.id,
        reason=(reason or "")[:500], status="pending", approval_chain=[],
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    write_audit(
        db, trace_id=f"ARQ-{request.id}", employee_id=twin_id, plugin_id=kb.data_level,
        knowledge_base_id=kb.id, action="access_apply", decision="pending",
        reason=f"knowledge_base:{kb.id}", result_summary="status=pending",
    )
    return request
