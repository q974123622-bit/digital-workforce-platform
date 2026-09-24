"""会话检索：在会话名、成员名与历史消息正文中查找关键字，按会话聚合返回。

为什么放在后端：消息正文只存在于 conversation_message 表，前端会话列表里只有被截断的
last_message，把正文拉到前端过滤既费流量又会拖慢首屏。

只读增强，必须 fail-safe：任何异常都在内部吞掉并返回空列表，绝不能影响 /conversations 列表链路。
当前用 LIKE + 手动转义实现（全库无 ilike 先例）；若消息表继续增长，只需替换本模块内部实现
（FTS5 / ES），接口契约不变。
"""

from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

# 与 `content.like(pattern, escape=_LIKE_ESCAPE)` 配对。用户输入里的 \ % _ 必须按字面量匹配，
# 否则一个 "%" 就会命中全部会话。
_LIKE_ESCAPE = "\\"
_WHITESPACE = re.compile(r"\s+")
_LINK = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_LIST_MARKER = re.compile(r"(?m)^\s{0,3}(?:[-*+]|\d{1,3}[.)])\s+")
_HEADING = re.compile(r"(?m)^\s{0,3}#{1,6}\s*")
_QUOTE = re.compile(r"(?m)^\s{0,3}>\s?")
_FALLBACK_SNIPPET_CHARS = 120


def escape_like(value: str) -> str:
    """把 LIKE 的通配符转义成字面量。"""
    return (
        value.replace(_LIKE_ESCAPE, _LIKE_ESCAPE * 2)
        .replace("%", _LIKE_ESCAPE + "%")
        .replace("_", _LIKE_ESCAPE + "_")
    )


def flatten_markdown(text: str) -> str:
    """把 Markdown 压成单行纯文本，供 304px 侧栏的片段展示。

    表格竖线、代码反引号、标题井号、引用尖括号、列表符号都要去掉，
    否则片段里会混进一堆符号，肉眼读不出关键词上下文。
    """
    if not text:
        return ""
    flat = _LINK.sub(r"\1", text)
    flat = flat.replace("```", " ").replace("`", "")
    flat = _HEADING.sub("", flat)
    flat = _QUOTE.sub("", flat)
    flat = _LIST_MARKER.sub("", flat)
    flat = flat.replace("|", " ").replace("~~", "").replace("*", "")
    return _WHITESPACE.sub(" ", flat).strip()


def build_snippet(content: str, q: str, before: int = 40, after: int = 60) -> str:
    """取命中处前后一小段原文并压平。

    定位在**原始** content 上做（一定找得到，不会与归一化后的文本错位），压平只作用于窗口，
    这样多词/含空格的关键字也能正确给出片段。
    """
    text = content or ""
    keyword = (q or "").strip()
    index = text.lower().find(keyword.lower()) if keyword else -1
    if index < 0:
        flat = flatten_markdown(text)
        return f"{flat[:_FALLBACK_SNIPPET_CHARS]}…" if len(flat) > _FALLBACK_SNIPPET_CHARS else flat
    start = max(0, index - before)
    end = min(len(text), index + len(keyword) + after)
    snippet = flatten_markdown(text[start:end])
    if start > 0:
        snippet = "…" + snippet
    if end < len(text):
        snippet = snippet + "…"
    return snippet


def conversation_is_active(db: Session, conv: models.Conversation) -> bool:
    """Keep history in storage but hide conversations for retired demo identities."""
    if conv.hidden_at is not None:
        return False
    participants = conv.participants or []
    if not participants:
        return False
    for participant in participants:
        employee = db.get(models.DigitalEmployee, participant.get("employee_no"))
        if employee is None or employee.status != "active":
            return False
    return True


def participant_names(db: Session, conv: models.Conversation) -> list[str]:
    """会话成员展示名。

    participants JSON 里只有 employee_no（name 由接口层按 DigitalEmployee 现查），
    所以这里必须回表取名，否则「搜成员姓名」会永远命中不了。
    """
    names: list[str] = []
    for participant in conv.participants or []:
        name = participant.get("name")
        if not name:
            employee = db.get(models.DigitalEmployee, participant.get("employee_no"))
            name = employee.name if employee is not None else ""
        if name:
            names.append(name)
    return names


def conversation_display_name(db: Session, conv: models.Conversation) -> str:
    """侧栏展示名：群聊取标题，私聊取第一位成员名（私聊 title 恒为空）。"""
    if conv.kind == "group":
        return conv.title or "协作空间"
    names = participant_names(db, conv)
    return names[0] if names else ""


def _name_haystack(db: Session, conv: models.Conversation) -> list[str]:
    return [conversation_display_name(db, conv)] + participant_names(db, conv)


def _latest_previews(db: Session, convs: list[models.Conversation]) -> dict[str, str]:
    """一次查出全部命中会话的最新一条有效消息，作为 last_message 预览（避免逐会话查询）。

    检索结果不需要「协作任务XX」那套预览（那是列表页的口径），这里只取消息正文。
    """
    generations = {conv.id: conv.generation for conv in convs}
    if not generations:
        return {}
    rows = db.scalars(
        select(models.ConversationMessage)
        .where(
            models.ConversationMessage.conversation_id.in_(list(generations)),
            models.ConversationMessage.superseded_at.is_(None),
        )
        .order_by(models.ConversationMessage.seq.desc())
    ).all()
    previews: dict[str, str] = {}
    for row in rows:
        if row.generation != generations.get(row.conversation_id):
            continue
        # seq 倒序，先到的是最新一条；已有则跳过。
        previews.setdefault(row.conversation_id, row.content or "")
    return previews


def search_conversations(db: Session, actor_no: str, q: str, limit: int = 20) -> list[dict]:
    """按关键字检索该员工的会话，返回按会话聚合的命中。

    每个元素：conversation(ORM 对象) / matched_in(name|content) / match_count / match_seq /
    match_role / match_speaker / snippet / last_message。名称命中的会话排在前，
    其余保持 updated_at 倒序（与列表页一致）。
    """
    keyword = (q or "").strip()
    if not keyword:
        return []
    try:
        convs = [
            conv
            for conv in db.scalars(
                select(models.Conversation)
                .where(models.Conversation.owner_human_no == actor_no)
                .order_by(models.Conversation.updated_at.desc())
            ).all()
            if conversation_is_active(db, conv)
        ]
        if not convs:
            return []

        generations = {conv.id: conv.generation for conv in convs}
        pattern = f"%{escape_like(keyword)}%"
        matched = db.scalars(
            select(models.ConversationMessage)
            .where(
                models.ConversationMessage.conversation_id.in_(list(generations)),
                # 被重跑取代的旧回复不参与检索，与界面主视图一致。
                models.ConversationMessage.superseded_at.is_(None),
                models.ConversationMessage.content.like(pattern, escape=_LIKE_ESCAPE),
            )
            .order_by(models.ConversationMessage.seq.asc())
        ).all()

        grouped: dict[str, list[models.ConversationMessage]] = {}
        for row in matched:
            if row.generation != generations.get(row.conversation_id):
                continue
            grouped.setdefault(row.conversation_id, []).append(row)

        lowered = keyword.lower()
        name_hits = {
            conv.id
            for conv in convs
            if any(lowered in (item or "").lower() for item in _name_haystack(db, conv))
        }

        hits: list[dict] = []
        for conv in convs:
            messages = grouped.get(conv.id) or []
            if not messages and conv.id not in name_hits:
                continue
            # 取该会话最大 seq 的命中消息（最近一次提到），同时给出命中条数避免信息丢失。
            latest = messages[-1] if messages else None
            hits.append(
                {
                    "conversation": conv,
                    "matched_in": "name" if conv.id in name_hits else "content",
                    "match_count": len(messages),
                    "match_seq": latest.seq if latest is not None else None,
                    "match_role": latest.role if latest is not None else "",
                    "match_speaker": (
                        latest.participant_name if latest is not None and latest.role == "assistant" else ""
                    ),
                    "snippet": build_snippet(latest.content, keyword) if latest is not None else "",
                }
            )

        # 名称命中置顶；组内保持 updated_at 倒序（hits 是按 convs 顺序追加的，稳定排序即可）。
        hits.sort(key=lambda hit: 0 if hit["matched_in"] == "name" else 1)
        top = hits[:limit]

        previews = _latest_previews(db, [hit["conversation"] for hit in top])
        for hit in top:
            hit["last_message"] = previews.get(hit["conversation"].id, "")
        return top
    except Exception:  # noqa: BLE001 - 只读增强，任何异常都不能冒泡到列表链路
        return []
