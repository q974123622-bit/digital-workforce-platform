from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.chat import ChatOrchestrator
from ..services.identity import resolve_identity
from ..services.llm import DeepSeekProvider, LLMUnavailableError
from ..services.policy import ResourceRef, evaluate

router = APIRouter(prefix="/employees", tags=["employees"])


def _grants_for(db: Session, employee_no: str) -> list[schemas.GrantOut]:
    plugins = {p.id: p for p in db.scalars(select(models.Plugin)).all()}
    rows = db.scalars(
        select(models.EmployeePluginGrant).where(models.EmployeePluginGrant.employee_id == employee_no)
    ).all()
    out = []
    for g in rows:
        plugin = plugins.get(g.plugin_id)
        out.append(
            schemas.GrantOut(
                plugin_id=g.plugin_id,
                name=plugin.name if plugin else "",
                type=plugin.type if plugin else "",
                action=g.action,
                decision_mode=g.decision_mode,
                data_level=plugin.data_level if plugin else "",
            )
        )
    return out


def _employment_type_for(db: Session, emp: models.DigitalEmployee) -> str:
    """twin 取 source（真人）的 employment_type，virtual/rpa 取 owner；找不到按 intern 处理。"""
    ref_no = emp.source_human_no or emp.owner_human_no
    human = db.get(models.HumanEmployee, ref_no) if ref_no else None
    return human.employment_type if human else "intern"


def _to_out(db: Session, emp: models.DigitalEmployee) -> schemas.EmployeeOut:
    return schemas.EmployeeOut(
        id=emp.employee_no,
        employee_no=emp.employee_no,
        name=emp.name,
        type=emp.type,
        employment_type=_employment_type_for(db, emp),
        source_human_no=emp.source_human_no,
        owner_human_no=emp.owner_human_no,
        department=emp.department,
        role_prompt=emp.role_prompt,
        status=emp.status,
        runtime_type=emp.runtime_type,
        runtime_ref=emp.runtime_ref,
        location=emp.location,
        internet=emp.internet,
        max_data_level=emp.max_data_level,
        allowed_domains=emp.allowed_domains or [],
        grants=_grants_for(db, emp.employee_no),
    )


def _next_employee_no(db: Session, type: str, source: str | None) -> str:
    if type == "twin":
        if not source:
            raise HTTPException(status_code=400, detail="twin 必须提供 source_human_no")
        return f"DT-{source}"
    prefix = {"virtual": "VE", "rpa": "RPA"}.get(type)
    if not prefix:
        raise HTTPException(status_code=400, detail="type 必须为 twin/virtual/rpa")
    rows = db.scalars(
        select(models.DigitalEmployee.employee_no).where(models.DigitalEmployee.employee_no.like(f"{prefix}-%"))
    ).all()
    max_n = max((int(n.split("-")[1]) for n in rows if "-" in n), default=0)
    return f"{prefix}-{max_n + 1:04d}"


@router.get("", response_model=list[schemas.EmployeeOut])
def list_employees(
    type: str | None = Query(default=None),
    department: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    q = select(models.DigitalEmployee).order_by(models.DigitalEmployee.employee_no)
    if type:
        q = q.where(models.DigitalEmployee.type == type)
    if department:
        q = q.where(models.DigitalEmployee.department == department)
    return [_to_out(db, e) for e in db.scalars(q).all()]


@router.post("", response_model=schemas.EmployeeOut, status_code=201)
def create_employee(payload: schemas.EmployeeCreate, db: Session = Depends(get_db)):
    employee_no = _next_employee_no(db, payload.type, payload.source_human_no)
    emp = models.DigitalEmployee(employee_no=employee_no, **payload.model_dump())
    db.add(emp)
    db.commit()
    db.refresh(emp)
    return _to_out(db, emp)


@router.get("/{employee_no}", response_model=schemas.EmployeeOut)
def get_employee(employee_no: str, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    return _to_out(db, emp)


@router.put("/{employee_no}", response_model=schemas.EmployeeOut)
def update_employee(employee_no: str, payload: schemas.EmployeeUpdate, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    for field, value in payload.model_dump(exclude_unset=True).items():
        setattr(emp, field, value)
    db.commit()
    db.refresh(emp)
    return _to_out(db, emp)


@router.delete("/{employee_no}", status_code=204)
def delete_employee(employee_no: str, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    db.delete(emp)
    db.commit()


@router.post("/{employee_no}/chat", response_model=schemas.ChatResponse)
def chat(
    employee_no: str,
    payload: schemas.ChatRequest,
    x_demo_actor: str | None = Header(default=None, alias="X-Demo-Actor"),
    db: Session = Depends(get_db),
):
    """一对一问答（Sprint 4）：User → Employee → LLM → Policy → Gateway → Adapter → LLM → Answer。

    X-Demo-Actor 请求头携带"当前真人用户"工号，用于把对话自动写入记忆（记忆插件）。
    """
    orchestrator = ChatOrchestrator(DeepSeekProvider())
    try:
        result = orchestrator.handle_message(
            db,
            employee_no=employee_no,
            message=payload.message,
            session_id=payload.session_id,
            human_no=x_demo_actor,
        )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"LLM_UNAVAILABLE：{exc}") from exc

    cards = [
        {
            "plugin_id": c.plugin_id,
            "name": c.name,
            "decision": c.decision,
            "policy_id": c.policy_id,
            "reason": c.reason,
        }
        for c in result.tool_cards
    ]
    denied = None
    if result.policy_denied is not None:
        denied = {
            "plugin_id": result.policy_denied.plugin_id,
            "name": result.policy_denied.name,
            "decision": "deny",
            "policy_id": result.policy_denied.policy_id,
            "reason": result.policy_denied.reason,
        }
    return schemas.ChatResponse(
        session_id=result.session_id,
        trace_id=result.trace_id,
        message=result.message,
        tool_cards=cards,
        policy_denied=denied,
    )


@router.get("/{employee_no}/workspace", response_model=schemas.WorkspaceOut)
def get_workspace(employee_no: str, db: Session = Depends(get_db)):
    """员工工作台（Sprint 6）：身份 + 人设 + 可用插件 + 知识库权限 + 安全配置。"""
    emp = db.get(models.DigitalEmployee, employee_no)
    if emp is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    subject = resolve_identity(db, employee_no)

    plugins = [
        schemas.WorkspacePluginOut(
            plugin_id=g.plugin_id,
            name=g.name,
            type=g.type,
            action=g.action,
            decision_mode=g.decision_mode,
            data_level=g.data_level,
        )
        for g in _grants_for(db, employee_no)
    ]

    kbs: list[schemas.WorkspaceKbOut] = []
    for kb in db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all():
        plugin_id = "knowledge-l1" if kb.data_level == "L1" else "knowledge-l2"
        result = evaluate(db, subject, ResourceRef(type="knowledge", id=plugin_id, data_level=kb.data_level), "read")
        kbs.append(
            schemas.WorkspaceKbOut(
                knowledge_base_id=kb.id,
                name=kb.name,
                data_level=kb.data_level,
                description=kb.description,
                accessible=result.decision in ("allow", "approval"),
                decision=result.decision,
            )
        )

    return schemas.WorkspaceOut(
        employee=_to_out(db, emp),
        role_prompt=emp.role_prompt or "",
        plugins=plugins,
        knowledge_bases=kbs,
        security=schemas.WorkspaceSecurityOut(
            location=emp.location,
            internet=emp.internet,
            max_data_level=emp.max_data_level,
            allowed_domains=emp.allowed_domains or [],
        ),
    )
