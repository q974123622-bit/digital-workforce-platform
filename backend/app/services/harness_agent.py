"""Full knowledge-agent loop executed inside one employee's Harness container."""

from __future__ import annotations

import json
import logging
import threading
import time
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import (
    attachments as attachment_service,
    config,
    execution_events,
    run_outputs,
    runtime_manager,
    workspace_readback,
)
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
    # 审核类技能：命中「材料/开户/核对」这类词时只做弱提示，核对范围仍由模型自己规划
    # 用户的口语常常一个业务词都没有（「审核一下这些材料」「信息是否准确」），
    # 所以除了业务词还要收裸词；真正的兜底是下面「有附件 + 只有一个技能」那条分支。
    "机构开户材料交叉核对": ["开户材料", "材料核对", "材料审核", "开户审核", "机构开户",
                            "营业执照", "开户资料",
                            "审核", "核对", "材料是否准确", "信息是否准确",
                            "是否一致", "是否出错", "材料信息"],
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
        # 兜底：有附件且该员工只有一个技能时直接注入 —— 只有一个技能就没有「选错技能」
        # 的风险，而用户口语（「审核一下这些材料」）经常一个关键词都命中不到。
        if attachment_catalog and len(skills) == 1:
            return {**skills[0], "score": 0, "reason": "sole_skill_with_attachment"}
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
    skills: list[dict] | None = None, materials: dict | None = None,
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
    # 材料清单直给：物化发生在构造 prompt 之前，代号 / 来源文件 / 提示业务号 / 转写行数
    # 服务端都已经算出来了，塞进信封模型就不必先跑一次 inspect 或 ls 清点 ——
    # 每省一次往返就省一次完整模型调用 + 全量上下文回灌。给信息，不是定流程。
    material_block = None
    if materials and materials.get("materials"):
        material_block = {
            "pack_id": materials.get("pack_id"),
            "layout": materials.get("layout") or "flat",
            "count": materials.get("materials"),
            "hint_note": ("hint_business_no 仅由文件名/目录名推测，仅供参考，可能与实际业务不符；"
                          "哪些材料属于同一笔业务请按材料内容自行判断。"),
            "catalog": materials.get("catalog") or [],
        }
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
    if material_block:
        envelope["materials"] = material_block
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
        # 技能说明给的是「目标 + 铁律 + 工具清单」，不是流水线：核什么、按什么顺序核、
        # 要不要深挖由模型按实际材料决定。这里刻意不写「按 SOP 执行、不要偏离」，
        # 那会把自主规划压回固定流程，技能包里的参考路径也只是参考。
        if routed_skill.get("score", 0) >= 2:
            playbook_lead = (
                f"本任务已匹配技能「{routed_skill['name']}」，其完整说明见 TASK_ENVELOPE.task_playbook.sop。"
                "这份说明给的是目标、合规铁律与可用工具；**具体怎么做、先看哪份材料、按什么顺序、"
                "发现异常往哪追，由你按实际情况自行规划**。说明里标注为「参考路径」的部分仅供参考，"
                "不必照搬；但铁律（只提示不决策、不猜、判定走脚本、敏感信息不外发）必须遵守。"
            )
        else:
            playbook_lead = (
                f"本任务可能匹配技能「{routed_skill['name']}」（仅弱关键词命中），其说明见 "
                "TASK_ENVELOPE.task_playbook.sop；仅当它与用户问题确实相关时才参考，"
                "若无关请忽略，直接按问题本身作答。"
            )
    else:
        playbook_lead = ""
    # 材料包附件已物化到 /workspace：图片与扫描页在上传阶段就转成了文本，
    # 这里要明确告诉模型「去哪里读、材料类型要自己判」，否则它会去调 read_attachment 逐个翻。
    pack_items = [item for item in (attachments or []) if item.get("pack_id")]
    materials_ready = bool(pack_items and (materials or {}).get("materials"))
    if materials_ready:
        # 已物化就别再让它逐个翻附件：每多一次往返就是一次完整模型调用 + 全量上下文回灌。
        attachment_lead = (
            "本任务包含会话附件，附件已在上传阶段完成转写并物化到工作区（见下文），"
            "**不要再调用 list_attachments / read_attachment 逐个翻附件**。"
        )
    else:
        attachment_lead = (
            "本任务包含会话附件。先调用 list_attachments 查看目录，再按需使用 search_attachment、"
            "read_attachment 或 read_spreadsheet_range；不得假装已经读取，也不要要求用户把全文粘贴进消息。"
            if attachments else ""
        )
    if pack_items:
        layout = (materials or {}).get("layout") or "flat"
        where = ("/workspace/materials/<材料包>/（平铺，一层目录）" if layout == "flat"
                 else "/workspace/materials/<材料包>/<业务号>/")
        materials_lead = (
            f"本次上传的是材料包，已物化到容器内 {where}：每份材料一个 JSON，"
            "事实层是 text_lines（上传阶段已完成转写），materials.json 是材料清单。"
            "**材料类型没有预设**：技能脚本会按文件名/转写文本先给一个判断并写回材料文件，"
            "你只需在明显判错时纠正（不要为了判类型逐份读材料）。"
            "**字段取值也已在物化时自动抽好**（含系统录入侧截图的基准方取值）——"
            "不要再为「先跑一遍 extract」花往返；发现某份材料抽错了，用技能脚本的 "
            "extract 子命令针对那一份重抽即可。"
            "材料清单（材料代号 / 来源文件 / 提示业务号 / 转写行数）已随任务给出，"
            "见 TASK_ENVELOPE.materials.catalog —— 直接开始核对，不要再花往返去清点目录；"
            "核对范围与顺序仍由你决定。不要在容器外重复做转写。"
        )
        if routed_skill:
            # 技能脚本的一条式命令就是完整流水线；模型若再逐份读材料、重写比对脚本，
            # 只是把一轮拖成几十次往返（真机实测 39 次工具调用 / 310 秒）。
            materials_lead += (
                "已匹配技能时，技能脚本的一条式命令（见 SKILL.md「参考路径」）本身就是完整流水线："
                "跑一次即可拿到结论与核对表，**不要逐份读材料 JSON、不要自己重写比对脚本、"
                "不要重复跑同一个子命令**；产物写到独立输出目录，不要写回材料包目录（会覆盖材料清单）。"
                "脚本退出码为 0 时，本轮剩下的工作只有「读核对表 + 写答复」；"
                "核对表默认同时产出 Markdown 与可打印的 HTML 两份，都要留在输出目录"
                "（平台会把它们一并收成可预览 / 可下载的产物卡片），答复末尾用一句话交代这一点。"
                "答复控制在 800 字以内 —— 逐条抄表既拖慢这一轮，也没人读得完。"
            )
    else:
        materials_lead = ""
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
        + playbook_lead + attachment_lead + materials_lead + skill_catalog_lead + skill_package_lead +
        CITATION_RULES + INPUT_RULES + TONE_RULES +
        conversation_lead +
        "最终直接给用户一份简洁、可核验的中文答复，不要输出内部思考过程、令牌或配置。"
        "如果你把产物写进了文件，结论也要在答复正文里给出 —— 用户只看得到答复，"
        "看不到工作区文件；只写文件不给答复等于没交付。\n\nTASK_ENVELOPE:\n"
        + json.dumps(envelope, ensure_ascii=False)
    )


def _format_stage_timing(started: float, prepared: float, harness_done: float) -> tuple[str, str, dict]:
    """把三段单调时钟差值压成一条轨迹事件的 (title, detail, payload)。

    只记秒数与毫秒数，不落任何材料内容或凭据。材料解析发生在**上传阶段**，
    不在这一轮里（附件转写完成才允许发起执行），所以这里只量三段：
    物化 + 构造任务、Harness 执行、答复整理。
    """
    now = time.monotonic()
    plan_ms = int(max(0.0, prepared - started) * 1000)
    harness_ms = int(max(0.0, harness_done - prepared) * 1000)
    answer_ms = int(max(0.0, now - harness_done) * 1000)
    total_ms = int(max(0.0, now - started) * 1000)
    total_seconds = total_ms / 1000.0
    if total_seconds >= 60:
        title = "本轮耗时 %d 分 %d 秒" % (total_seconds // 60, round(total_seconds % 60))
    else:
        title = "本轮耗时 %d 秒" % round(total_seconds)
    detail = ("材料准备 %.1f 秒 · 规划执行 %.1f 秒 · 答复整理 %.1f 秒"
              % (plan_ms / 1000.0, harness_ms / 1000.0, answer_ms / 1000.0))
    payload = {
        "plan_ms": plan_ms, "harness_ms": harness_ms,
        "answer_ms": answer_ms, "total_ms": total_ms,
    }
    return title, detail, payload


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
    # 分段打点的起点：从这里到「任务构造完成」= 材料准备段。
    stage_started = time.monotonic()
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
    # 材料包：把每份材料的转写文本物化进目标员工的 /workspace/materials/<包>/<业务号>/。
    # 物化放在发起执行时（不是上传时）：上传时还不知道目标员工是谁，而 workspace 是按员工分的。
    # 有附件就物化（不限材料包）：零散上传的文件同样要进 /workspace/materials，
    # 否则技能脚本 --materials 无处可指，模型只能回到逐个 read_attachment。
    materialized: dict = {}
    if attachment_catalog:
        try:
            materialized = attachment_service.materialize_materials(
                db, employee_id=employee_id, trace_id=task_trace)
        except (OSError, ValueError) as exc:  # noqa: BLE001 - 物化失败不阻断执行，让模型按附件工具走
            materialized = {}
            logger.warning("材料物化失败 employee=%s trace=%s: %s", employee_id, task_trace, exc)
        if execution is not None and materialized.get("materials"):
            extracted = sum(item.get("extracted") or 0
                            for report in (materialized.get("extraction") or {}).values()
                            for item in (report.get("by_material") or {}).values())
            detail = ("提示业务号：%s；材料已平铺到 /workspace/materials（分笔由 AI 员工判断）"
                      % "、".join(materialized.get("businesses") or []))
            if extracted:
                detail += "；已自动抽取 %d 个字段取值（含系统录入侧基准方）" % extracted
            execution_events.emit(
                db, execution.id, event_type="materials_ready", stage="harness_started",
                status="running", actor_employee_id=employee_id,
                title="材料已就绪 %d 份" % materialized["materials"],
                detail=detail,
            )
    routed_skill = _route_skill_content(db, employee_id, message, attachment_catalog, skills)
    if routed_skill is not None and execution is not None:
        # 前置路由命中：在轨迹中显式记录技能来源，保证可审计（说明已注入任务上下文）。
        execution_events.emit(
            db, execution.id, event_type="skill_routed", stage="harness_started",
            status="running", actor_employee_id=employee_id,
            title=f"已匹配技能「{routed_skill['name']}」",
            detail="技能说明已注入任务上下文，核对范围与顺序由 AI 员工自行规划",
        )
    task = _prompt(
        employee=employee, profile=profile, requester=requester_human_no,
        message=message, history=history or [], knowledge=knowledge, depth=depth,
        plugins=plugins, routed_skill=routed_skill, attachments=attachment_catalog,
        parent_goal=parent_goal, skills=skills, materials=materialized,
    )
    stage_prepared = time.monotonic()
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
        # 记下发起时刻：读回兜底只认这之后动过的文件，避免把上一轮的旧表当成本轮结论。
        started_at = workspace_readback.run_started_at()
        result = DockerHarnessRuntimeAdapter(timeout=timeout).run(
            employee_id=employee_id,
            task_prompt=task,
            trace_id=task_trace,
            tool_token=token,
            tool_base_url=base_url,
        )
        stage_harness_done = time.monotonic()
        answer = (result.result or "").strip()
        if result.empty_output:
            # `empty_output` 时 result 里放的是占位诊断文案（"Harness 未返回内容"），
            # 它不是答复：清掉才会走到下面的读回兜底。
            answer = ""
        # 只有「跑完了但没输出」才读回兜底（empty_output）：容器起不来、密钥缺失这类
        # 真失败不兜底，照旧把原因抛给用户。
        if not result.ok and not result.empty_output:
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
        # 没拿到答复时读回工作区产物：模型可能把预算花在工具调用上、或者进程被终止，
        # 但它在 /workspace 里写出的核对表是产物，读回来比报「没有内容」有用。
        if not answer:
            artifacts = workspace_readback.collect(employee_id, since=started_at)
            readback = workspace_readback.render_artifacts(
                artifacts, reason="进程没有输出" if result.empty_output else "")
            if readback:
                answer = readback
                logger.warning(
                    "harness returned no answer, read back workspace artifacts "
                    "employee=%s trace=%s files=%s",
                    employee_id, task_trace, [item["name"] for item in artifacts],
                )
                if execution is not None:
                    execution_events.emit(
                        db, execution.id, event_type="answer_from_workspace",
                        stage="answer_preparing", status="running",
                        actor_employee_id=employee_id,
                        title="未收到最终答复，已读回工作区产物",
                        detail=workspace_readback.summarize(artifacts),
                    )
        if not answer:
            detail_text = result.result or "Harness 未返回内容，工作区也没有可读回的产物"
            logger.warning(
                "harness execution failed employee=%s trace=%s mode=%s detail=%s",
                employee_id, task_trace, result.mode, detail_text[:1000],
            )
            runtime_manager.mark_ready(db, employee_id, detail_text)
            raise HTTPException(
                status_code=503,
                detail={"message": detail_text, "retryable": True},
            )
        runtime_manager.mark_ready(db, employee_id)
        if execution is not None:
            # 快照本轮工作区产物（核对表等）。模型在答复里只会写「已保存至
            # /workspace/output/核对表.md」，用户看不到也拿不到那个文件 ——
            # 快照下来 + 一条带 payload 的事件，前端就能渲染成可预览/可下载的卡片。
            # 失败不影响执行链路（答复照常返回，只是少一个下载入口）。
            try:
                artifacts = run_outputs.snapshot_run(
                    db, execution, employee_id, since=started_at)
            except Exception as exc:  # noqa: BLE001
                artifacts = []
                logger.warning("产物快照失败 employee=%s trace=%s: %s",
                               employee_id, task_trace, exc)
            if artifacts:
                execution_events.emit(
                    db, execution.id, event_type="artifact", stage="answer_preparing",
                    status="running", actor_employee_id=employee_id,
                    title="已生成 %d 份产物" % len(artifacts),
                    detail="、".join(item.filename for item in artifacts[:3]),
                    payload={"artifacts": [run_outputs.payload(item) for item in artifacts]},
                )
            execution_events.emit(
                db, execution.id, event_type="progress", stage="answer_preparing",
                status="running", actor_employee_id=employee_id,
                title="资料已整理，准备生成答复",
                detail="正在核对执行结果并整理为可读答案",
            )
            # 分段耗时小结：带 title 的新事件会被前端自动渲染成轨迹里的一步，
            # 无需改前端结构。只记秒数，不落任何材料内容。
            timing_title, timing_detail, timing_payload = _format_stage_timing(
                stage_started, stage_prepared, stage_harness_done)
            execution_events.emit(
                db, execution.id, event_type="stage_timing", stage="answer_preparing",
                status="running", actor_employee_id=employee_id,
                title=timing_title, detail=timing_detail, payload=timing_payload,
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
        # 用 answer 而不是 result.result：没拿到答复时 answer 已被读回的工作区产物替换
        message=answer, tool_cards=cards,
    )
