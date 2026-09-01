"""内部接口（服务间，不暴露前端）：Policy / Gateway / Harness tools。"""

from uuid import uuid4

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.gateway import invoke_plugin, search_knowledge, write_audit
from ..services.harness_token import HarnessClaims, verify_token
from ..services.identity import resolve_identity
from ..services.policy import DECISION_ALLOW, DECISION_DENY, ResourceRef, evaluate
from ..services.runtime_adapter import DockerHarnessRuntimeAdapter, RuntimeResult
from ..services.sandbox_manager import SandboxManager
from ..services.sandbox_policy import from_identity
from ..services import execution_events

router = APIRouter(prefix="/internal", tags=["internal"])


class AgentKnowledgeToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str
    query: str


class AgentDelegateToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_agent_id: str
    question: str


def _claims(authorization: str | None = Header(default=None)) -> HarnessClaims:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="缺少 Harness 工具令牌")
    try:
        return verify_token(authorization[7:].strip())
    except ValueError as exc:
        raise HTTPException(status_code=401, detail=str(exc)) from exc


def _can_delegate(db: Session, claims: HarnessClaims) -> bool:
    profile = db.get(models.AgentProfile, claims.employee_id)
    return bool(profile and profile.identity_kind == "human_twin" and claims.depth == 0)


def _agent_search(
    db: Session, claims: HarnessClaims, payload: AgentKnowledgeToolIn,
) -> dict:
    execution = execution_events.execution_for_trace(db, claims.trace_id)
    kb = db.get(models.KnowledgeBase, payload.knowledge_base_id)
    kb_name = kb.name if kb is not None else payload.knowledge_base_id
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="knowledge_started", stage="knowledge_search",
            status="running", actor_employee_id=claims.employee_id,
            knowledge_base_id=payload.knowledge_base_id,
            title=f"正在检索 {kb_name}", detail="正在通过授权知识接口查找相关资料",
        )
    used = db.scalar(
        select(func.count(models.AuditEvent.id)).where(
            models.AuditEvent.trace_id == claims.trace_id,
            models.AuditEvent.employee_id == claims.employee_id,
            models.AuditEvent.action == "read",
        )
    ) or 0
    if used >= 5:
        raise HTTPException(status_code=429, detail="单次任务最多检索五次")
    try:
        out = search_knowledge(
            db,
            employee_id=claims.employee_id,
            knowledge_base_id=payload.knowledge_base_id,
            query=payload.query,
            trace_id=claims.trace_id,
            requester_human_no=claims.requester_human_no,
        )
    except Exception:
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="knowledge_completed", stage="knowledge_search",
                status="running", actor_employee_id=claims.employee_id,
                knowledge_base_id=payload.knowledge_base_id,
                title=f"{kb_name} 检索未完成", detail="知识接口返回安全错误，数字员工正在判断后续步骤",
            )
        raise
    data = out.get("data") or {}
    hits = data.get("hits", [])
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="knowledge_completed", stage="knowledge_search",
            status="running", actor_employee_id=claims.employee_id,
            knowledge_base_id=payload.knowledge_base_id, hit_count=len(hits),
            title=f"{kb_name} 检索完成", detail=f"共找到 {len(hits)} 条可用资料，正在检查证据充分性",
        )
    return {
        "source": "mock" if data.get("source") == "demo" else data.get("source", "mock"),
        "knowledge_base_id": payload.knowledge_base_id,
        "hits": hits,
        "decision": out.get("decision", "deny"),
        "trace_id": claims.trace_id,
    }


def _agent_delegate(
    db: Session, claims: HarnessClaims, payload: AgentDelegateToolIn,
) -> dict:
    if not _can_delegate(db, claims):
        raise HTTPException(status_code=403, detail="当前数字员工不能委派")
    target_profile = db.get(models.AgentProfile, payload.target_agent_id)
    target = db.get(models.DigitalEmployee, payload.target_agent_id)
    if not target_profile or not target or target.status != "active" or target_profile.identity_kind != "role_employee":
        raise HTTPException(status_code=403, detail="委派目标不是可用的岗位数字员工")
    existing = db.scalar(
        select(func.count(models.DelegationRun.id)).where(
            models.DelegationRun.trace_id == claims.trace_id,
            models.DelegationRun.sender_employee_id == claims.employee_id,
        )
    ) or 0
    if existing:
        raise HTTPException(status_code=409, detail="单次任务只能委派一次")
    run = models.DelegationRun(
        id=f"D-{uuid4().hex[:12].upper()}", trace_id=claims.trace_id,
        conversation_id="harness", requester_human_no=claims.requester_human_no,
        sender_employee_id=claims.employee_id, recipient_employee_id=payload.target_agent_id,
        action="delegate", goal="", reason="Harness 自主委派", status="running",
    )
    db.add(run)
    db.commit()
    execution = execution_events.execution_for_trace(db, claims.trace_id)
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="delegation_started", stage="delegation",
            status="running", actor_employee_id=claims.employee_id,
            target_agent_id=payload.target_agent_id,
            title=f"正在向 {target.name} 咨询", detail="已按一次委派限制交由岗位数字员工处理",
        )
    try:
        from ..services.harness_agent import run_agent

        result = run_agent(
            db, employee_id=payload.target_agent_id,
            requester_human_no=claims.requester_human_no,
            message=payload.question, history=[], trace_id=claims.trace_id, depth=1,
        )
        run.status = "completed"
        run.evidence = [card.plugin_id for card in result.tool_cards]
        db.add(run)
        db.commit()
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="delegation_completed", stage="delegation",
                status="running", actor_employee_id=claims.employee_id,
                target_agent_id=payload.target_agent_id,
                title=f"{target.name} 已完成协助", detail="委派结果已返回，数字分身正在整合答复",
            )
        return {
            "target_agent_id": payload.target_agent_id,
            "answer": result.message,
            "decision": "allow",
            "trace_id": claims.trace_id,
        }
    except Exception:
        run.status = "failed"
        db.add(run)
        db.commit()
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="delegation_completed", stage="delegation",
                status="running", actor_employee_id=claims.employee_id,
                target_agent_id=payload.target_agent_id,
                title=f"{target.name} 暂时无法协助", detail="委派未完成，数字分身正在判断是否可独立答复",
            )
        raise


@router.post("/agent-tools/knowledge/search")
def agent_knowledge_search(
    payload: AgentKnowledgeToolIn, claims: HarnessClaims = Depends(_claims), db: Session = Depends(get_db),
):
    return _agent_search(db, claims, payload)


@router.post("/agent-tools/delegate")
def agent_delegate(
    payload: AgentDelegateToolIn, claims: HarnessClaims = Depends(_claims), db: Session = Depends(get_db),
):
    return _agent_delegate(db, claims, payload)


@router.post("/agent-tools/mcp")
async def agent_tools_mcp(
    request: Request, claims: HarnessClaims = Depends(_claims), db: Session = Depends(get_db),
):
    """Minimal stateless Streamable HTTP MCP endpoint for the DSH profile."""
    body = await request.json()
    method = body.get("method")
    request_id = body.get("id")
    if method == "initialize":
        result = {
            "protocolVersion": "2025-03-26",
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "dwp-platform-tools", "version": "0.1.0"},
        }
    elif method == "notifications/initialized":
        return {"jsonrpc": "2.0", "result": {}}
    elif method == "tools/list":
        tools = [{
            "name": "search_knowledge",
            "description": "在当前数字员工已授权的一个知识库中检索，每次只能检索一库。",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "knowledge_base_id": {"type": "string"},
                    "query": {"type": "string"},
                },
                "required": ["knowledge_base_id", "query"],
            },
        }]
        if _can_delegate(db, claims):
            tools.append({
                "name": "ask_digital_employee",
                "description": "向一名岗位数字员工求助一次，对方不能继续委派。",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "target_agent_id": {"type": "string", "enum": ["AI-GENERAL", "AI-INVESTMENT"]},
                        "question": {"type": "string"},
                    },
                    "required": ["target_agent_id", "question"],
                },
            })
        result = {"tools": tools}
    elif method == "tools/call":
        params = body.get("params") or {}
        arguments = params.get("arguments") or {}
        try:
            if params.get("name") == "search_knowledge":
                data = _agent_search(db, claims, AgentKnowledgeToolIn.model_validate(arguments))
            elif params.get("name") == "ask_digital_employee":
                data = _agent_delegate(db, claims, AgentDelegateToolIn.model_validate(arguments))
            else:
                raise HTTPException(status_code=404, detail="未知工具")
            result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
        except Exception as exc:
            detail = exc.detail if isinstance(exc, HTTPException) else "工具执行失败"
            result = {"isError": True, "content": [{"type": "text", "text": str(detail)}]}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/policy/evaluate", response_model=schemas.PolicyEvaluateOut)
def policy_evaluate(payload: schemas.PolicyEvaluateIn, db: Session = Depends(get_db)):
    """四维策略评估；subject 以数据库身份为准（调用方传入的身份字段仅作参考，不可伪造）。"""
    subject = resolve_identity(db, payload.subject.employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    resource = ResourceRef(
        type=payload.resource.type,
        id=payload.resource.id,
        data_level=payload.resource.data_level or "L1",
    )
    result = evaluate(db, subject, resource, payload.action, payload.context)
    return schemas.PolicyEvaluateOut(
        decision=result.decision,
        policy_id=result.policy_id,
        reason=result.reason,
    )


@router.post("/gateway/invoke", response_model=schemas.GatewayInvokeOut)
def gateway_invoke(payload: schemas.GatewayInvokeIn, db: Session = Depends(get_db)):
    """唯一能力执行入口：Identity → Policy → Gateway → Harness → Adapter 工具 → Audit。"""
    return invoke_plugin(
        db,
        employee_id=payload.employee_id,
        plugin_id=payload.plugin_id,
        action=payload.action,
        params=payload.params or {},
        trace_id=payload.trace_id,
    )


@router.post("/harness/execute", response_model=schemas.HarnessExecuteOut)
def harness_execute(payload: schemas.HarnessExecuteIn, db: Session = Depends(get_db)):
    """DeepSeek Harness 执行引擎（壳回调平台入口）。

    调用链：Employee Identity -> Policy（internet/remote_only）-> Docker Harness -> Audit。
    """
    subject = resolve_identity(db, payload.employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")

    # Policy 前置：Harness 是受控模型执行通道，不等同于业务公网插件；
    # internet 字段继续约束 http 插件，Harness 还受 DWP_HARNESS_ENABLED 与 SAFEMODE 约束。
    result = evaluate(
        db,
        subject,
        ResourceRef(type="runtime", id="harness", data_level=subject.max_data_level or "L1"),
        "execute",
        {},
    )
    if result.decision != DECISION_ALLOW:
        write_audit(
            db,
            trace_id=payload.trace_id,
            employee_id=payload.employee_no,
            plugin_id="harness:execute",
            action="execute",
            decision=result.decision,
            reason=result.reason or "Policy 拒绝",
            result_summary=payload.task_prompt[:200],
        )
        return schemas.HarnessExecuteOut(
            trace_id=payload.trace_id,
            decision=result.decision,
            policy_id=result.policy_id,
            reason=result.reason or "Policy 拒绝",
        )

    runner = DockerHarnessRuntimeAdapter()
    out: RuntimeResult = runner.run(
        employee_id=payload.employee_no,
        task_prompt=payload.task_prompt,
        trace_id=payload.trace_id,
    )
    write_audit(
        db,
        trace_id=payload.trace_id,
        employee_id=payload.employee_no,
        plugin_id="harness:execute",
        action="execute",
        decision=DECISION_ALLOW,
        reason=f"Harness 执行完成（mode={out.mode}）",
        result_summary=(out.result or "")[:200],
    )
    return schemas.HarnessExecuteOut(
        trace_id=payload.trace_id,
        decision=DECISION_ALLOW,
        reason="Harness 执行完成",
        mode=out.mode,
        ok=out.ok,
        result=out.result,
    )


@router.post("/knowledge/search", response_model=schemas.KnowledgeSearchOut)
def knowledge_search(payload: schemas.KnowledgeSearchIn, db: Session = Depends(get_db)):
    """知识库专用入口（Sprint 3）：统一经过 Policy → Gateway → KnowledgeAdapter → Audit。"""
    return search_knowledge(
        db,
        employee_id=payload.employee_id,
        knowledge_base_id=payload.knowledge_base_id,
        query=payload.query,
        trace_id=payload.trace_id,
    )


@router.post("/sandbox/run", response_model=schemas.SandboxRunOut)
def sandbox_run(payload: schemas.SandboxRunIn, db: Session = Depends(get_db)):
    """Sandbox 执行（Sprint 3，Mock Executor）：先 Policy 后启动，被拒不执行。"""
    subject = resolve_identity(db, payload.employee_id)
    if subject is None:
        raise HTTPException(status_code=404, detail="数字员工不存在")
    policy = from_identity(subject)
    trace_id = f"SBX-{payload.task_id or subject.employee_id}"
    resource_id = "local" if payload.execution_location == "local" else "remote"

    # 1) 位置策略（remote_only 禁止 local）：经 Policy Engine 判定
    result = evaluate(db, subject, ResourceRef(type="sandbox", id=resource_id, data_level="L1"), "execute")
    if result.decision == DECISION_DENY:
        reason = f"{result.policy_id}: {result.reason}" if result.policy_id else result.reason
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=subject.employee_id,
            plugin_id=f"sandbox:{resource_id}",
            action="execute",
            decision=DECISION_DENY,
            reason=reason,
        )
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": result.policy_id, "reason": reason, "audit_id": audit_id},
        )

    # 2) 网络策略（internet deny 禁止非 none 网络）
    if payload.network != "none" and policy.internet_access == "deny":
        reason = "POLICY-003: 禁网员工禁止公网访问"
        audit_id = write_audit(
            db,
            trace_id=trace_id,
            employee_id=subject.employee_id,
            plugin_id=f"sandbox:{resource_id}",
            action="execute",
            decision=DECISION_DENY,
            reason=reason,
        )
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": "POLICY-003", "reason": reason, "audit_id": audit_id},
        )

    # 3) 允许：SandboxManager 执行（Docker 真容器优先；daemon 不可用/失败自动降级 local）
    executed = SandboxManager().execute(
        employee_id=subject.employee_id,
        command=payload.command,
        mount_dir=payload.mount_dir or policy.filesystem_scope,
        network=payload.network,
        execution_location=payload.execution_location,
        trace_id=trace_id,
    )
    write_audit(
        db,
        trace_id=trace_id,
        employee_id=subject.employee_id,
        plugin_id=f"sandbox:{resource_id}",
        action="execute",
        decision=DECISION_ALLOW,
        result_summary=f"mode={executed['mode']} status={executed['status']}",
    )
    return schemas.SandboxRunOut(mode=executed["mode"], status=executed["status"], logs=executed["logs"])
