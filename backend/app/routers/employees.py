from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models, schemas
from ..database import get_db
from ..services.chat import ChatOrchestrator
from ..services.identity import resolve_identity
from ..services.llm import DeepSeekProvider, LLMUnavailableError
from ..services.policy import ResourceRef, evaluate
from ..services.agentteams_gateway import AgentTeamsUnavailableError
from ..services import agentteams_lifecycle as at_lifecycle

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
    return schemas.EmployeeOut(
        id=emp.employee_no,
        employee_no=emp.employee_no,
        name=emp.name,
        type=emp.type,
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
    owner = db.get(models.HumanEmployee, payload.owner_human_no)
    if owner is None:
        raise HTTPException(status_code=400, detail="owner_human_no 对应的真人员工不存在")
    if payload.type == "twin" and payload.source_human_no != payload.owner_human_no:
        raise HTTPException(status_code=400, detail="数字分身的 source_human_no 必须与 owner_human_no 一致")
    employee_no = _next_employee_no(db, payload.type, payload.source_human_no)
    # 概念模型：分身=对话组织者（demo，不建容器）；虚拟员工/RPA=执行者（agentteams，自动建容器）
    effective_runtime = payload.runtime_type or (
        "demo" if payload.type == "twin" else "agentteams"
    )
    if payload.type == "twin" and effective_runtime != "demo":
        raise HTTPException(status_code=400, detail="数字分身只负责组织，不创建 AgentTeams worker")
    if effective_runtime not in ("demo", "agentteams"):
        raise HTTPException(status_code=400, detail="runtime_type 必须为 demo 或 agentteams")
    data = payload.model_dump()
    data["runtime_type"] = effective_runtime
    emp = models.DigitalEmployee(employee_no=employee_no, **data)
    runtime_ref: str | None = None
    if effective_runtime == "agentteams":
        expected_worker = at_lifecycle.worker_name(employee_no, payload.type)
        existed_before: bool | None = None
        try:
            existed_before = at_lifecycle.get_worker(expected_worker) is not None
            runtime_ref = at_lifecycle.create_worker(
                employee_no=employee_no,
                employee_type=payload.type,
                display_name=payload.name,
                soul=payload.role_prompt or f"你是{payload.name}，请按角色履行职责。",
            )
            at_lifecycle.add_team_member(runtime_ref)
        except AgentTeamsUnavailableError as exc:
            if existed_before is False:
                try:
                    at_lifecycle.delete_worker(expected_worker)
                except AgentTeamsUnavailableError:
                    pass
            raise HTTPException(status_code=503, detail=f"AgentTeams 容器创建失败：{exc}") from exc
        emp.runtime_ref = runtime_ref
    try:
        db.add(emp)
        db.commit()
        db.refresh(emp)
    except Exception:
        # 容器已建但落库失败：尽力清理容器，避免孤儿实例
        if runtime_ref:
            try:
                at_lifecycle.delete_worker(runtime_ref)
            except AgentTeamsUnavailableError:
                pass
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
    if emp.type == "twin" and desired_runtime != "demo":
        raise HTTPException(status_code=400, detail="数字分身只负责组织对话，不创建 AgentTeams worker")
    if desired_runtime not in ("demo", "agentteams"):
        raise HTTPException(status_code=400, detail="runtime_type 必须为 demo 或 agentteams")

    old_runtime = emp.runtime_type
    old_ref = emp.runtime_ref
    new_ref = old_ref
    if old_runtime != desired_runtime:
        if desired_runtime == "agentteams":
            try:
                new_ref = at_lifecycle.create_worker(
                    employee_no=emp.employee_no,
                    employee_type=emp.type,
                    display_name=patch.get("name", emp.name),
                    soul=patch.get("role_prompt", emp.role_prompt) or f"你是{emp.name}，请按角色履行职责。",
                )
                at_lifecycle.add_team_member(new_ref)
            except AgentTeamsUnavailableError as exc:
                raise HTTPException(status_code=503, detail=f"Worker 创建失败：{exc}") from exc
        elif old_ref:
            try:
                at_lifecycle.delete_worker(old_ref)
            except AgentTeamsUnavailableError as exc:
                raise HTTPException(status_code=503, detail=f"Worker 删除失败：{exc}") from exc
            new_ref = None

    for field, value in patch.items():
        setattr(emp, field, value)
    emp.runtime_ref = new_ref
    # 人设/显示名变化时同步 worker SOUL（仅 agentteams 运行时）
    if emp.runtime_type == "agentteams" and emp.runtime_ref:
        changed = payload.role_prompt is not None or payload.name is not None
        if changed:
            try:
                at_lifecycle.update_worker_soul(emp.runtime_ref, payload.name or emp.name, payload.role_prompt or emp.role_prompt)
            except AgentTeamsUnavailableError as exc:
                raise HTTPException(status_code=503, detail=f"Worker SOUL 同步失败：{exc}") from exc
    try:
        db.commit()
        db.refresh(emp)
    except Exception:
        db.rollback()
        # 数据库失败时尽量恢复外部运行实例，避免配置与实际资源长期分叉。
        if old_runtime != desired_runtime and desired_runtime == "agentteams" and new_ref:
            try:
                at_lifecycle.delete_worker(new_ref)
            except AgentTeamsUnavailableError:
                pass
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
        raise HTTPException(status_code=409, detail="数字员工仍有运行中或待审批任务，不能删除")
    if emp.type == "twin":
        owned_conversation = db.scalar(
            select(models.Conversation.id).where(
                models.Conversation.owner_human_no == emp.owner_human_no
            ).limit(1)
        )
        if owned_conversation:
            raise HTTPException(status_code=409, detail="数字分身仍绑定职场会话，不能直接删除")

    # 先删容器（失败则保留记录），再原子清理平台侧授权和成员关系。
    if emp.runtime_type == "agentteams" and emp.runtime_ref:
        try:
            at_lifecycle.delete_worker(emp.runtime_ref)
        except AgentTeamsUnavailableError as exc:
            raise HTTPException(status_code=503, detail=f"AgentTeams 容器删除失败：{exc}") from exc
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
    """返回数字员工对应的运行实例状态（AgentTeams worker / demo）。"""
    emp = db.get(models.DigitalEmployee, employee_no)
    if not emp:
        raise HTTPException(status_code=404, detail="员工不存在")
    if emp.runtime_type != "agentteams" or not emp.runtime_ref:
        return schemas.EmployeeRuntimeOut(
            employee_no=emp.employee_no,
            runtime_type=emp.runtime_type,
            runtime_ref=emp.runtime_ref,
            status=emp.status,
            detail="未绑定 AgentTeams 运行实例",
        )
    try:
        worker = at_lifecycle.get_worker(emp.runtime_ref)
    except AgentTeamsUnavailableError as exc:
        raise HTTPException(status_code=503, detail=f"AgentTeams 状态查询失败：{exc}") from exc
    return schemas.EmployeeRuntimeOut(
        employee_no=emp.employee_no,
        runtime_type=emp.runtime_type,
        runtime_ref=emp.runtime_ref,
        status=emp.status,
        worker_phase=(worker or {}).get("phase"),
        matrix_user_id=(worker or {}).get("matrixUserID"),
        room_id=(worker or {}).get("roomID"),
        detail="" if worker else "实例不存在（可能已删除）",
    )


@router.post("/{employee_no}/chat", response_model=schemas.ChatResponse)
def chat(employee_no: str, payload: schemas.ChatRequest, db: Session = Depends(get_db)):
    """一对一问答（Sprint 4）：User → Employee → LLM → Policy → Gateway → Adapter → LLM → Answer。"""
    orchestrator = ChatOrchestrator(DeepSeekProvider())
    try:
        result = orchestrator.handle_message(
            db,
            employee_no=employee_no,
            message=payload.message,
            session_id=payload.session_id,
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
