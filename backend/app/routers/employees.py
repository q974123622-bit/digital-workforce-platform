from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models, schemas
from ..database import get_db
from ..services.chat import ChatOrchestrator, tool_card_dict
from ..services.harness_agent import run_agent
from ..services.identity import resolve_identity
from ..services import knowledge_source_health
from ..services.knowledge_registry import plugin_id_for_level
from ..services.llm import DeepSeekProvider, LLMUnavailableError
from ..services.policy import ResourceRef, evaluate
from ..services import config

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


def _to_out(db: Session, emp: models.DigitalEmployee) -> schemas.EmployeeOut:
    identity = resolve_identity(db, emp.employee_no)
    owner = db.get(models.HumanEmployee, emp.owner_human_no)
    return schemas.EmployeeOut(
        id=emp.employee_no,
        employee_no=emp.employee_no,
        name=emp.name,
        type=emp.type,
        employment_type=identity.employment_type,
        source_human_no=emp.source_human_no,
        owner_human_no=emp.owner_human_no,
        owner_name=owner.name if owner else emp.owner_human_no,
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
        avatar_url=emp.avatar_url,
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
    owner = db.get(models.HumanEmployee, payload.owner_human_no)
    if owner is None:
        raise HTTPException(status_code=400, detail="owner_human_no 对应的真人员工不存在")
    if payload.type == "twin" and payload.source_human_no != payload.owner_human_no:
        raise HTTPException(status_code=400, detail="数字分身的 source_human_no 必须与 owner_human_no 一致")
    if payload.type == "twin":
        existing_twin = db.scalar(
            select(models.DigitalEmployee).where(
                models.DigitalEmployee.type == "twin",
                models.DigitalEmployee.source_human_no == payload.source_human_no,
            )
        )
        if existing_twin is not None:
            raise HTTPException(
                status_code=409,
                detail=f"每位真人只能拥有一个数字分身，当前已绑定 {existing_twin.employee_no}",
            )
    employee_no = _next_employee_no(db, payload.type, payload.source_human_no)
    # 运行实例由 DeepSeek Harness 统一管理（twin 与 virtual/rpa 均走 harness，无需外部容器）。
    effective_runtime = payload.runtime_type or "harness"
    if effective_runtime not in ("demo", "harness"):
        raise HTTPException(status_code=400, detail="runtime_type 必须为 demo 或 harness")
    data = payload.model_dump()
    data["runtime_type"] = effective_runtime
    emp = models.DigitalEmployee(employee_no=employee_no, **data)
    try:
        db.add(emp)
        db.commit()
        db.refresh(emp)
    except Exception:
        db.rollback()
        raise
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
    patch = payload.model_dump(exclude_unset=True)
    desired_runtime = patch.get("runtime_type", emp.runtime_type)
    if desired_runtime not in ("demo", "harness"):
        raise HTTPException(status_code=400, detail="runtime_type 必须为 demo 或 harness")

    for field, value in patch.items():
        setattr(emp, field, value)
    try:
        db.commit()
        db.refresh(emp)
    except Exception:
        db.rollback()
        raise
    return _to_out(db, emp)


@router.delete("/{employee_no}", status_code=204)
def delete_employee(employee_no: str, db: Session = Depends(get_db)):
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    active_runs = db.scalars(
        select(models.TaskRun).where(models.TaskRun.status.in_(["running", "approval"]))
    ).all()
    if any(
        any(sub.get("worker_id") == employee_no for sub in (run.subtasks or []))
        for run in active_runs
    ):
        raise HTTPException(status_code=409, detail="AI员工仍有运行中或待审批任务，不能删除")
    if emp.type == "twin":
        owned_conversation = db.scalar(
            select(models.Conversation.id).where(
                models.Conversation.owner_human_no == emp.owner_human_no
            ).limit(1)
        )
        if owned_conversation:
            raise HTTPException(status_code=409, detail="数字分身仍绑定职场会话，不能直接删除")

    # 原子清理平台侧授权和成员关系。
    for conv in db.scalars(select(models.Conversation)).all():
        participants = [
            p for p in (conv.participants or []) if p.get("employee_no") != employee_no
        ]
        if len(participants) != len(conv.participants or []):
            conv.participants = participants
            flag_modified(conv, "participants")
    db.query(models.EmployeePluginGrant).filter(
        models.EmployeePluginGrant.employee_id == employee_no
    ).delete()
    db.query(models.TeamMember).filter(models.TeamMember.employee_id == employee_no).delete()
    db.delete(emp)
    db.commit()


@router.get("/{employee_no}/runtime", response_model=schemas.EmployeeRuntimeOut)
def get_employee_runtime(employee_no: str, db: Session = Depends(get_db)):
    """返回AI员工对应的运行实例状态（DeepSeek Harness）。"""
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    return schemas.EmployeeRuntimeOut(
        employee_no=emp.employee_no,
        runtime_type=emp.runtime_type,
        runtime_ref=emp.runtime_ref,
        status=emp.status,
        detail="运行实例由 DeepSeek Harness 管理",
    )


@router.post("/{employee_no}/chat", response_model=schemas.ChatResponse)
def chat(employee_no: str, payload: schemas.ChatRequest, db: Session = Depends(get_db)):
    """一对一问答（Sprint 4）：User → Employee → LLM → Policy → Gateway → Adapter → LLM → Answer。"""
    try:
        employee = db.get(models.DigitalEmployee, employee_no)
        if employee and employee_no in {"AI-GENERAL", "AI-INVESTMENT", "AI-RESEARCH", "AI-FUND", "AI-XYZQ", "DT-E10281", "DT-E20999"} and (
            config.get("DWP_AGENT_EXECUTION_MODE", "harness") or "harness"
        ) == "harness":
            result = run_agent(
                db, employee_id=employee_no,
                requester_human_no=employee.owner_human_no or "E10281",
                message=payload.message, history=[],
            )
        else:
            result = ChatOrchestrator(DeepSeekProvider()).handle_message(
                db, employee_no=employee_no, message=payload.message,
                session_id=payload.session_id,
            )
    except LLMUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"LLM_UNAVAILABLE：{exc}") from exc

    cards = [tool_card_dict(c) for c in result.tool_cards]
    denied = None
    if result.policy_denied is not None:
        denied = {
            "plugin_id": result.policy_denied.plugin_id,
            "name": result.policy_denied.name,
            "decision": "deny",
            "source": result.policy_denied.source,
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
    explicit_kb_grants = {
        row.knowledge_base_id
        for row in db.scalars(select(models.AgentKnowledgeGrant).where(
            models.AgentKnowledgeGrant.employee_id == employee_no,
            models.AgentKnowledgeGrant.action == "read",
            models.AgentKnowledgeGrant.status == "active",
        )).all()
    }
    for kb in db.scalars(select(models.KnowledgeBase).order_by(models.KnowledgeBase.id)).all():
        plugin_id = plugin_id_for_level(kb.data_level)
        result = evaluate(db, subject, ResourceRef(type="knowledge", id=plugin_id, data_level=kb.data_level), "read")
        # accessible=有权限；available=数据源现在真的能用。两者分开，避免把连通性问题
        # 显示成「没权限」，员工侧也能看懂「这个库暂时用不了」。
        availability = knowledge_source_health.kb_availability(db, kb)
        kbs.append(
            schemas.WorkspaceKbOut(
                knowledge_base_id=kb.id,
                name=kb.name,
                data_level=kb.data_level,
                description=kb.description,
                accessible=kb.id in explicit_kb_grants and result.decision in ("allow", "approval"),
                decision=result.decision if kb.id in explicit_kb_grants else "deny",
                available=availability["available"],
                active_source=availability["active_source"],
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
