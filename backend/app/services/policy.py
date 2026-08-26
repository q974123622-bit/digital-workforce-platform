"""Policy Engine — 唯一授权源（Sprint 2）。

评估维度：subject / resource / action / environment。
效果：allow | deny | approval。决策优先级 Deny > Approval > Allow；未授权默认拒绝。

内置规则 ID 与 mock-data/seed.json 的 policy 记录保持一致（种子用于展示与审计引用）。
- POLICY-001  正式员工数字分身可访问内部知识库（L2）
- POLICY-002  实习生数字分身访问内部知识库 DENY
- POLICY-003  禁网（internet=deny）员工调用公网插件 DENY
- POLICY-004  仅远程（location=remote）员工请求本地执行 DENY
- POLICY-005  敏感操作（L3 执行类）APPROVAL
- P-DATA-003  L3 读取仅允许 whitelist grant
- P-PLUGIN-007 VE-0001 可执行 adp-onboarding
- P-DEFAULT-001 默认 L1 可读
"""

from dataclasses import dataclass, field
from typing import Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from .identity import EmployeeIdentity

DECISION_ALLOW = "allow"
DECISION_DENY = "deny"
DECISION_APPROVAL = "approval"

# 插件类资源（需检查 employee_plugin_grant）；sandbox 等执行资源只走规则
PLUGIN_RESOURCE_TYPES = {"knowledge", "mcp", "workflow", "rpa", "http"}


@dataclass(frozen=True)
class ResourceRef:
    type: str
    id: str
    data_level: str = "L1"


@dataclass(frozen=True)
class EvaluationResult:
    decision: str
    policy_id: str | None
    reason: str


@dataclass(frozen=True)
class Rule:
    policy_id: str
    effect: str
    priority: int
    reason: str
    condition: Callable[[EmployeeIdentity, ResourceRef, str], bool] = field(repr=False)


def _is_internal_kb(resource: ResourceRef) -> bool:
    return resource.type == "knowledge" and resource.data_level == "L2"


RULES: list[Rule] = [
    # POLICY-003 禁网调公网插件
    Rule(
        "POLICY-003", DECISION_DENY, 100,
        "禁网员工禁止调用公网插件",
        lambda s, r, a: s.internet == "deny" and r.type == "http",
    ),
    # POLICY-004 仅远程请求本地执行
    Rule(
        "POLICY-004", DECISION_DENY, 80,
        "仅远程 Sandbox 禁止本地执行",
        lambda s, r, a: s.location == "remote" and r.type == "sandbox" and r.id == "local",
    ),
    # SANDBOX-POLICY-001 远程 Sandbox 允许远程执行
    Rule(
        "SANDBOX-POLICY-001", DECISION_ALLOW, 75,
        "远程 Sandbox 允许远程执行",
        lambda s, r, a: r.type == "sandbox" and r.id == "remote" and s.location == "remote",
    ),
    # POLICY-HARNESS-001 远程数字员工允许调用 DeepSeek Harness 执行引擎
    Rule(
        "POLICY-HARNESS-001", DECISION_ALLOW, 72,
        "远程执行允许调用 DeepSeek Harness",
        lambda s, r, a: r.type == "runtime" and r.id == "harness" and a == "execute" and s.location == "remote",
    ),
    # POLICY-002 实习生分身禁止内部知识
    Rule(
        "POLICY-002", DECISION_DENY, 70,
        "实习生数字分身禁止访问内部知识库",
        lambda s, r, a: s.employee_type == "twin" and s.employment_type == "intern" and _is_internal_kb(r),
    ),
    # POLICY-001 正式员工数字分身可访问内部知识库
    Rule(
        "POLICY-001", DECISION_ALLOW, 60,
        "正式员工数字分身可访问内部知识库",
        lambda s, r, a: s.employee_type == "twin" and s.employment_type == "formal" and _is_internal_kb(r),
    ),
    # POLICY-005 敏感操作审批（L3 执行类；白名单只覆盖 L3 读取，不得绕过人工审批）
    Rule(
        "POLICY-005", DECISION_APPROVAL, 50,
        "敏感操作需要人工审批",
        lambda s, r, a: r.data_level == "L3" and a in ("execute", "export", "delete", "approve"),
    ),
    # P-PLUGIN-007 VE-0001 可执行 ADP
    Rule(
        "P-PLUGIN-007", DECISION_ALLOW, 10,
        "入职助手可用 ADP 入职流程",
        lambda s, r, a: s.employee_id == "VE-0001" and r.id == "adp-onboarding" and a == "execute",
    ),
    # P-DEFAULT-001 默认 L1 可读
    Rule(
        "P-DEFAULT-001", DECISION_ALLOW, 1,
        "所有数字员工可读取 L1 知识",
        lambda s, r, a: r.type == "knowledge" and r.data_level == "L1" and a == "read",
    ),
]


def _best(matched: list[Rule]) -> Rule:
    # 同优先级下 Deny > Approval > Allow（规则匹配阶段已按 effect 分组，这里按优先级取最高）
    return max(matched, key=lambda r: r.priority)


def evaluate(
    db: Session,
    subject: EmployeeIdentity,
    resource: ResourceRef,
    action: str,
    context: dict | None = None,
) -> EvaluationResult:
    """四维评估：subject / resource / action / environment（environment 取自 subject 绑定配置）。"""
    # L3 读取使用显式白名单，且只认访问审批链写入的 whitelist grant。
    # L3 执行类动作继续由 POLICY-005 控制，白名单不得绕过人工审批。
    if resource.data_level == "L3" and action == "read":
        whitelist = db.scalar(
            select(models.EmployeePluginGrant).where(
                models.EmployeePluginGrant.employee_id == subject.employee_id,
                models.EmployeePluginGrant.plugin_id == resource.id,
                models.EmployeePluginGrant.action == "read",
                models.EmployeePluginGrant.decision_mode == DECISION_ALLOW,
                models.EmployeePluginGrant.grant_source == "whitelist",
            )
        )
        if whitelist is None:
            return EvaluationResult(DECISION_DENY, "P-DATA-003", "L3 敏感数据需先申请并通过白名单审批")
        return EvaluationResult(DECISION_ALLOW, "P-DATA-003", "L3 敏感数据白名单授权通过")

    matched = [r for r in RULES if r.condition(subject, resource, action)]

    deny_rules = [r for r in matched if r.effect == DECISION_DENY]
    if deny_rules:
        best = _best(deny_rules)
        return EvaluationResult(DECISION_DENY, best.policy_id, best.reason)

    # 插件授权检查（employee_plugin_grant）：无授权默认拒绝
    grant = None
    if resource.type in PLUGIN_RESOURCE_TYPES:
        grant = db.scalar(
            select(models.EmployeePluginGrant).where(
                models.EmployeePluginGrant.employee_id == subject.employee_id,
                models.EmployeePluginGrant.plugin_id == resource.id,
                models.EmployeePluginGrant.action == action,
            )
        )
        if grant is None:
            return EvaluationResult(DECISION_DENY, None, "未授权插件：默认拒绝")
        if grant.decision_mode == DECISION_DENY:
            return EvaluationResult(DECISION_DENY, None, f"插件授权为 deny：{resource.id}")

    approval_rules = [r for r in matched if r.effect == DECISION_APPROVAL]
    if approval_rules:
        best = _best(approval_rules)
        return EvaluationResult(DECISION_APPROVAL, best.policy_id, best.reason)
    if grant is not None and grant.decision_mode == DECISION_APPROVAL:
        return EvaluationResult(DECISION_APPROVAL, None, f"插件授权为 approval：{resource.id}")

    allow_rules = [r for r in matched if r.effect == DECISION_ALLOW]
    if allow_rules:
        best = _best(allow_rules)
        return EvaluationResult(DECISION_ALLOW, best.policy_id, best.reason)
    if grant is not None and grant.decision_mode == DECISION_ALLOW:
        return EvaluationResult(DECISION_ALLOW, None, f"插件授权为 allow：{resource.id}")

    return EvaluationResult(DECISION_DENY, None, "默认拒绝：无匹配策略")
