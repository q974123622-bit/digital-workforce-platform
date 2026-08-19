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
from .gateway import search_knowledge
from .identity import resolve_identity
from .knowledge_registry import list_resources
from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError
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
    }
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
        human_no: str | None = None,
    ) -> ChatResult:
        subject = resolve_identity(db, employee_no)
        if subject is None:
            raise HTTPException(status_code=404, detail="数字员工不存在")

        session, _ = get_or_create(db, session_id, employee_no)
        add_message(db, session_id=session.session_id, role="user", content=message)
        # 会话标题：优先 LLM 自动总结；无密钥/失败时降级为截断
        if not session.title:
            session.title = self._summarize_title(message)
            db.commit()

        messages: list[dict] = [
            {"role": "system", "content": self._system_prompt(db, subject), "source": "demo"},
        ]
        for msg in history(db, session.session_id)[:-1]:
            messages.append({"role": msg.role, "content": msg.content, "source": "demo"})
        messages.append({"role": "user", "content": message, "source": "demo"})

        tool_cards: list[ToolCard] = []
        policy_denied: ToolCard | None = None

        try:
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
                    card, tool_message = self._execute_tool(db, subject, tc.arguments)
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
        except LLMUnavailableError as exc:
            # LLM 不可用（如未配置密钥）：保留会话与 session_id，返回降级结果，前端可继续对话
            final_text = f"LLM 暂不可用：{exc}"
            add_message(db, session_id=session.session_id, role="assistant", content=final_text, tool_cards=[])
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

    @staticmethod
    def _summarize_title(message: str) -> str:
        """生成会话标题：优先 LLM 总结；无密钥/失败时降级为截断。"""
        try:
            provider = DeepSeekProvider()
            resp = provider.chat(
                [{"role": "user", "content": f"请用不超过 15 个字概括这句话的主题，只输出标题本身：{message}", "source": "demo"}]
            )
            title = (resp.content or "").strip()
            if title:
                return title[:20]
        except Exception:
            pass  # 降级：下面用截断
        cleaned = message.strip().replace("\n", " ")
        return cleaned[:20] + ("…" if len(cleaned) > 20 else "")

    def _execute_tool(self, db: Session, subject, arguments: dict) -> tuple[ToolCard, str]:
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
