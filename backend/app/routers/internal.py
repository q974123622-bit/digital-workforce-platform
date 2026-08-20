"""内部接口（服务间，不暴露前端）：Policy Evaluate / Gateway Invoke。"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import schemas
from ..database import get_db
from ..services.gateway import invoke_plugin, search_knowledge, write_audit
from ..services.identity import resolve_identity
from ..services.policy import DECISION_ALLOW, DECISION_DENY, ResourceRef, evaluate
from ..services.runtime_adapter import DockerHarnessRuntimeAdapter, RuntimeResult
from ..services.sandbox_manager import SandboxManager
from ..services.sandbox_policy import from_identity

router = APIRouter(prefix="/internal", tags=["internal"])


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
