"""内部接口（服务间，不暴露前端）：Policy / Gateway / Harness tools。"""

import asyncio
import json
import logging
from uuid import uuid4

logger = logging.getLogger(__name__)

from fastapi import APIRouter, Depends, Header, HTTPException, Request
from pydantic import BaseModel, ConfigDict
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services.gateway import invoke_plugin, search_knowledge, write_audit
from ..services.harness_token import HarnessClaims, verify_token
from ..services.identity import resolve_identity
from ..services.knowledge_registry import accessible_knowledge_bases
from ..services.memory_service import conversation_history, history_window
from ..services.plugin_governance import effective_skills
from ..services.policy import DECISION_ALLOW, DECISION_DENY, ResourceRef, evaluate
from ..services.runtime_adapter import DockerHarnessRuntimeAdapter, RuntimeResult
from ..services.sandbox_manager import SandboxManager
from ..services.sandbox_policy import from_identity
from ..services import attachments as attachment_service, config, execution_events, mcp_registry, tool_input
from ..services.external_mcp_adapter import ehome_adapter, qieman_adapter, xyzq_adapter
from ..services.external_mcp_client import ExternalMcpError
from ..services.prompt_shared import MAX_KNOWLEDGE_SEARCHES, truncate_playbook

router = APIRouter(prefix="/internal", tags=["internal"])

# 单次任务内允许的委派尝试次数上限（含失败重试）。成功的委派仍然只允许一次。
MAX_DELEGATION_ATTEMPTS = 3

# 内置 External MCP 适配器（进程级单例，缓存工具清单）。
# 运行期实际使用的清单 = 本常量 + ``mcp_registry`` 按数据库动态注册的自助接入 MCP；
# 保留模块级常量是为了让测试/扩展可以直接替换内置源集合。
_EXTERNAL_ADAPTERS = (qieman_adapter, xyzq_adapter, ehome_adapter)


def _external_adapters(db: Session) -> list:
    """内置 MCP 源 + 数据库动态注册的自助接入 MCP 源。"""
    return mcp_registry.adapters(db, _EXTERNAL_ADAPTERS)


class AgentKnowledgeToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    knowledge_base_id: str
    query: str


class AgentDelegateToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_agent_id: str
    question: str


class LoadSkillToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skill_name: str


class AttachmentSearchToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    query: str
    top_k: int = 5


class AttachmentReadToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    chunk_ids: list[int]


class SpreadsheetRangeToolIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    sheet: str
    range: str


def _mcp_result_text(result: dict) -> str:
    """拼接 MCP result 的 text content（用于事后缺参识别，不含凭据）。"""
    parts: list[str] = []
    for item in (result.get("content") or []):
        if isinstance(item, dict) and isinstance(item.get("text"), str):
            parts.append(item["text"])
    return "\n".join(parts)


def _input_required_result(
    db: Session, claims: HarnessClaims, adapter, raw_name: str,
    fields: list[dict], reason: str,
) -> dict:
    """产出「待补全」结构化结果：落库 + emit ``input_required`` + 给模型的可读说明。

    刻意不返回普通 ``isError``：模型看到错误会反复重试或道歉，而这里需要它**如实说明
    需要补充什么**（平台已弹气泡，用户补全后会自动续跑）。
    """
    execution = execution_events.execution_for_trace(db, claims.trace_id)
    execution_id = execution.id if execution is not None else ""
    request = tool_input.create_request(
        db,
        conversation_id=claims.conversation_id or "",
        execution_id=execution_id,
        trace_id=claims.trace_id,
        plugin_id=adapter.plugin_id,
        tool_name=raw_name,
        fields=fields,
        reason=reason,
    )
    db.commit()
    if execution is not None:
        tool_input.emit_input_required(
            db, execution_id=execution.id, request=request,
            actor_employee_id=claims.employee_id,
            source_label=str(getattr(adapter, "source_label", "") or adapter.plugin_id),
        )
    keys = "、".join(str(field.get("key") or "") for field in fields)
    label = str(getattr(adapter, "source_label", "") or adapter.plugin_id)
    text = (
        f"本次调用暂停：{label}需要用户补充 {keys}。"
        "平台已弹出「需要补充信息」输入气泡，用户补充后本轮会自动继续，"
        "无需用户重新提问。请用一句自然语言告知用户需要补充什么（字段名 + 去哪里获取），"
        "不要编造参数值、不要重复调用本工具。"
    )
    return {
        "isError": False,
        "content": [{"type": "text", "text": text}],
        "dwpInputRequired": {
            "request_id": request.id,
            "plugin_id": adapter.plugin_id,
            "tool_name": raw_name,
            "reason": reason,
            "fields": fields,
        },
    }


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


def _has_knowledge_tool(db: Session, claims: HarnessClaims) -> bool:
    # 知识访问已改为 Policy（data_level 分级）+ AgentKnowledgeGrant 控制，
    # 不再依赖 knowledge-lX 插件绑定；有显式知识授权即可提供 search_knowledge 工具。
    return db.scalar(select(models.AgentKnowledgeGrant).where(
        models.AgentKnowledgeGrant.employee_id == claims.employee_id,
        models.AgentKnowledgeGrant.action == "read",
        models.AgentKnowledgeGrant.status == "active",
    )) is not None


def _delegation_targets(db: Session) -> list[dict]:
    """可被委派的岗位AI员工（active 且 identity_kind=role_employee），带专长标签。

    委派枚举不能写死：员工新增/停用后枚举会与实际不符，模型选中已停用员工会直接 403；
    同时必须把「谁擅长什么」告诉模型，否则只能靠名字猜（AI-FUND 与 AI-INVESTMENT 极易混）。
    """
    rows = db.execute(
        select(models.DigitalEmployee, models.AgentProfile)
        .join(
            models.AgentProfile,
            models.AgentProfile.employee_id == models.DigitalEmployee.employee_no,
        )
        .where(
            models.DigitalEmployee.status == "active",
            models.AgentProfile.identity_kind == "role_employee",
        )
        .order_by(models.DigitalEmployee.employee_no)
    ).all()
    targets: list[dict] = []
    for employee, profile in rows:
        domains = "、".join(str(d) for d in (profile.knowledge_domains or []) if d)
        duties = "、".join(str(r) for r in (profile.responsibilities or [])[:2] if r)
        expertise = domains or duties or "通用"
        department = f"{employee.department}·" if employee.department else ""
        targets.append(
            {
                "employee_no": employee.employee_no,
                "label": f"{employee.employee_no} {employee.name}（{department}{expertise}）",
            }
        )
    return targets


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
            # payload 带库名：前端执行轨迹/来源兜底要显示中文库名，而不是 KB-XXX 原始 id。
            payload={"knowledge_base_name": kb_name},
            title=f"正在检索 {kb_name}", detail="正在通过授权知识接口查找相关资料",
        )
    used = db.scalar(
        select(func.count(models.AuditEvent.id)).where(
            models.AuditEvent.trace_id == claims.trace_id,
            models.AuditEvent.employee_id == claims.employee_id,
            models.AuditEvent.action == "read",
        )
    ) or 0
    if used >= MAX_KNOWLEDGE_SEARCHES:
        raise HTTPException(status_code=429, detail=f"单次任务最多检索 {MAX_KNOWLEDGE_SEARCHES} 次")
    try:
        out = search_knowledge(
            db,
            employee_id=claims.employee_id,
            knowledge_base_id=payload.knowledge_base_id,
            query=payload.query,
            trace_id=claims.trace_id,
            requester_human_no=claims.requester_human_no,
        )
    except Exception as exc:
        if execution is not None:
            # 数据源不可达与「接口报错」对用户是两件事：前者要说清「暂时用不了」。
            failure_detail = getattr(exc, "detail", None)
            if isinstance(failure_detail, dict) and failure_detail.get("reason") == "knowledge_source_unavailable":
                detail_text = "该知识库对应的数据源当前不可用，AI员工会说明情况并给出替代建议"
            else:
                detail_text = "知识接口返回安全错误，AI员工正在判断后续步骤"
            execution_events.emit(
                db, execution.id, event_type="knowledge_completed", stage="knowledge_search",
                status="running", actor_employee_id=claims.employee_id,
                knowledge_base_id=payload.knowledge_base_id,
                payload={"knowledge_base_name": kb_name},
                title=f"{kb_name} 检索未完成", detail=detail_text,
            )
        raise
    data = out.get("data") or {}
    hits = data.get("hits", [])
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="knowledge_completed", stage="knowledge_search",
            status="running", actor_employee_id=claims.employee_id,
            knowledge_base_id=payload.knowledge_base_id, hit_count=len(hits),
            payload={"knowledge_base_name": kb_name},
            title=f"{kb_name} 检索完成", detail=f"共找到 {len(hits)} 条可用资料，正在检查证据充分性",
        )
    return {
        "source": "mock" if data.get("source") == "demo" else data.get("source", "mock"),
        "knowledge_base_id": payload.knowledge_base_id,
        "hits": hits,
        "decision": out.get("decision", "deny"),
        "trace_id": claims.trace_id,
    }


def _delegated_history(db: Session, conversation_id: str) -> list[dict]:
    """被委派方要看的会话最近对话；无会话（如旧聊天页入口）时返回空列表。

    按委派发生时的 conversation.generation 取，会话若已被清空则自然取不到历史。
    """
    if not conversation_id:
        return []
    conversation = db.get(models.Conversation, conversation_id)
    if conversation is None:
        return []
    return history_window(
        conversation_history(db, conversation.id, conversation.generation, with_speaker=True)
    )


def _agent_delegate(
    db: Session, claims: HarnessClaims, payload: AgentDelegateToolIn,
) -> dict:
    if not _can_delegate(db, claims):
        raise HTTPException(status_code=403, detail="当前AI员工不能委派")
    target_profile = db.get(models.AgentProfile, payload.target_agent_id)
    target = db.get(models.DigitalEmployee, payload.target_agent_id)
    if not target_profile or not target or target.status != "active" or target_profile.identity_kind != "role_employee":
        raise HTTPException(status_code=403, detail="委派目标不是可用的岗位AI员工")
    # 「单次任务只能委派一次」约束的是**成功/进行中**的委派：失败的尝试（如目标员工正忙、
    # 容器异常）不应消耗额度，否则模型收到错误后无法重试，只能放弃 → 用户拿到没有知识库依据
    # 的答案。同时用 attempts 上限兜住重试次数，避免退化成委派循环。
    succeeded = db.scalar(
        select(func.count(models.DelegationRun.id)).where(
            models.DelegationRun.trace_id == claims.trace_id,
            models.DelegationRun.sender_employee_id == claims.employee_id,
            models.DelegationRun.status != "failed",
        )
    ) or 0
    if succeeded:
        raise HTTPException(status_code=409, detail="单次任务只能委派一次")
    attempts = db.scalar(
        select(func.count(models.DelegationRun.id)).where(
            models.DelegationRun.trace_id == claims.trace_id,
            models.DelegationRun.sender_employee_id == claims.employee_id,
        )
    ) or 0
    if attempts >= MAX_DELEGATION_ATTEMPTS:
        raise HTTPException(
            status_code=409,
            detail=f"本次任务的委派尝试已达上限（{MAX_DELEGATION_ATTEMPTS} 次），请基于已有信息作答",
        )
    run = models.DelegationRun(
        id=f"D-{uuid4().hex[:12].upper()}", trace_id=claims.trace_id,
        conversation_id="harness", requester_human_no=claims.requester_human_no,
        sender_employee_id=claims.employee_id, recipient_employee_id=payload.target_agent_id,
        action="delegate", goal=payload.question[:500], reason="Harness 自主委派", status="running",
    )
    db.add(run)
    db.commit()
    execution = execution_events.execution_for_trace(db, claims.trace_id)
    if execution is not None:
        execution_events.emit(
            db, execution.id, event_type="delegation_started", stage="delegation",
            status="running", actor_employee_id=claims.employee_id,
            target_agent_id=payload.target_agent_id,
            title=f"正在向 {target.name} 咨询", detail="已按一次委派限制交由岗位AI员工处理",
        )
    try:
        from ..services.harness_agent import run_agent

        result = run_agent(
            db, employee_id=payload.target_agent_id,
            requester_human_no=claims.requester_human_no,
            message=payload.question,
            # 被委派方也要看得见会话上文，否则只能看到一句被转述的问题。
            history=_delegated_history(db, claims.conversation_id),
            trace_id=claims.trace_id, depth=1,
            parent_goal=claims.goal,
            conversation_id=claims.conversation_id,
            # 目标员工同时在跑别的任务时，先等一会儿再判失败，而不是立刻 409。
            slot_wait_seconds=config.delegation_slot_wait_seconds(),
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
    except Exception as exc:  # noqa: BLE001 - 委派失败必须落原因并原样抛出
        # 委派失败分支原先完全静默：delegation_run 只留一个 status=failed，既没有原因字段，
        # 后端日志里也没有任何痕迹。最常见的触发原因是目标员工正在执行别的任务
        # （历史上 run_agent 的 per-employee 锁是 acquire(blocking=False)，会直接抛
        # 409「AI员工正忙」，且委派路径不重试 → 发起方仍返回 completed，
        # 用户拿到的是没有知识库依据的答案）。
        detail = getattr(exc, "detail", None)
        if detail is None:
            reason = f"{type(exc).__name__}: {exc}"
        elif isinstance(detail, dict):
            reason = str(detail.get("message") or detail)
        else:
            reason = str(detail)
        reason = reason[:500]
        logger.warning(
            "delegation failed sender=%s target=%s trace=%s reason=%s goal=%s",
            claims.employee_id, payload.target_agent_id, claims.trace_id, reason,
            (payload.question or "")[:200],
        )
        run.status = "failed"
        run.error = reason
        db.add(run)
        db.commit()
        if execution is not None:
            execution_events.emit(
                db, execution.id, event_type="delegation_completed", stage="delegation",
                status="running", actor_employee_id=claims.employee_id,
                target_agent_id=payload.target_agent_id,
                title=f"{target.name} 暂时无法协助",
                # 把原因透到执行轨迹里：用户至少能看到「为什么这次没有知识库依据」。
                detail=f"委派未完成（{reason}），数字分身正在判断是否可独立答复",
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
        tools = []
        attachment_catalog = attachment_service.attachment_catalog(db, claims.trace_id)
        if attachment_catalog:
            allowed_ids = [item["attachment_id"] for item in attachment_catalog]
            tools.extend([
                {
                    "name": "list_attachments",
                    "description": "列出本次任务明确选择且已解析完成的会话附件。",
                    "inputSchema": {"type": "object", "properties": {}},
                },
                {
                    "name": "search_attachment",
                    "description": "按问题检索附件内容片段；先检索再按 chunk_id 精读。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "string", "enum": allowed_ids},
                            "query": {"type": "string"},
                            "top_k": {"type": "integer", "minimum": 1, "maximum": 8},
                        },
                        "required": ["attachment_id", "query"],
                    },
                },
                {
                    "name": "read_attachment",
                    "description": "读取 search_attachment 返回的指定内容片段，单次最多八段。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "string", "enum": allowed_ids},
                            "chunk_ids": {"type": "array", "items": {"type": "integer"}, "maxItems": 8},
                        },
                        "required": ["attachment_id", "chunk_ids"],
                    },
                },
            ])
            spreadsheet_ids = [item["attachment_id"] for item in attachment_catalog if item["file_type"] == "xlsx"]
            if spreadsheet_ids:
                tools.append({
                    "name": "read_spreadsheet_range",
                    "description": "读取 XLSX 指定工作表范围，单次最多 200 个单元格。",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "attachment_id": {"type": "string", "enum": spreadsheet_ids},
                            "sheet": {"type": "string"},
                            "range": {"type": "string", "description": "例如 A1:F20"},
                        },
                        "required": ["attachment_id", "sheet", "range"],
                    },
                })
        if _has_knowledge_tool(db, claims):
            subject = resolve_identity(db, claims.employee_id)
            # accessible_knowledge_bases 已过滤掉数据源不可达的知识库；
            # 若过滤后一个都不剩，就不暴露 search_knowledge —— 否则模型会拿到空枚举
            # 反复试错，用户侧体验更差（宁可不给工具）。
            allowed = (
                accessible_knowledge_bases(db, subject, claims.requester_human_no)
                if subject is not None else []
            )
            if allowed:
                kb_desc = "在当前AI员工已授权的一个知识库中检索，每次只能检索一库。"
                kb_desc += " knowledge_base_id 必须从以下已授权知识库中选择："
                kb_desc += "、".join(
                    f"{kb['id']}（{kb['name']}；领域：{kb.get('domain') or '通用'}，{kb.get('data_level') or 'L1'}）"
                    for kb in allowed
                )
                tools.append({
                    "name": "search_knowledge",
                    "description": kb_desc,
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "knowledge_base_id": {"type": "string", "enum": [kb["id"] for kb in allowed]},
                            "query": {"type": "string"},
                        },
                        "required": ["knowledge_base_id", "query"],
                    },
                })
        if _can_delegate(db, claims):
            targets = _delegation_targets(db)
            if targets:
                tools.append({
                    "name": "ask_digital_employee",
                    "description": (
                        "向一名岗位AI员工求助一次，对方不能继续委派。"
                        "请按专长选择最匹配的一位：" + "、".join(t["label"] for t in targets)
                    ),
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "target_agent_id": {
                                "type": "string",
                                "enum": [t["employee_no"] for t in targets],
                                "description": "必须从枚举中选择，并按上文的专长描述匹配，不要凭名字猜测。",
                            },
                            "question": {
                                "type": "string",
                                "description": "转述给对方的问题，需包含必要的任务背景，不要只给一句孤立问题。",
                            },
                        },
                        "required": ["target_agent_id", "question"],
                    },
                })
        # 技能按需加载：prompt 里只给 available_skills 元信息，是否调用由模型自己判断，
        # 避免把全部 SOP 正文强塞进上下文（既浪费预算，又会被截断丢掉后半段）。
        available_skills = effective_skills(db, claims.employee_id)
        if available_skills:
            tools.append({
                "name": "load_skill",
                "description": (
                    "读取一项已授权个人技能的完整 SOP。TASK_ENVELOPE.available_skills 中"
                    "若有一项与当前任务确实相关，先调用本工具取回其完整内容再按其执行；"
                    "不相关就不要调用。"
                ),
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "skill_name": {
                            "type": "string",
                            "enum": [s["name"] for s in available_skills],
                            "description": "必须从枚举中选择，不要编造技能名。",
                        },
                    },
                    "required": ["skill_name"],
                },
            })
        # 追加已授权员工的外部 MCP 工具（内置源 + 自助接入动态源，加前缀；
        # 失败降级不阻塞内部工具）。
        for adapter in _external_adapters(db):
            if adapter.configured() and adapter.is_authorized(db, claims.employee_id):
                try:
                    tools.extend(adapter.prefixed_tools())
                except ExternalMcpError as exc:
                    logger.warning("合并外部 MCP 工具失败：%s", exc)
        result = {"tools": tools}
    elif method == "tools/call":
        params = body.get("params") or {}
        arguments = params.get("arguments") or {}
        tool_name = str(params.get("name") or "")
        adapter = None
        raw_name = ""
        try:
            # 最长前缀优先：``mcp_ab__`` 不能误吞 ``mcp_abc__`` 的调用。
            adapter = mcp_registry.match(_external_adapters(db), tool_name)
            if adapter is not None:
                # 外部 MCP 工具：转发到对应 adapter 的 ExternalMcpClient，
                # 返回的 MCP result（content/isError）直接透传给 Harness。
                # 同步 httpx 调用放入线程池，避免阻塞 uvicorn 事件循环。
                raw_name = tool_name[len(adapter.tool_prefix):]
                execution = execution_events.execution_for_trace(db, claims.trace_id)
                # 事前预检：用户级凭据缺失/已过期时直接产出「待补全」，不发注定失败的请求。
                missing = tool_input.detect_missing_input(
                    db, adapter, claims.requester_human_no, raw_name, arguments,
                )
                if missing:
                    result = _input_required_result(
                        db, claims, adapter, raw_name, missing, tool_input.REASON_MISSING,
                    )
                else:
                    if execution is not None:
                        execution_events.emit(
                            db, execution.id, event_type="external_mcp_started", stage="external_mcp",
                            status="running", actor_employee_id=claims.employee_id,
                            title=f"正在调用{adapter.source_label} MCP 工具 {raw_name}",
                            detail=f"正在通过{adapter.source_label}开放平台获取数据",
                        )
                    # 按人鉴权：把该员工自己的凭据按次注入请求头（不落日志、不入库明文）。
                    # 无按人凭据的适配器保持「两个位置参数」调用，兼容既有适配器与测试替身。
                    auth_headers = tool_input.build_auth_headers(db, adapter, claims.requester_human_no)
                    if auth_headers:
                        result = await asyncio.to_thread(
                            adapter.call_tool, tool_name, arguments, auth_headers,
                        )
                    else:
                        result = await asyncio.to_thread(
                            adapter.call_tool, tool_name, arguments,
                        )
                    if isinstance(result, dict) and result.get("isError"):
                        # 事后识别：服务端把缺参放在 result 里（未抛异常）时同样转成待补全。
                        error_fields = tool_input.detect_missing_from_text(
                            _mcp_result_text(result), adapter,
                        )
                        if error_fields:
                            result = _input_required_result(
                                db, claims, adapter, raw_name, error_fields,
                                tool_input.REASON_TOOL_ERROR,
                            )
                    if execution is not None:
                        execution_events.emit(
                            db, execution.id, event_type="external_mcp_completed", stage="external_mcp",
                            status="running", actor_employee_id=claims.employee_id,
                            title=f"{adapter.source_label} MCP 工具 {raw_name} 调用完成", detail="数据已获取",
                        )
                    # 写审计，让前端执行轨迹可见外部 MCP 调用；只记工具名与来源，不落凭据/响应内容。
                    write_audit(
                        db, trace_id=claims.trace_id, employee_id=claims.employee_id,
                        plugin_id=adapter.plugin_id, action="execute", decision="allow",
                        reason=f"调用{adapter.source_label} MCP 工具 {raw_name}",
                        result_summary=__import__("json").dumps(
                            {"source": adapter.plugin_id, "tool": raw_name, "type": "mcp"}, ensure_ascii=False,
                        ),
                    )
            elif tool_name == "search_knowledge":
                data = _agent_search(db, claims, AgentKnowledgeToolIn.model_validate(arguments))
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "ask_digital_employee":
                # 委派会同步拉起目标AI员工的 Harness 容器执行（最长约 2 分钟）。
                # 这里必须放到线程池，否则会阻塞 uvicorn 事件循环，导致被委派方
                # 的 MCP 回调（tools/list、search_knowledge）无法被处理而超时失败。
                data = await asyncio.to_thread(
                    _agent_delegate, db, claims, AgentDelegateToolIn.model_validate(arguments)
                )
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "list_attachments":
                data = {"attachments": attachment_service.attachment_catalog(db, claims.trace_id)}
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "search_attachment":
                payload = AttachmentSearchToolIn.model_validate(arguments)
                data = attachment_service.search_attachment(
                    db, trace_id=claims.trace_id, requester=claims.requester_human_no,
                    employee_id=claims.employee_id, attachment_id=payload.attachment_id,
                    query=payload.query, top_k=payload.top_k,
                )
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "read_attachment":
                payload = AttachmentReadToolIn.model_validate(arguments)
                data = attachment_service.read_attachment(
                    db, trace_id=claims.trace_id, requester=claims.requester_human_no,
                    employee_id=claims.employee_id, attachment_id=payload.attachment_id,
                    chunk_ids=payload.chunk_ids,
                )
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "read_spreadsheet_range":
                payload = SpreadsheetRangeToolIn.model_validate(arguments)
                data = attachment_service.read_spreadsheet_range(
                    db, trace_id=claims.trace_id, requester=claims.requester_human_no,
                    employee_id=claims.employee_id, attachment_id=payload.attachment_id,
                    sheet=payload.sheet, cell_range=payload.range,
                )
                result = {"content": [{"type": "text", "text": __import__("json").dumps(data, ensure_ascii=False)}]}
            elif tool_name == "load_skill":
                payload = LoadSkillToolIn.model_validate(arguments)
                skill = next(
                    (s for s in effective_skills(db, claims.employee_id) if s["name"] == payload.skill_name),
                    None,
                )
                if skill is None:
                    raise HTTPException(status_code=404, detail="技能不存在或未授权")
                sop, truncated = truncate_playbook(skill["content"])
                execution = execution_events.execution_for_trace(db, claims.trace_id)
                if execution is not None:
                    execution_events.emit(
                        db, execution.id, event_type="skill_routed", stage="harness_started",
                        status="running", actor_employee_id=claims.employee_id,
                        title=f"已载入技能「{skill['name']}」",
                        detail="AI员工判断该技能与当前任务相关，正在按其 SOP 执行",
                    )
                result = {"content": [{"type": "text", "text": json.dumps({
                    "skill_name": skill["name"],
                    "description": skill["description"],
                    "sop": sop,
                    "sop_truncated": truncated,
                }, ensure_ascii=False)}]}
            else:
                raise HTTPException(status_code=404, detail="未知工具")
        except ExternalMcpError as exc:
            # 事后识别：错误文本里出现「Missing required header / 缺少请求参数」等信号时，
            # 转成「待补全」而不是普通错误 —— 模型看到错误只会道歉或重试，用户拿不到入口。
            error_fields = (
                tool_input.detect_missing_from_text(str(exc), adapter) if adapter is not None else []
            )
            if error_fields:
                result = _input_required_result(
                    db, claims, adapter, raw_name, error_fields, tool_input.REASON_TOOL_ERROR,
                )
            else:
                result = {"isError": True, "content": [{"type": "text", "text": f"外部 MCP 工具调用失败：{exc}"}]}
        except Exception as exc:
            # HTTPException 的 detail 可能是 dict（如 {"message": ..., "retryable": True}）。
            # 直接 str(dict) 会把 Python repr（单引号、True）塞给模型看，模型很难据此决策；
            # 统一取 message，缺失时回退成 JSON。
            if isinstance(exc, HTTPException):
                detail = exc.detail
                if isinstance(detail, dict):
                    text = str(detail.get("message") or json.dumps(detail, ensure_ascii=False))
                else:
                    text = str(detail)
            else:
                text = f"工具执行失败：{exc.__class__.__name__}"
            logger.warning("agent tool failed tool=%s text=%s", tool_name, text[:300])
            result = {"isError": True, "content": [{"type": "text", "text": text}]}
    else:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


@router.post("/policy/evaluate", response_model=schemas.PolicyEvaluateOut)
def policy_evaluate(payload: schemas.PolicyEvaluateIn, db: Session = Depends(get_db)):
    """四维策略评估；subject 以数据库身份为准（调用方传入的身份字段仅作参考，不可伪造）。"""
    subject = resolve_identity(db, payload.subject.employee_no)
    if subject is None:
        raise HTTPException(status_code=404, detail="AI员工不存在")
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
        raise HTTPException(status_code=404, detail="AI员工不存在")

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
        raise HTTPException(status_code=404, detail="AI员工不存在")
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
