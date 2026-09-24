"""会话历史的单一来源：窗口裁剪、文本渲染与会话消息读取。

任务判定 / 子任务拆解 / Harness 主链路 / 兜底聊天链路都从这里取「最近对话」，
保证口径一致：先取最近 MAX_HISTORY_ROUNDS 轮，再按字符预算从最早丢。
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

# 全链路统一的历史窗口：轮次上限 + 字符预算（与 MAX_PLAYBOOK_CHARS 对齐）。
MAX_HISTORY_ROUNDS = 8
MAX_HISTORY_CHARS = 4000


def _content_len(item: dict) -> int:
    return len(str(item.get("content") or ""))


def _turns(messages: list[dict]) -> list[list[dict]]:
    """把消息切成「轮」：一个 user 吸收其后所有 assistant，直到下一个 user。

    旧实现一个 user 只配对第一条 assistant，群聊里第二个及以后的成员回复会被静默
    丢弃；这里改为整轮聚合，是「看得见上文」的前置条件。
    """
    turns: list[list[dict]] = []
    current: list[dict] | None = None
    for item in messages:
        role = item.get("role")
        if role == "user":
            if current is not None:
                turns.append(current)
            current = [item]
        elif role == "assistant":
            if current is None:
                # 会话开头的孤立 assistant（如欢迎语）：单独成轮，不丢
                turns.append([item])
            else:
                current.append(item)
    if current is not None:
        turns.append(current)
    return turns


def history_window(
    messages: list[dict],
    goal: str | None = None,
    *,
    rounds: int = MAX_HISTORY_ROUNDS,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[dict]:
    """返回最近若干轮对话，供模型作为上下文。

    - 轮次上限：默认最近 8 轮（一轮 = 一个 user 及其后的全部 assistant）。
    - 字符预算：超出 max_chars 时从最早的整轮开始丢弃，避免 prompt 无界膨胀。
    - 悬空提问：末轮只有 user、没有 assistant 时，若其内容与 goal 不同则保留
      （上一轮答复可能因报错未落库）；与 goal 相同则剔除，避免重复注入。
    """
    turns = _turns(messages)
    tail: list[dict] = []
    if turns and len(turns[-1]) == 1 and turns[-1][0].get("role") == "user":
        pending = turns.pop()
        if goal is not None and str(pending[0].get("content") or "") != str(goal):
            tail = pending
    selected = turns[-rounds:] if rounds > 0 else list(turns)
    total = sum(_content_len(item) for turn in selected for item in turn)
    while selected and total > max_chars:
        dropped = selected.pop(0)
        total -= sum(_content_len(item) for item in dropped)
    return [item for turn in selected for item in turn] + tail


def five_complete_rounds(messages: list[dict], goal: str | None = None) -> list[dict]:
    """历史窗口的兼容入口（既有调用方沿用此名，语义与 history_window 一致）。"""
    return history_window(messages, goal)


def render_transcript(messages: list[dict]) -> str:
    """把窗口内消息渲染成纯文本对话记录（`说话人：内容`）。

    一律不使用「【名字】」前缀：UI 已按参与者展示姓名，前缀只会诱导模型模仿该格式。
    空历史返回空串，调用方据此决定是否插入段落。
    """
    lines: list[str] = []
    for item in messages:
        content = str(item.get("content") or "").strip()
        if not content:
            continue
        speaker = str(item.get("speaker") or "").strip()
        if not speaker:
            speaker = "我" if item.get("role") == "user" else "同事"
        lines.append(f"{speaker}：{content}")
    return "\n".join(lines)


def conversation_history(
    db: Session,
    conversation_id: str,
    generation: int = 0,
    *,
    with_speaker: bool = False,
) -> list[dict]:
    """读取会话消息并转成模型上下文（source=demo）。

    with_speaker=True 时额外带 `speaker`（user 为「我」），仅供渲染成文本的路径使用；
    不要把它塞进 provider 的 messages，OpenAI 兼容接口会拒绝多余字段。

    已被就地重跑取代的回复（`superseded_at` 非空）不进入上下文：否则模型会同时
    看到同一轮的两条互相矛盾的答案。
    """
    rows = db.scalars(
        select(models.ConversationMessage)
        .where(
            models.ConversationMessage.conversation_id == conversation_id,
            models.ConversationMessage.generation == generation,
            models.ConversationMessage.superseded_at.is_(None),
        )
        .order_by(models.ConversationMessage.seq)
    ).all()
    out: list[dict] = []
    for row in rows:
        role = "user" if row.role == "user" else "assistant"
        item: dict = {"role": role, "content": row.content, "source": "demo"}
        if with_speaker:
            item["speaker"] = "我" if role == "user" else (row.participant_name or "同事")
        out.append(item)
    return out


def conversation_transcript(
    db: Session,
    conversation_id: str,
    generation: int = 0,
    *,
    exclude_current: str | None = None,
) -> str:
    """取该会话最近对话并渲染为纯文本，供任务判定 / 拆解注入。

    exclude_current 传入本轮用户消息文本时，会先剔除末尾同内容的 user 消息
    （它已单独作为「用户消息」给出），避免重复注入。
    """
    messages = conversation_history(db, conversation_id, generation, with_speaker=True)
    if (
        exclude_current is not None
        and messages
        and messages[-1].get("role") == "user"
        and str(messages[-1].get("content") or "") == str(exclude_current)
    ):
        messages = messages[:-1]
    return render_transcript(history_window(messages))
