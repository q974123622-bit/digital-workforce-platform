"""插件与知识资源的使用申请：申请 → 审批 → 授权（唯一写链路）。

员工端插件市场、老 ``/access-requests`` 接口、管理端审批面板共用本模块，
避免两套授权逻辑漂移：

* **插件类申请**：审批通过后 upsert ``AgentPluginBinding``
  （``admin_enabled ∧ employee_enabled ∧ decision_mode='allow'``）。这是插件
  可用性的唯一授权真相 —— 员工端目录、提示词注入、群聊可执行插件、网关判定
  因此同时生效，无需额外写 ``employee_plugin_grant``；L3 插件额外补一条只读
  白名单（``EmployeePluginGrant``），与知识库口径一致。
* **知识库类申请**：保持「L3 只读白名单」原行为不变。

正式员工校验（``ACCESS-FORMAL-ONLY``）只对敏感资源生效：知识库申请一律要求
正式员工；插件申请仅 L3 插件要求正式员工，L1/L2 插件允许任意员工自助申请
（审批人本身就是人工闸门，且实习生被一刀切挡在插件市场外并不合理）。
"""

from __future__ import annotations

import logging
from datetime import datetime

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .gateway import write_audit
from .identity import resolve_identity
from .knowledge_registry import plugin_id_for_level, resolve as resolve_kb
from .policy import DECISION_ALLOW, DECISION_DENY

logger = logging.getLogger(__name__)

TERMINAL_STATUSES = {"granted", "rejected"}


def formal_subject(db: Session, employee_no: str, *, approver: bool = False):
    """正式员工校验（原 ``routers/access.py::_formal_subject``，两侧共用）。"""
    subject = resolve_identity(db, employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail={"message": "AI员工不存在", "employee_no": employee_no})
    allowed = subject.employment_type == "formal" and (not approver or subject.employee_type == "twin")
    if not allowed:
        role = "正式员工数字分身" if approver else "正式员工"
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": "ACCESS-FORMAL-ONLY", "reason": f"仅{role}可执行该操作"},
        )
    return subject


def has_published_version(db: Session, plugin: models.Plugin) -> bool:
    return db.scalar(select(models.PluginVersion.id).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.publish_status == "published",
    ).limit(1)) is not None


def resolve_plugin_for_request(
    db: Session, plugin_id: str, *, requester_human_no: str | None = None,
) -> models.Plugin:
    """校验插件可以进入申请流程；不满足时抛 4xx。

    ``requester_human_no`` 为空表示只做资源侧校验（审批时用，申请人已在提交时校验过）。
    调用方既可能传自然人工号（``E10281``）也可能传分身工号（``DT-E10281``），
    因此属主比对同时接受两种写法。
    """
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail={"message": "插件不存在", "resource_id": plugin_id})
    if plugin.scope == "personal" and requester_human_no and plugin.owner_human_no:
        owners = {plugin.owner_human_no, f"DT-{plugin.owner_human_no}"}
        if requester_human_no not in owners:
            raise HTTPException(status_code=403, detail="个人插件只能由属主本人申请")
    if plugin.status != "active":
        raise HTTPException(status_code=409, detail="插件已下架或尚未启用，暂不可申请")
    if not has_published_version(db, plugin):
        raise HTTPException(status_code=409, detail="插件尚未发布可用版本，暂不可申请")
    if requester_human_no:
        target = requester_human_no if requester_human_no.startswith("DT-") else f"DT-{requester_human_no}"
        binding = db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin.id,
            models.AgentPluginBinding.target_agent_id == target,
        ))
        if binding is not None and binding.decision_mode == "deny":
            # 管理员的明确禁止意图不应被员工申请绕过。
            raise HTTPException(status_code=409, detail="管理员已禁止该插件，暂不可申请")
    return plugin


def plugin_request_subject(db: Session, employee_no: str, plugin: models.Plugin) -> str:
    """返回可发起该插件申请的分身工号；不合规时抛 403。"""
    twin_id = f"DT-{employee_no}"
    if plugin.data_level == "L3":
        # 敏感插件沿用敏感资源口径：仅正式员工可申请。
        formal_subject(db, twin_id)
        return twin_id
    subject = resolve_identity(db, twin_id)
    if subject is None or subject.employee_type != "twin":
        raise HTTPException(
            status_code=403,
            detail={"message": "策略拒绝", "policy_id": "ACCESS-TWIN-ONLY", "reason": "仅员工数字分身可申请插件"},
        )
    return twin_id


def create_plugin_request(
    db: Session, *, applicant_no: str, plugin: models.Plugin, reason: str,
) -> models.AccessRequest:
    """提交插件使用申请；同一插件已有 pending 申请时 409（幂等）。"""
    duplicate = db.scalar(select(models.AccessRequest).where(
        models.AccessRequest.applicant_no == applicant_no,
        models.AccessRequest.resource_type == "plugin",
        models.AccessRequest.resource_id == plugin.id,
        models.AccessRequest.status == "pending",
    ))
    if duplicate is not None:
        raise HTTPException(
            status_code=409,
            detail={"message": "相同资源已有待审批申请", "request_id": duplicate.id},
        )
    request = models.AccessRequest(
        applicant_no=applicant_no, resource_type="plugin", resource_id=plugin.id,
        reason=(reason or "")[:500], status="pending", approval_chain=[],
    )
    db.add(request)
    db.commit()
    db.refresh(request)
    write_audit(
        db, trace_id=f"ARQ-{request.id}", employee_id=applicant_no, plugin_id=plugin.id,
        action="access_apply", decision="pending", reason=f"plugin:{plugin.id}",
        result_summary="status=pending",
    )
    return request


def _upsert_employee_grant(db: Session, employee_id: str, plugin_id: str) -> None:
    grant = db.scalar(select(models.EmployeePluginGrant).where(
        models.EmployeePluginGrant.employee_id == employee_id,
        models.EmployeePluginGrant.plugin_id == plugin_id,
        models.EmployeePluginGrant.action == "read",
    ))
    if grant is None:
        db.add(models.EmployeePluginGrant(
            employee_id=employee_id, plugin_id=plugin_id, action="read",
            decision_mode=DECISION_ALLOW, grant_source="whitelist",
        ))
        return
    grant.decision_mode = DECISION_ALLOW
    grant.grant_source = "whitelist"


def grant_access(db: Session, request: models.AccessRequest) -> tuple[str, str | None]:
    """审批通过后的授权写入；返回 ``(plugin_id, knowledge_base_id)`` 供审计。

    幂等：重复执行只会把绑定/白名单改回允许态，不产生重复行。
    """
    if request.resource_type == "plugin":
        plugin = db.get(models.Plugin, request.resource_id)
        if plugin is None:
            raise HTTPException(status_code=404, detail={"message": "插件不存在", "resource_id": request.resource_id})
        target_agent_id = request.applicant_no
        if plugin.scope == "personal" and plugin.owner_human_no:
            if target_agent_id != f"DT-{plugin.owner_human_no}":
                raise HTTPException(status_code=409, detail="个人插件只能授权给属主本人的数字分身")
        binding = db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin.id,
            models.AgentPluginBinding.target_agent_id == target_agent_id,
        ))
        authorized_by = f"access-request:{request.id}"
        if binding is None:
            db.add(models.AgentPluginBinding(
                plugin_id=plugin.id, target_agent_id=target_agent_id, authorized_by=authorized_by,
                admin_enabled=True, employee_enabled=True, decision_mode=DECISION_ALLOW,
            ))
        else:
            # 保留管理端已配置的 priority / pinned_version，只把授权态改回允许。
            binding.admin_enabled = True
            binding.employee_enabled = True
            binding.decision_mode = DECISION_ALLOW
            binding.authorized_by = authorized_by
        if plugin.data_level == "L3":
            _upsert_employee_grant(db, target_agent_id, plugin.id)
        if plugin.plugin_type == "skill":
            # 技能审批通过即把技能包（SKILL.md + scripts）增量投放进该员工的
            # /workspace/skills/<slug>/，否则「已授权」只是台账上的状态、脚本跑不起来。
            # 投放是文件复制（容器 workspace 是 bind mount），失败只记日志不阻断审批。
            from . import runtime_manager

            try:
                runtime_manager.deploy_skills(db, target_agent_id)
            except Exception as exc:  # noqa: BLE001
                logger.warning("技能包投放失败 agent=%s plugin=%s: %s", target_agent_id, plugin.id, exc)
        return plugin.id, None

    kb = resolve_kb(db, request.resource_id)
    if kb is None:
        raise HTTPException(
            status_code=404,
            detail={"message": "知识库资源不存在", "resource_id": request.resource_id},
        )
    # 知识库的真实授权是 AgentKnowledgeGrant（gateway.search_knowledge 读它），
    # 审批通过必须写这张表，否则「批了但读不到」。写入前先做独占冲突校验：
    # 冲突时抛 409 且不改任何数据（审批界面据此说明被谁占用）。
    from . import knowledge_grant

    try:
        knowledge_grant.grant_knowledge_base(
            db, employee_id=request.applicant_no, knowledge_base_id=kb.id,
        )
    except knowledge_grant.KnowledgeGrantConflict as exc:
        raise HTTPException(status_code=409, detail={
            "message": str(exc), "reason": "knowledge_grant_conflict", "conflicts": exc.conflicts,
        }) from exc
    except knowledge_grant.KnowledgeGrantInvalid as exc:
        raise HTTPException(status_code=422, detail={"message": str(exc), "reason": "knowledge_grant_invalid"}) from exc

    plugin_id = plugin_id_for_level(kb.data_level)
    # 历史口径保留：L3 知识库同时补一条只读白名单，供 policy 的 L3 read 分支使用。
    if kb.data_level == "L3":
        _upsert_employee_grant(db, request.applicant_no, plugin_id)
    return plugin_id, kb.id


def _audit_refs(db: Session, request: models.AccessRequest) -> tuple[str, str | None]:
    """拒绝分支的审计引用：只做尽力解析，绝不因为资源被删而阻断审批。"""
    if request.resource_type == "plugin":
        return request.resource_id, None
    kb = resolve_kb(db, request.resource_id)
    if kb is None:
        return request.resource_id, None
    return plugin_id_for_level(kb.data_level), kb.id


def decide_access_request(
    db: Session, request: models.AccessRequest, *, approve: bool, decided_by: str, note: str = "",
) -> models.AccessRequest:
    """审批申请单：写终态 + 授权（仅通过）+ 审计。终态不可重复审批。"""
    if request.status in TERMINAL_STATUSES:
        raise HTTPException(
            status_code=409,
            detail={"message": "申请单已终态，不可重复审批", "status": request.status},
        )
    plugin_id, kb_id = grant_access(db, request) if approve else _audit_refs(db, request)
    trace_id = f"ARQ-{request.id}"
    decision_entry: dict[str, str] = {
        "actor_no": decided_by,
        "decision": "approve" if approve else "reject",
    }
    if note.strip():
        decision_entry["note"] = note.strip()[:200]
    request.decided_by = decided_by
    request.decided_at = datetime.now()
    request.approval_chain = [decision_entry]
    request.status = "granted" if approve else "rejected"
    db.commit()
    db.refresh(request)

    write_audit(
        db, trace_id=trace_id, employee_id=request.applicant_no,
        plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_approve",
        decision=DECISION_ALLOW if approve else DECISION_DENY,
        reason=f"{'审批通过' if approve else '审批拒绝'} by {decided_by}",
        result_summary=f"status={request.status}",
    )
    if approve:
        write_audit(
            db, trace_id=trace_id, employee_id=request.applicant_no,
            plugin_id=plugin_id, knowledge_base_id=kb_id, action="access_grant",
            decision=DECISION_ALLOW,
            reason=(
                "插件使用授权写入（AgentPluginBinding）" if request.resource_type == "plugin"
                else "L3 读取白名单授权写入"
            ),
            result_summary=(
                "decision_mode=allow, admin_enabled=true, employee_enabled=true"
                if request.resource_type == "plugin"
                else "action=read, grant_source=whitelist"
            ),
        )
    return request
