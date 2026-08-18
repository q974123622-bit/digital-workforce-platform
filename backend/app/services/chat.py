"""Chat Orchestrator（Sprint 4）。

严格调用链：User → Employee → LLM → Tool Intent → Policy Engine
  → Plugin Gateway → Knowledge Adapter → Result → LLM → Answer

禁止：LLM 直连 Knowledge Adapter / 数据库 / 内部 API。
工具调用一律经 gateway（含 Policy 评估与审计）。
"""

import json
from dataclasses import dataclass, field

from fastapi import HTTPException
from sqlalchemy.orm import Session

from .. import models
from .gateway import invoke_plugin, search_knowledge
from .identity import resolve_identity
from .llm import LLMProvider, LLMUnavailableError
from .session import add_message, get_or_create, history

MAX_TOOL_ROUNDS = 3

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "search_knowledge",
            "description": "按知识库 ID 搜索知识库内容（调用前会经过策略授权）",
            "parameters": {
                "type": "object",
                "properties": {
                    "knowledge_base_id": {"type": "string", "description": "知识库资源 ID，如 KB-PUBLIC / KB-INTERNAL / KB-ONBOARD"},
                    "query": {"type": "string", "description": "检索关键词/问题"},
                },
                "required": ["knowledge_base_id", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "collaborate_employee",
            "description": "向目标数字员工发起协作：询问信息、委托子任务或转交任务（调用前会经过策略授权）",
            "parameters": {
                "type": "object",
                "properties": {
                    "target_employee_id": {"type": "string", "description": "目标数字员工工号"},
                    "action": {"type": "string", "enum": ["ask", "delegate", "handoff"], "description": "协作方式"},
                    "request": {"type": "string", "description": "协作请求内容"},
                },
                "required": ["target_employee_id", "action"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "read_document",
            "description": "读取虚构文档内容（调用前会经过策略授权）",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_name": {"type": "string", "description": "文档文件名，如 normal-document.md"},
                },
                "required": ["document_name"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "query_work_records",
            "description": "查询当前数字员工的工作记录（可选按状态过滤）",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "可选状态过滤：completed / in_progress / not_done / research / review / issue_resolved"},
                },
                "required": [],
            },
        },
    },
]


@dataclass
class ToolCard:
    plugin_id: str
    name: str
    decision: str
    policy_id: str | None = None
    reason: str | None = None


@dataclass
class ChatResult:
    session_id: str
    trace_id: str
    message: str
    tool_cards: list[ToolCard] = field(default_factory=list)
    policy_denied: ToolCard | None = None


class ChatOrchestrator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def handle_message(
        self,
        db: Session,
        *,
        employee_no: str,
        message: str,
        session_id: str | None,
    ) -> ChatResult:
        subject = resolve_identity(db, employee_no)
        if subject is None:
            raise HTTPException(status_code=404, detail="数字员工不存在")

        session, _ = get_or_create(db, session_id, employee_no)
        add_message(db, session_id=session.session_id, role="user", content=message)

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt(subject), "source": "demo"},
        ]
        for msg in history(db, session.session_id)[:-1]:
            messages.append({"role": msg.role, "content": msg.content, "source": "demo"})
        messages.append({"role": "user", "content": message, "source": "demo"})

        tool_cards: list[ToolCard] = []
        policy_denied: ToolCard | None = None

        for _round in range(MAX_TOOL_ROUNDS):
            resp = self.provider.chat(messages, tools=TOOLS)
            if not resp.tool_calls:
                # 最终回答
                add_message(db, session_id=session.session_id, role="assistant", content=resp.content, tool_cards=[self._card_dict(c) for c in tool_cards])
                return ChatResult(
                    session_id=session.session_id,
                    trace_id=session.trace_id,
                    message=resp.content,
                    tool_cards=tool_cards,
                    policy_denied=policy_denied,
                )
            # 组装 assistant 工具意图消息（保留 tool_calls，供模型多轮继续）
            assistant_msg: dict = {
                "role": "assistant",
                "content": resp.content or "",
                "tool_calls": [
                    {
                        "id": tc.id,
                        "type": "function",
                        "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)},
                    }
                    for tc in resp.tool_calls
                ],
                "source": "demo",
            }
            messages.append(assistant_msg)
            for tc in resp.tool_calls:
                card, tool_message = self._execute_tool(db, subject, tc.name, tc.arguments)
                tool_cards.append(card)
                if card.decision == "deny" and policy_denied is None:
                    policy_denied = card
                messages.append({"role": "tool", "tool_call_id": tc.id, "content": tool_message, "source": "demo"})

        # 超过工具轮数：直接返回最后一次工具结果描述
        final_text = "工具调用次数过多，请重试。"
        add_message(db, session_id=session.session_id, role="assistant", content=final_text, tool_cards=[self._card_dict(c) for c in tool_cards])
        return ChatResult(
            session_id=session.session_id,
            trace_id=session.trace_id,
            message=final_text,
            tool_cards=tool_cards,
            policy_denied=policy_denied,
        )

    @staticmethod
    def _card_dict(card: ToolCard) -> dict:
        return {
            "plugin_id": card.plugin_id,
            "name": card.name,
            "decision": card.decision,
            "policy_id": card.policy_id,
            "reason": card.reason,
        }

    def _execute_tool(self, db: Session, subject, name: str, arguments: dict) -> tuple[ToolCard, str]:
        if name == "search_knowledge":
            return self._execute_knowledge(db, subject, arguments)
        if name == "collaborate_employee":
            return self._invoke_demo_tool(db, subject, "employee-collaboration", "execute", arguments, "collaboration")
        if name == "read_document":
            return self._invoke_demo_tool(db, subject, "document-read", "read", arguments, "document")
        if name == "query_work_records":
            merged = dict(arguments)
            merged["employee_id"] = subject.employee_id
            return self._invoke_demo_tool(db, subject, "work-record-query", "read", merged, "work-records")
        return ToolCard(plugin_id=name, name=name, decision="error", reason="未知工具"), "未知工具调用"

    def _execute_knowledge(self, db: Session, subject, arguments: dict) -> tuple[ToolCard, str]:
        kb_id = str(arguments.get("knowledge_base_id", ""))
        query = str(arguments.get("query", ""))
        try:
            result = search_knowledge(
                db,
                employee_id=subject.employee_id,
                knowledge_base_id=kb_id,
                query=query,
                trace_id=f"T-CHAT-{subject.employee_id}",
            )
            card = ToolCard(
                plugin_id=f"knowledge:{kb_id}",
                name=kb_id,
                decision="allow",
                policy_id=result.get("policy_id"),
            )
            return card, f"工具结果（source=demo）：{json.dumps(result.get('data', {}), ensure_ascii=False)}"
        except HTTPException as exc:
            if exc.status_code == 403:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                card = ToolCard(
                    plugin_id=f"knowledge:{kb_id}",
                    name=kb_id,
                    decision="deny",
                    policy_id=detail.get("policy_id"),
                    reason=detail.get("reason"),
                )
                return card, "POLICY_DENIED（source=demo）：当前身份无权访问该知识库，请如实告知用户。"
            raise

    def _invoke_demo_tool(self, db: Session, subject, plugin_id: str, action: str, params: dict, label: str) -> tuple[ToolCard, str]:
        try:
            result = invoke_plugin(
                db,
                employee_id=subject.employee_id,
                plugin_id=plugin_id,
                action=action,
                params=params,
                trace_id=f"T-CHAT-{subject.employee_id}",
            )
            card = ToolCard(
                plugin_id=plugin_id,
                name=label,
                decision="allow",
                policy_id=result.get("policy_id"),
            )
            return card, f"工具结果（source=demo）：{json.dumps(result.get('data', {}), ensure_ascii=False)}"
        except HTTPException as exc:
            if exc.status_code == 403:
                detail = exc.detail if isinstance(exc.detail, dict) else {}
                card = ToolCard(
                    plugin_id=plugin_id,
                    name=label,
                    decision="deny",
                    policy_id=detail.get("policy_id"),
                    reason=detail.get("reason"),
                )
                return card, "POLICY_DENIED（source=demo）：当前身份无权执行该操作，请如实告知用户。"
            raise

    def _system_prompt(self, subject) -> str:
        role_label = "正式员工" if subject.employment_type == "formal" else "实习生"
        return (
            "你是数字员工平台的演示助手。所有内容均为虚构演示数据（source=demo）。"
            f"当前数字员工：{subject.employee_id}（类型 {subject.employee_type}，身份 {role_label}，"
            f"部门 {subject.department}，Owner {subject.owner_id}）。"
            "只使用当前可用的工具查询知识库、进行员工协作、读取文档或查询工作记录，禁止编造内容；"
            "工具返回拒绝时如实告知用户无权访问，不得尝试绕过。"
        )
