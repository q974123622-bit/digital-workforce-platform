"""Full knowledge-agent loop executed inside one employee's Harness container."""

from __future__ import annotations

import json
import threading
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config, execution_events, runtime_manager
from .chat import ChatResult, ToolCard, knowledge_display_name
from .harness_token import issue_token
from .identity import resolve_identity
from .knowledge_registry import accessible_knowledge_bases
from .memory_service import five_complete_rounds
from .plugin_governance import effective_plugins
from .runtime_adapter import DockerHarnessRuntimeAdapter

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def _lock(employee_id: str) -> threading.Lock:
    with _LOCKS_GUARD:
        return _LOCKS.setdefault(employee_id, threading.Lock())


# 技能前置路由关键词：任务命中时直接把对应技能的完整 SOP 注入任务上下文。
SKILL_ROUTE_KEYWORDS: dict[str, list[str]] = {
    "市场早报": ["早报", "盘前", "收盘", "行情速报", "市场热点", "基金经理观点"],
    "基金筛选": ["筛选", "选基", "选基金", "挑基金", "推荐基金", "回撤", "偏股", "股票基金", "指数基金", "债基"],
    "组合诊断": ["组合", "持仓", "诊断", "回测"],
    "基金分析": ["分析基金", "业绩", "评价"],
    "财富规划": ["财富规划", "资产配置", "理财规划", "现金流"],
    "目标测算": ["目标测算", "攒", "年化", "月投", "终值", "达成率", "缺口"],
    "目标匹配": ["目标匹配", "优先级"],
    "财务体检": ["体检", "财务健康", "收支", "负债"],
    "财富报告": ["财富报告", "资产报告", "持仓报告"],
    "投顾内容": ["投顾", "观点", "文案", "营销", "选题"],
    "数据可视化": ["图表", "可视化", "画图", "数据图", "长图"],
    # 内部 Skill（真人分身沉淀的个人 SOP）
    "报销制度速答": ["报销", "差旅", "住宿", "出差", "发票"],
    "券商术语速查": ["融资融券", "期权", "科创板", "术语"],
    "会议纪要规范": ["周会", "会议纪要", "纪要"],
    "IT 办公技巧": ["VPN", "邮箱", "企业微信", "办公"],
    "入职准备清单": ["入职", "准备材料", "报到"],
}


def _route_skill_content(db: Session, employee_id: str, message: str) -> dict | None:
    """按关键词规则将任务路由到已授权技能，返回 {name, content} 或 None。"""
    skills: list[dict] = []
    for skill in db.query(models.Skill).filter(models.Skill.status == "active").all():
        plugin_id = f"SKILL-{skill.id}"
        bound = db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == plugin_id,
            models.AgentPluginBinding.target_agent_id == employee_id,
            models.AgentPluginBinding.admin_enabled.is_(True),
            models.AgentPluginBinding.employee_enabled.is_(True),
            models.AgentPluginBinding.decision_mode == "allow",
        ))
        if bound is not None:
            skills.append({"name": skill.name, "description": skill.description, "content": skill.content})
    if not skills:
        return None
    best, best_score = None, 0
    message_lower = message.lower()
    for skill in skills:
        keywords = SKILL_ROUTE_KEYWORDS.get(skill["name"], [skill["name"]])
        score = sum(1 for kw in keywords if kw.lower() in message_lower)
        if score > best_score:
            best, best_score = skill, score
    return best if best_score > 0 else None


def _prompt(
    *, employee: models.DigitalEmployee, profile: models.AgentProfile, requester: str,
    message: str, history: list[dict], knowledge: list[dict], depth: int,
    plugins: list[dict], routed_skill: dict | None = None,
) -> str:
    can_delegate = profile.identity_kind == "human_twin" and depth == 0
    authorized = [{"id": kb["id"], "name": kb["name"]} for kb in knowledge]
    # Skill 通过前置路由（task_playbook）注入完整 SOP，不再出现在 enabled_plugins 清单。
    enabled = [p for p in plugins if p.get("plugin_type") != "skill"]
    envelope = {
        "identity": {
            "employee_id": employee.employee_no,
            "name": employee.name,
            "kind": profile.identity_kind,
            "persona": employee.role_prompt,
            "responsibilities": profile.responsibilities,
        },
        "requester_human_no": requester,
        "authorized_knowledge_bases": authorized,
        "recent_conversation": five_complete_rounds(history),
        "enabled_plugins": enabled,
        "goal": message,
        "limits": {
            "knowledge_searches": 5,
            "delegation_depth": depth,
            "may_delegate_once": can_delegate,
            "delegate_only_to_role_employee": True,
        },
    }
    if routed_skill:
        # 前置路由命中：把完整 SOP 直接注入任务上下文，DSH 按 SOP 执行。
        envelope["task_playbook"] = {
            "skill_name": routed_skill["name"],
            "sop": routed_skill["content"],
        }
        playbook_lead = (
            f"本任务已匹配技能「{routed_skill['name']}」，其完整 SOP 见 TASK_ENVELOPE.task_playbook.sop，"
            "请严格按其步骤、工具编排与输出规范执行，不要偏离 SOP 自行发挥。"
        )
    else:
        playbook_lead = ""
    return (
        "你是企业内部的真实数字同事，请在本轮内自主完成任务。先理解问题，再判断是否需要检索知识库；"
        "证据不足时可顺序检索其他已授权知识库，但最多五次。只能使用工具目录中出现的工具，"
        "不得猜测知识库内容。数字分身可向一名岗位数字员工委派一次；岗位数字员工不得委派。"
        "检索知识库必须调用工具目录中名为 search_knowledge 的工具；"
        "knowledge_base_id 参数必须原样复制 authorized_knowledge_bases 中某一项的 id 值，"
        "禁止自行编造、猜测，或用知识库名称代替 id。"
        + playbook_lead +
        "工具失败、无权限或证据不足时必须明确说明。最终直接给用户一份简洁、可核验的中文答复，"
        "不要输出内部思考过程、令牌或配置。\n\nTASK_ENVELOPE:\n"
        + json.dumps(envelope, ensure_ascii=False)
    )


def run_agent(
    db: Session,
    *,
    employee_id: str,
    requester_human_no: str,
    message: str,
    history: list[dict] | None = None,
    trace_id: str | None = None,
    depth: int = 0,
) -> ChatResult:
    employee = db.get(models.DigitalEmployee, employee_id)
    profile = db.get(models.AgentProfile, employee_id)
    subject = resolve_identity(db, employee_id)
    if employee is None or profile is None or subject is None or employee.status != "active":
        raise HTTPException(status_code=404, detail="数字员工不存在或未启用")
    if depth > 0 and profile.identity_kind != "role_employee":
        raise HTTPException(status_code=403, detail="委派目标必须是岗位数字员工")

    task_trace = trace_id or f"T-HAR-{uuid4().hex[:16].upper()}"
    execution = execution_events.execution_for_trace(db, task_trace)
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="agent_started", stage="harness_started",
            status="running", actor_employee_id=employee_id,
            title=f"{employee.name} 已进入 Harness 执行环境",
            detail="正在根据职责和授权能力规划执行步骤",
        )
    knowledge = accessible_knowledge_bases(db, subject, requester_human_no)
    plugins = effective_plugins(db, employee_id, requester_human_no)
    routed_skill = _route_skill_content(db, employee_id, message)
    if routed_skill is not None and execution is not None:
        # 前置路由命中：在轨迹中显式记录技能来源，保证可审计（SOP 已注入任务上下文）。
        execution_events.emit(
            db, execution.id, event_type="skill_routed", stage="harness_started",
            status="running", actor_employee_id=employee_id,
            title=f"已匹配技能「{routed_skill['name']}」",
            detail="SOP 已注入任务上下文，正在按其步骤与工具编排执行",
        )
    task = _prompt(
        employee=employee, profile=profile, requester=requester_human_no,
        message=message, history=history or [], knowledge=knowledge, depth=depth,
        plugins=plugins, routed_skill=routed_skill,
    )
    token = issue_token(
        employee_id=employee_id,
        requester_human_no=requester_human_no,
        trace_id=task_trace,
        depth=depth,
        ttl_seconds=300,
    )
    base_url = config.get("DWP_PLATFORM_TOOL_BASE_URL", "http://host.docker.internal:8000") or ""
    timeout = (
        config.harness_timeout_role()
        if profile.identity_kind == "role_employee"
        else config.harness_timeout_twin()
    )
    employee_lock = _lock(employee_id)
    if not employee_lock.acquire(blocking=False):
        raise HTTPException(status_code=409, detail={"message": "数字员工正忙，请稍后重试", "retryable": True})
    try:
        runtime_manager.mark_busy(db, employee_id)
        result = DockerHarnessRuntimeAdapter(timeout=timeout).run(
            employee_id=employee_id,
            task_prompt=task,
            trace_id=task_trace,
            tool_token=token,
            tool_base_url=base_url,
        )
        if not result.ok:
            runtime_manager.mark_ready(db, employee_id, result.result or "Harness 执行失败")
            raise HTTPException(
                status_code=503,
                detail={"message": result.result or "Harness 执行失败", "retryable": True},
            )
        runtime_manager.mark_ready(db, employee_id)
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="progress", stage="answer_preparing",
                status="running", actor_employee_id=employee_id,
                title="资料已整理，准备生成答复",
                detail="正在核对执行结果并整理为可读答案",
            )
    finally:
        employee_lock.release()

    audits = db.scalars(
        select(models.AuditEvent)
        .where(models.AuditEvent.trace_id == task_trace, models.AuditEvent.employee_id == employee_id)
        .order_by(models.AuditEvent.id)
    ).all()
    cards = []
    seen_cards: set[tuple[str, str | None]] = set()
    for row in audits:
        if row.action not in ("read", "execute"):
            continue
        source = None
        tool_name = None
        if row.result_summary:
            try:
                summary = json.loads(row.result_summary)
                if isinstance(summary, dict):
                    source = str(summary.get("source")) if summary.get("source") else None
                    tool_name = summary.get("tool")
            except (TypeError, ValueError, json.JSONDecodeError):
                source = None
        kb_id = row.knowledge_base_id or row.plugin_id or "工具"
        name = row.knowledge_base_id or tool_name or row.plugin_id or "工具"
        dedup_key = (kb_id, source)
        if dedup_key in seen_cards:
            continue
        seen_cards.add(dedup_key)
        cards.append(
            ToolCard(
                plugin_id=row.plugin_id or "knowledge",
                name=knowledge_display_name(db, row.knowledge_base_id) if row.knowledge_base_id else name,
                decision=row.decision,
                source=source,
                reason=row.reason,
            )
        )
    return ChatResult(
        session_id=f"H-{task_trace}", trace_id=task_trace,
        message=result.result, tool_cards=cards,
    )
