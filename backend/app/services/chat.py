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
from .knowledge_registry import list_resources
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
                    "knowledge_base_id": {
                        "type": "string",
                        "description": (
                            "知识库资源 ID（按问题领域选择）："
                            "KB-PUBLIC 公共制度/FAQ；KB-ONBOARD 新员工入职；"
                            "KB-INTERNAL 正式员工内部制度；KB-FINTECH 金融科技；"
                            "KB-IT-SERVICE IT/办公软件（企业微信/邮箱/VPN 等）；"
                            "KB-SECURITIES 证券业务（融资融券/期权/科创板等）；"
                            "KB-REG-INTERNAL 内部合规制度；KB-REG-EXTERNAL 外部监管法规"
                        ),
                    },
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
    {
        "type": "function",
        "function": {
            "name": "list_knowledge_bases",
            "description": "列出当前 Demo 项目可用的知识库目录（可选按 level / domain 过滤）",
            "parameters": {
                "type": "object",
                "properties": {
                    "level": {"type": "string", "description": "可选：L1 / L2"},
                    "domain": {"type": "string", "description": "可选：知识库领域"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_employee",
            "description": "搜索 Mock 员工目录（可选 keyword / department / type / digital_only）",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选：匹配工号/姓名/部门"},
                    "department": {"type": "string", "description": "可选：部门"},
                    "type": {"type": "string", "description": "可选：twin / virtual / rpa"},
                    "digital_only": {"type": "boolean", "description": "可选：只返回数字员工"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_documents",
            "description": "列出 Demo 文档 Fixture 目录（只返回名称，不返回正文）",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "compare_regulations",
            "description": "同时查询外部监管与内部制度，形成监管对比材料",
            "parameters": {
                "type": "object",
                "properties": {
                    "query": {"type": "string", "description": "监管对比查询关键词"},
                },
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "review_document_compliance",
            "description": "读取文档并收集外部监管与内部制度依据（最终分析由本技能完成）",
            "parameters": {
                "type": "object",
                "properties": {
                    "document_name": {"type": "string", "description": "文档文件名"},
                    "query": {"type": "string", "description": "合规依据查询关键词"},
                },
                "required": ["document_name", "query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "handle_it_support",
            "description": "查询 IT 知识库，可选升级协作（escalate=true 时尝试联系 IT 数字员工）",
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string", "description": "IT 问题"},
                    "escalate": {"type": "boolean", "description": "可选：是否升级协作"},
                    "target_employee_id": {"type": "string", "description": "可选：目标 IT 数字员工工号"},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "assist_with_employee",
            "description": "查找数字员工并发起协作询问",
            "parameters": {
                "type": "object",
                "properties": {
                    "keyword": {"type": "string", "description": "可选：搜索关键词"},
                    "department": {"type": "string", "description": "可选：部门"},
                    "target_employee_id": {"type": "string", "description": "可选：直接指定目标数字员工"},
                    "request": {"type": "string", "description": "协作请求内容"},
                },
                "required": ["request"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "prepare_work_report",
            "description": "汇总工作记录并触发 RPA 报表（可能返回 approval_required，不得自动通过审批）",
            "parameters": {
                "type": "object",
                "properties": {
                    "status": {"type": "string", "description": "可选：工作记录状态过滤"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "analyze_policy_change",
            "description": (
                "用于对虚构监管/制度变更材料做跨知识源影响分析："
                "读取变更文档，检索外部监管、内部制度和证券业务知识，"
                "并可选查找相关数字员工发起协作。"
                "该工具只收集结构化证据，不生成正式法律或合规结论。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "document_name": {"type": "string", "description": "待分析的虚构监管/制度变更材料文件名"},
                    "query": {"type": "string", "description": "希望分析的政策变更主题"},
                    "collaborate": {"type": "boolean", "description": "可选：是否在知识检索后发起数字员工协作，默认 true"},
                    "target_employee_id": {"type": "string", "description": "可选：明确指定协作对象数字员工工号"},
                    "employee_keyword": {"type": "string", "description": "可选：未指定 target 时用于搜索相关数字员工"},
                    "department": {"type": "string", "description": "可选：未指定 target 时用于限定员工部门"},
                },
                "required": ["document_name", "query"],
            },
        },
    },
]


DEMO_TOOL_PLUGIN_MAP: dict[str, tuple[str, str, str]] = {
    "collaborate_employee": ("employee-collaboration", "execute", "collaboration"),
    "read_document": ("document-read", "read", "document"),
    "query_work_records": ("work-record-query", "read", "work-records"),
    "list_knowledge_bases": ("knowledge-catalog", "read", "knowledge-catalog"),
    "search_employee": ("employee-search", "read", "employee-search"),
    "list_documents": ("document-catalog", "read", "document-catalog"),
    "compare_regulations": ("regulation-compare-workflow", "execute", "regulation-compare-workflow"),
    "review_document_compliance": ("document-compliance-workflow", "execute", "document-compliance-workflow"),
    "handle_it_support": ("it-support-workflow", "execute", "it-support-workflow"),
    "assist_with_employee": ("employee-assist-workflow", "execute", "employee-assist-workflow"),
    "prepare_work_report": ("report-export-workflow", "execute", "report-export-workflow"),
    "analyze_policy_change": ("policy-change-impact-workflow", "execute", "policy-change-impact-workflow"),
}


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
            {"role": "system", "content": self._system_prompt(db, subject), "source": "demo"},
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
        entry = DEMO_TOOL_PLUGIN_MAP.get(name)
        if entry is None:
            return ToolCard(plugin_id=name, name=name, decision="error", reason="未知工具"), "未知工具调用"
        plugin_id, action, label = entry
        params = dict(arguments)
        if name == "query_work_records":
            params["employee_id"] = subject.employee_id
        return self._invoke_demo_tool(db, subject, plugin_id, action, params, label)

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

    def _system_prompt(self, db: Session, subject) -> str:
        role_label = "正式员工" if subject.employment_type == "formal" else "实习生"
        kb_names = ", ".join(kb.name for kb in list_resources(db))
        persona = subject.role_prompt or "你是数字员工平台的演示助手。"
        return (
            f"【人设】{persona}\n"
            "【身份】所有内容均为虚构演示数据（source=demo）。"
            f"当前数字员工：{subject.employee_id}（类型 {subject.employee_type}，身份 {role_label}，"
            f"部门 {subject.department}，Owner {subject.owner_id}）。"
            f"【知识库】平台登记的知识库：{kb_names}。"
            "【规则】只能通过 search_knowledge 工具查询知识库，禁止编造知识库内容；"
            "工具返回拒绝时如实告知用户无权访问，不得尝试绕过。"
        )
