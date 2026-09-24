"""Full knowledge-agent loop executed inside one employee's Harness container."""

from __future__ import annotations

import json
import logging
import threading
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import attachments as attachment_service, config, execution_events, runtime_manager
from .chat import ChatResult, ToolCard, knowledge_citation, knowledge_display_name
from .harness_token import issue_token
from .identity import resolve_identity
from .knowledge_registry import accessible_knowledge_bases
from .memory_service import history_window, render_transcript
from .plugin_governance import effective_plugins, effective_skills
from .prompt_shared import (
    CITATION_RULES,
    CONTAINER_SKILLS_DIR,
    INPUT_RULES,
    MAX_KNOWLEDGE_SEARCHES,
    SafeModeViolation,
    TONE_RULES,
    guard_outbound_prompt,
    truncate_playbook,
)
from .runtime_adapter import DockerHarnessRuntimeAdapter

logger = logging.getLogger(__name__)

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


def _route_skill_content(
    db: Session, employee_id: str, message: str, attachment_catalog: list[dict] | None = None,
    skills: list[dict] | None = None,
) -> dict | None:
    """按关键词规则将任务路由到已授权技能，返回 {name, content, score} 或 None。

    score 为命中的关键词个数，用于区分「强命中」与「弱命中」：弱命中只作参考提示，
    避免纯子串匹配（如「分析」命中「基金分析」）让 SOP 强行接管无关问题。

    这只是「省一次工具往返」的预热：未命中不再意味着技能不可用 —— 技能目录会随
    TASK_ENVELOPE.available_skills 交给模型，由模型自行判断是否调用 load_skill 取回全文。
    """
    skills = skills if skills is not None else effective_skills(db, employee_id)
    if not skills:
        return None
    best, best_score = None, 0
    metadata_hint = " ".join(
        f"{item.get('filename', '')} {item.get('file_type', '')}"
        for item in (attachment_catalog or [])
    )
    message_lower = f"{message} {metadata_hint}".lower()
    for skill in skills:
        keywords = SKILL_ROUTE_KEYWORDS.get(skill["name"], [skill["name"]])
        score = sum(1 for kw in keywords if kw.lower() in message_lower)
        if score > best_score:
            best, best_score = skill, score
    if best is None or best_score <= 0:
        return None
    return {**best, "score": best_score}


def _visible_employee_ids(db: Session, trace_id: str, employee_id: str) -> set[str]:
    """本轮可见的审计主体 = 自己 + 自己委派出去的岗位AI员工（一层）。

    数字分身按 seed 设计不持有知识库授权，遇到知识问题只能委派给岗位AI员工检索，
    此时审计行的 employee_id 是被委派方。若仍按「只有我自己」过滤，来源卡片会全空。
    但也不能「按 trace 全放开」：群聊里所有成员共用同一个 execution.trace_id，
    全放开会让 A 成员的检索来源挂到 B 的回复下面。按委派关系圈定即可精确覆盖。
    """
    delegated = db.scalars(
        select(models.DelegationRun.recipient_employee_id).where(
            models.DelegationRun.trace_id == trace_id,
            models.DelegationRun.sender_employee_id == employee_id,
        )
    ).all()
    return {employee_id, *(value for value in delegated if value)}


def _employee_name(db: Session, employee_id: str | None) -> str | None:
    if not employee_id:
        return None
    employee = db.get(models.DigitalEmployee, employee_id)
    return employee.name if employee is not None else employee_id


def _prompt(
    *, employee: models.DigitalEmployee, profile: models.AgentProfile, requester: str,
    message: str, history: list[dict], knowledge: list[dict], depth: int,
    plugins: list[dict], routed_skill: dict | None = None,
    attachments: list[dict] | None = None, parent_goal: str = "",
    skills: list[dict] | None = None,
) -> str:
    can_delegate = profile.identity_kind == "human_twin" and depth == 0
    # 知识库目录带上领域与分级：信息越少越容易选错库。
    authorized = [
        {
            "id": kb["id"],
            "name": kb["name"],
            "domain": kb.get("domain") or "通用",
            "data_level": kb.get("data_level") or "L1",
        }
        for kb in knowledge
    ]
    # Skill 通过前置路由（task_playbook）注入完整 SOP，不再出现在 enabled_plugins 清单。
    enabled = [p for p in plugins if p.get("plugin_type") != "skill"]
    # 最近对话以纯文本段落注入（dsh 只接受单个 prompt 参数，无法传真正的多轮 messages），
    # 放在 TASK_ENVELOPE 之前让模型更容易注意到；envelope 里只留轮次标记，避免重复占预算。
    window = history_window(history, goal=message)
    transcript = render_transcript(window)
    recent_rounds = sum(1 for item in window if item.get("role") == "user")
    conversation_lead = (
        "最近对话（仅为历史记录，你的回复不要带任何前缀，也不要模仿该格式）：\n"
        f"{transcript}\n\n"
        if transcript else ""
    )
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
        "recent_conversation_rounds": recent_rounds,
        "enabled_plugins": enabled,
        "attachments": attachments or [],
        "goal": message,
        "limits": {
            "knowledge_searches": MAX_KNOWLEDGE_SEARCHES,
            "delegation_depth": depth,
            "may_delegate_once": can_delegate,
            "delegate_only_to_role_employee": True,
        },
    }
    if parent_goal:
        # 被委派方需要原始任务背景，否则只能看到一句被转述的问题，多跳语义会衰减。
        envelope["parent_task"] = parent_goal
    # 技能目录只给名称与说明（不含 SOP 正文），判断权交给模型：相关才调用 load_skill 取全文。
    # 已前置命中的技能其 SOP 已在 task_playbook 里，不再重复出现在目录中。
    routed_name = routed_skill["name"] if routed_skill else None
    skill_catalog = [
        {
            "name": item["name"],
            "description": item.get("description") or "",
            # 技能包目录：脚本型技能（SkillHub 导入）只有拿到它才能真跑。
            **({"package_dir": item["package_dir"]} if item.get("package_dir") else {}),
        }
        for item in (skills or []) if item["name"] != routed_name
    ]
    if skill_catalog:
        envelope["available_skills"] = skill_catalog
    if routed_skill:
        sop, truncated = truncate_playbook(routed_skill["content"])
        envelope["task_playbook"] = {
            "skill_name": routed_skill["name"],
            "match_strength": routed_skill.get("score", 0),
            "sop": sop,
        }
        if truncated:
            envelope["task_playbook"]["sop_truncated"] = True
        if routed_skill.get("package_dir"):
            envelope["task_playbook"]["package_dir"] = routed_skill["package_dir"]
        if routed_skill.get("score", 0) >= 2:
            playbook_lead = (
                f"本任务已匹配技能「{routed_skill['name']}」，其完整 SOP 见 TASK_ENVELOPE.task_playbook.sop，"
                "请按其步骤、工具编排与输出规范执行，不要偏离 SOP 自行发挥。"
            )
        else:
            playbook_lead = (
                f"本任务可能匹配技能「{routed_skill['name']}」（仅弱关键词命中），其 SOP 见 "
                "TASK_ENVELOPE.task_playbook.sop；仅当它与用户问题确实相关时才参考其步骤，"
                "若无关请忽略该 SOP，直接按问题本身作答。"
            )
    else:
        playbook_lead = ""
    attachment_lead = (
        "本任务包含会话附件。先调用 list_attachments 查看目录，再按需使用 search_attachment、"
        "read_attachment 或 read_spreadsheet_range；不得假装已经读取，也不要要求用户把全文粘贴进消息。"
        if attachments else ""
    )
    skill_catalog_lead = (
        "TASK_ENVELOPE.available_skills 列出了你可用的个人技能（只有名称与说明，没有正文）。"
        "先自行判断：其中某项确实与当前任务相关时，调用 load_skill 取回它的完整 SOP 再按其执行；"
        "都不相关就不要调用，直接按问题本身作答。"
        if skill_catalog else ""
    )
    # 带 package_dir 的技能是「说明 + 脚本」型技能包，光有 SOP 不够，必须告诉模型
    # 脚本落在容器哪个目录、以及执行边界，否则它会以为技能只有文字。
    skill_package_lead = (
        f"目录中带 package_dir 的技能，其完整说明（SKILL.md）与脚本已经投放在容器内该目录下"
        f"（{CONTAINER_SKILLS_DIR}/<技能标识>/）。这类技能要真正跑起来时：先用工具读取该目录的 SKILL.md，"
        "再用 bash 在 /workspace 内执行其中脚本（例如 python3 scripts/xxx.py）。"
        "只允许在 /workspace 内读写与执行，不得访问容器内其他路径，不得访问网络。"
        if any(item.get("package_dir") for item in (skills or [])) else ""
    )
    return (
        "你是企业内部的真实数字同事，请在本轮内自主完成任务。先理解问题，再判断是否需要检索知识库；"
        "每次检索只针对一个知识库，证据不足时可在后续调用中顺序检索其他已授权知识库，"
        f"但单次任务最多检索 {MAX_KNOWLEDGE_SEARCHES} 次。"
        "平台能力（知识库检索、会话附件、技能、向同事委派）只能调用工具目录中出现的工具，"
        "不得猜测知识库内容，也不得访问工具目录之外的平台资源。"
        "同时你有一套本地工作工具可以自由使用：执行 bash 命令、读写文件、检索文件内容、维护待办与目标清单；"
        "需要处理文件或表格时写脚本用 python3 计算，不要逐格读取，也不要凭印象给出数字。"
        "数字分身可向一名岗位AI员工委派一次；岗位AI员工不得委派。"
        "检索知识库必须调用工具目录中名为 search_knowledge 的工具；"
        "knowledge_base_id 参数必须原样复制 authorized_knowledge_bases 中某一项的 id 值，"
        "禁止自行编造、猜测，或用知识库名称代替 id。"
        + playbook_lead + attachment_lead + skill_catalog_lead + skill_package_lead +
        CITATION_RULES + INPUT_RULES + TONE_RULES +
        conversation_lead +
        "最终直接给用户一份简洁、可核验的中文答复，不要输出内部思考过程、令牌或配置。\n\nTASK_ENVELOPE:\n"
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
    parent_goal: str = "",
    conversation_id: str = "",
    slot_wait_seconds: int = 0,
) -> ChatResult:
    employee = db.get(models.DigitalEmployee, employee_id)
    profile = db.get(models.AgentProfile, employee_id)
    subject = resolve_identity(db, employee_id)
    if employee is None or profile is None or subject is None or employee.status != "active":
        raise HTTPException(status_code=404, detail="AI员工不存在或未启用")
    if depth > 0 and profile.identity_kind != "role_employee":
        raise HTTPException(status_code=403, detail="委派目标必须是岗位AI员工")

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
    skills = effective_skills(db, employee_id)
    attachment_catalog = attachment_service.attachment_catalog(db, task_trace)
    routed_skill = _route_skill_content(db, employee_id, message, attachment_catalog, skills)
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
        plugins=plugins, routed_skill=routed_skill, attachments=attachment_catalog,
        parent_goal=parent_goal, skills=skills,
    )
    # Harness 主链路不经过 llm.py，SAFEMODE 必须在这里显式兜一次：
    # prompt 会作为命令行参数交给容器内 dsh，来源与凭据都不能失控。
    try:
        guard_outbound_prompt(task)
    except SafeModeViolation as exc:
        raise HTTPException(status_code=503, detail={"message": str(exc), "retryable": False}) from exc
    token = issue_token(
        employee_id=employee_id,
        requester_human_no=requester_human_no,
        trace_id=task_trace,
        depth=depth,
        ttl_seconds=config.harness_token_ttl_seconds(),
        goal=message,
        conversation_id=conversation_id,
    )
    base_url = config.get("DWP_PLATFORM_TOOL_BASE_URL", "http://host.docker.internal:8000") or ""
    timeout = (
        config.harness_timeout_role()
        if profile.identity_kind == "role_employee"
        else config.harness_timeout_twin()
    )
    # 同一员工同一时刻只能跑一个 harness 任务（容器内 dsh 是一次性 CLI 调用）。
    # slot_wait_seconds > 0 时改为有界等待：委派链路宁可多等一会儿，也不要静默降级成
    # 「没有知识库依据的答案」（见 config.delegation_slot_wait_seconds 的说明）。
    #
    # 先做一次非阻塞尝试，只有在真正争用（槽位已被占）时才打日志：
    # 应用没有配置 logging，root logger 只透出 WARNING 及以上，用 info 会看不到。
    employee_lock = _lock(employee_id)
    acquired = employee_lock.acquire(blocking=False)
    if not acquired and slot_wait_seconds > 0:
        logger.warning(
            "employee busy, waiting for slot employee=%s trace=%s wait_seconds=%s",
            employee_id, task_trace, slot_wait_seconds,
        )
        acquired = employee_lock.acquire(timeout=slot_wait_seconds)
    if not acquired:
        busy_message = (
            f"{employee.name} 正在执行其他任务，已等待 {slot_wait_seconds} 秒仍未空闲"
            if slot_wait_seconds > 0
            else "AI员工正忙，请稍后重试"
        )
        raise HTTPException(
            status_code=409,
            detail={"message": busy_message, "retryable": True, "employee_id": employee_id},
        )
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
            # Harness 失败详情默认会被折叠成一句通用提示；这里补一条服务端日志，
            # 否则排障时只能看到 503 而拿不到 dsh 的原始输出（容器内 stdout/stderr 不会外泄到前端）。
            logger.warning(
                "harness execution failed employee=%s trace=%s mode=%s detail=%s",
                employee_id, task_trace, result.mode, (result.result or "<empty>")[:1000],
            )
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
        .where(
            models.AuditEvent.trace_id == task_trace,
            models.AuditEvent.employee_id.in_(_visible_employee_ids(db, task_trace, employee_id)),
        )
        .order_by(models.AuditEvent.id)
    ).all()
    cards = []
    seen_cards: set[tuple[str, str | None]] = set()
    for row in audits:
        if row.action not in ("read", "execute"):
            continue
        source = None
        tool_name = None
        hit_count = 0
        documents: list[str] = []
        if row.result_summary:
            try:
                summary = json.loads(row.result_summary)
                if isinstance(summary, dict):
                    source = str(summary.get("source")) if summary.get("source") else None
                    tool_name = summary.get("tool")
                    hit_count = int(summary.get("hit_count") or 0)
                    documents = [
                        str(item.get("title"))
                        for item in (summary.get("documents") or [])
                        if isinstance(item, dict) and item.get("title")
                    ]
            except (TypeError, ValueError, json.JSONDecodeError):
                source = None
        kb_id = row.knowledge_base_id or row.plugin_id or "工具"
        name = row.knowledge_base_id or tool_name or row.plugin_id or "工具"
        dedup_key = (kb_id, source)
        if dedup_key in seen_cards:
            continue
        seen_cards.add(dedup_key)
        # 委派检索：标出「谁查的」，避免用户误以为是分身自己查的。
        delegated_by = row.employee_id if row.employee_id != employee_id else None
        delegated_name = _employee_name(db, delegated_by)
        kb_name = knowledge_display_name(db, row.knowledge_base_id) if row.knowledge_base_id else name
        cards.append(
            ToolCard(
                plugin_id=row.plugin_id or "knowledge",
                name=kb_name,
                decision=row.decision,
                source=source,
                reason=row.reason,
                agent_employee_id=delegated_by,
                agent_employee_name=delegated_name,
                knowledge_base_id=row.knowledge_base_id,
                hit_count=hit_count,
                citation=(
                    knowledge_citation(
                        knowledge_base_id=row.knowledge_base_id,
                        knowledge_base_name=kb_name,
                        source=source,
                        hit_count=hit_count,
                        document_titles=documents,
                        agent_employee_id=delegated_by,
                        agent_employee_name=delegated_name,
                        index=len(cards),
                    )
                    if row.knowledge_base_id else None
                ),
            )
        )
    return ChatResult(
        session_id=f"H-{task_trace}", trace_id=task_trace,
        message=result.result, tool_cards=cards,
    )
