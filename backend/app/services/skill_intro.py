"""用平台大模型为 SkillHub 技能补写中文简介与业务分类。

为什么需要：SkillHub 上约 37% 的技能简介是英文（``docx``/``pdf``/``pptx`` 这批办公
文档技能尤其明显），最长的接近 1000 字。员工侧货架直接渲染这段文字，体验很差。

策略：**只对不含中文或过长的简介调模型**，中文简介质量本来就好的技能不浪费额度。
失败或模型不可用时降级为「原文首句」，并在 provenance 里标注降级原因。

安全与降级（与 ``services/mcp_intro.py`` 完全同口径）：

- SAFEMODE 要求每条 message 都带 ``source="demo"``（漏加会抛 ``LLMUnavailableError``）；
- **绝不把密钥放进 prompt**：只发送技能名、slug、部门、tags、上游 summary 与
  SKILL.md 正文摘录；
- SKILL.md 正文来自外部仓库，视为不可信数据，只用于生成文案，不参与指令解析。
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime
from typing import Any, Iterable

from .. import models
from . import skillhub_client
from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError

logger = logging.getLogger(__name__)

# 超过这个长度就认为简介冗长，需要收敛成一句话。
LONG_DESCRIPTION_CHARS = 200
# 生成结果的硬上限（模型偶尔会写长）。
MAX_DESCRIPTION_LENGTH = 200
# 送进 prompt 的原文上限：summary 与 SKILL.md 正文各截一段，避免把上下文撑爆。
MAX_SUMMARY_CHARS = 800
MAX_INSTRUCTION_CHARS = 2000
MAX_PROMPT_CHARS = 1000
_CHINESE_RE = re.compile(r"[\u4e00-\u9fff]")

SYSTEM_PROMPT = (
    "你是企业 AI 员工平台的技能治理助手，负责为内网技能市场的技能补写中文简介。"
    "只输出 JSON，不要输出解释。JSON 字段："
    '{"description": 技能简介（40~120 字，说清它能解决什么业务问题、什么场景该用它，不要罗列文件名）,'
    ' "category": 业务分类，取值必须是给定候选分类中的一项，无法判断时给空串}'
)

DEFAULT_INSTRUCTION = (
    "请阅读下面的技能信息，用中文写一段简介，并从候选分类里挑一个最贴切的业务分类。"
)


def has_chinese(text: str | None) -> bool:
    return bool(_CHINESE_RE.search(text or ""))


def needs_intro(description: str | None) -> bool:
    """判断简介是否需要生成：空、不含中文、或超过 200 字。

    中文且简短的简介（如种子技能）质量已经很好，不调模型。
    """
    text = (description or "").strip()
    if not text:
        return True
    if len(text) > LONG_DESCRIPTION_CHARS:
        return True
    return not has_chinese(text)


def _first_sentence(text: str, limit: int = MAX_DESCRIPTION_LENGTH) -> str:
    """降级用：取第一句（中英文句读都算，保留原句读符号），再截断。"""
    cleaned = " ".join((text or "").split())
    if not cleaned:
        return ""
    parts = re.split(r"([。！？!?])", cleaned, maxsplit=1)
    head = (parts[0] or cleaned).strip()
    if len(parts) > 1 and parts[1]:
        head = f"{head}{parts[1]}"
    elif head and head[-1] not in "。.！!？?":
        head = f"{head}。"
    return head[:limit]


def _fallback(
    *, name: str, summary: str, instruction: str, tags: Iterable[str] | None, reason: str,
) -> dict[str, Any]:
    """模型不可用时的降级：原文首句 + tags 映射分类。"""
    description = _first_sentence(summary) or _first_sentence(instruction)
    if not description:
        description = f"来自内网技能市场的技能 {name}。"
    return {
        "description": description,
        "category": skillhub_client.skill_category_from_tags(tags),
        "generated": False,
        "degraded_reason": reason,
    }


def _build_messages(
    *,
    name: str,
    slug: str,
    summary: str,
    department: str,
    tags: Iterable[str] | None,
    instruction: str,
    categories: Iterable[str] | None,
    custom_prompt: str | None,
) -> list[dict[str, str]]:
    payload = {
        "skill": {
            "name": name,
            "slug": slug,
            "department": department,
            "tags": list(tags or ()),
            "summary": (summary or "")[:MAX_SUMMARY_CHARS],
            "skill_md_excerpt": (instruction or "")[:MAX_INSTRUCTION_CHARS],
        },
        "candidate_categories": list(categories or skillhub_client.DEFAULT_SKILL_CATEGORIES),
    }
    instruction_text = (custom_prompt or "").strip()[:MAX_PROMPT_CHARS] or DEFAULT_INSTRUCTION
    return [
        {"role": "system", "content": SYSTEM_PROMPT, "source": "demo"},
        {
            "role": "user",
            "content": f"{instruction_text}\n\n【技能信息】\n{json.dumps(payload, ensure_ascii=False)}",
            "source": "demo",
        },
    ]


def generate_draft(
    *,
    name: str,
    slug: str = "",
    summary: str = "",
    department: str = "",
    tags: Iterable[str] | None = None,
    instruction: str = "",
    categories: Iterable[str] | None = None,
    prompt: str | None = None,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """生成中文简介草稿（不落库）。

    返回 ``{"description", "category", "generated", "degraded_reason"}``；
    ``generated=False`` 表示走了降级路径，``degraded_reason`` 说明原因。
    """
    clean_name = (name or "").strip() or slug or "未命名技能"
    messages = _build_messages(
        name=clean_name, slug=slug, summary=summary, department=department,
        tags=tags, instruction=instruction, categories=categories, custom_prompt=prompt,
    )
    try:
        data = (provider or DeepSeekProvider()).structured_output(messages, {"type": "object"})
    except LLMUnavailableError as exc:
        logger.info("技能简介生成降级：%s", exc)
        return _fallback(
            name=clean_name, summary=summary, instruction=instruction, tags=tags,
            reason=f"大模型不可用（{exc}），已回落原文首句",
        )
    except Exception as exc:  # noqa: BLE001 - 生成失败不能阻塞导入
        logger.warning("技能简介生成异常：%s", exc.__class__.__name__)
        return _fallback(
            name=clean_name, summary=summary, instruction=instruction, tags=tags,
            reason="大模型生成异常，已回落原文首句",
        )

    if not isinstance(data, dict):
        return _fallback(
            name=clean_name, summary=summary, instruction=instruction, tags=tags,
            reason="大模型返回结构非预期，已回落原文首句",
        )

    description = str(data.get("description") or "").strip()[:MAX_DESCRIPTION_LENGTH]
    if not description:
        return _fallback(
            name=clean_name, summary=summary, instruction=instruction, tags=tags,
            reason="大模型未给出简介，已回落原文首句",
        )
    allowed = set(categories or skillhub_client.DEFAULT_SKILL_CATEGORIES)
    category = str(data.get("category") or "").strip()
    if category not in allowed:
        # 模型没给出可用分类时，回落到 tags 映射，避免把脏值写进库。
        category = skillhub_client.skill_category_from_tags(tags, vocabulary=allowed)
    return {
        "description": description,
        "category": category,
        "generated": True,
        "degraded_reason": None,
    }


def apply_category_only(plugin: models.Plugin, draft: dict[str, Any]) -> bool:
    """只补业务分类，**不动简介**。

    用于「简介本身没问题、只是没分类」的技能：这类技能的中文简介质量已经很好，
    用模型重写反而更差，但分类可以让模型判断。返回是否产生了变化。
    """
    if plugin.skill_category:
        return False
    category = str(draft.get("category") or "").strip()
    if not category:
        return False
    plugin.skill_category = category
    meta = dict(plugin.runtime_meta or {})
    intro = dict(meta.get("intro") or {})
    intro["category_source"] = "ai" if draft.get("generated") else "tags"
    intro["categorized_at"] = datetime.now().isoformat(timespec="seconds")
    meta["intro"] = intro
    plugin.runtime_meta = meta
    return True


def apply_intro(plugin: models.Plugin, draft: dict[str, Any]) -> bool:
    """把草稿写进 ``Plugin``（不提交事务）；返回是否产生了变化。

    ``runtime_meta['intro']`` 记录 provenance，便于管理端区分「AI 生成」与
    「原文透传」。上游原文不会丢：它仍在 ``PluginVersion.manifest['description']``。
    """
    changed = False
    description = str(draft.get("description") or "").strip()
    if description and plugin.description != description:
        plugin.description = description
        changed = True
    category = str(draft.get("category") or "").strip()
    if category and plugin.skill_category != category:
        plugin.skill_category = category
        changed = True
    meta = dict(plugin.runtime_meta or {})
    meta["intro"] = {
        "generated": bool(draft.get("generated")),
        "degraded_reason": draft.get("degraded_reason"),
        "generated_at": datetime.now().isoformat(timespec="seconds"),
    }
    plugin.runtime_meta = meta
    return changed
