"""用平台大模型为自助接入的 MCP 服务生成中文简介草稿。

产出**只作草稿**：不落库，返回前端供人工逐项编辑确认；管理员不满意时可以传
``prompt`` 覆盖默认指令重新生成一版。

安全与降级：
- SAFEMODE 要求每条 message 都带 ``source="demo"``（漏加会直接抛
  ``LLMUnavailableError``，见 ``services/prompt_shared.assert_safemode``）；
- **绝不把密钥放进 prompt**：只发送服务名、工具名/说明与入参摘要；
- 工具描述来自外部服务，视为不可信数据，只用于生成文案，不参与任何指令解析；
- 模型不可用时降级为「原样透传 + 规整」，并在返回值里标注降级原因。
"""

from __future__ import annotations

import json
import logging
from typing import Any
from urllib.parse import urlparse

from .llm import DeepSeekProvider, LLMProvider, LLMUnavailableError
from .plugin_governance import MCP_CATEGORIES

logger = logging.getLogger(__name__)

DATA_LEVELS = ("L1", "L2", "L3")
MAX_TOOLS_IN_PROMPT = 40
MAX_DESCRIPTION_LENGTH = 400
MAX_PROMPT_LENGTH = 1000

SYSTEM_PROMPT = (
    "你是企业 AI 员工平台的插件治理助手，负责为外部 MCP 服务补写中文简介。"
    "只输出 JSON，不要输出解释。JSON 字段："
    '{"name": 插件中文名（不超过 24 字）, "description": 插件简介（60~160 字，说明它能解决什么业务问题）,'
    ' "tools": [{"name": 原始工具名, "description": 中文说明（20~60 字，说明何时该调用它）}],'
    ' "mcp_category": 取值必须是 knowledge|cloud_information|fund|internal_system|employee_service|other 之一'
    "（员工自助类服务取 employee_service，企业内部业务系统取 internal_system），"
    ' "data_level": 取值必须是 L1|L2|L3 之一（涉及客户隐私、交易或内部经营数据取 L3，'
    "公开资讯取 L1，其余取 L2）}"
)

DEFAULT_INSTRUCTION = (
    "请阅读下面的 MCP 服务信息，补写中文插件名、中文简介，并为每个工具补写中文说明，"
    "同时给出分类与数据等级建议。工具名必须与输入完全一致，不要增删工具。"
)


def _fallback_name(server_info: dict[str, Any], url: str) -> str:
    title = str(server_info.get("title") or server_info.get("name") or "").strip()
    if title:
        return title[:60]
    host = urlparse(url).hostname or ""
    return f"MCP 服务（{host}）" if host else "MCP 服务"


def _fallback_description(tools: list[dict[str, Any]], url: str) -> str:
    host = urlparse(url).hostname or "外部服务"
    names = [str(tool.get("name") or "") for tool in tools[:5] if tool.get("name")]
    detail = "、".join(names)
    base = f"由 {host} 提供的外部 MCP 服务，共 {len(tools)} 个工具"
    return f"{base}：{detail}。" if detail else f"{base}。"


def _fallback(
    tools: list[dict[str, Any]], server_info: dict[str, Any], url: str, reason: str,
) -> dict[str, Any]:
    """模型不可用时的降级：原样透传 + 规整。"""
    return {
        "name": _fallback_name(server_info, url),
        "description": _fallback_description(tools, url),
        "tools": [
            {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or "暂未提供工具说明")[:MAX_DESCRIPTION_LENGTH],
            }
            for tool in tools
        ],
        "mcp_category": "other",
        "data_level": "L1",
        "generated": False,
        "degraded_reason": reason,
    }


def _build_messages(
    tools: list[dict[str, Any]], server_info: dict[str, Any], url: str, custom_prompt: str | None,
) -> list[dict[str, str]]:
    payload = {
        "service": {
            "url_host": urlparse(url).hostname or "",
            "server_info": server_info or {},
        },
        "tools": [
            {
                "name": str(tool.get("name") or ""),
                "description": str(tool.get("description") or "")[:MAX_DESCRIPTION_LENGTH],
                "parameters": tool.get("parameters") or [],
            }
            for tool in tools[:MAX_TOOLS_IN_PROMPT]
        ],
    }
    instruction = (custom_prompt or "").strip()[:MAX_PROMPT_LENGTH] or DEFAULT_INSTRUCTION
    return [
        {"role": "system", "content": SYSTEM_PROMPT, "source": "demo"},
        {
            "role": "user",
            "content": (
                f"{instruction}\n\n"
                "【服务信息】\n"
                f"{json.dumps(payload, ensure_ascii=False)}"
            ),
            "source": "demo",
        },
    ]


def _merge_tools(tools: list[dict[str, Any]], generated: Any) -> list[dict[str, str]]:
    """以输入工具清单为准合并模型产出的中文说明（模型漏项/多项都按输入口径收敛）。"""
    by_name: dict[str, str] = {}
    if isinstance(generated, list):
        for item in generated:
            if not isinstance(item, dict):
                continue
            key = str(item.get("name") or "").strip()
            if key:
                by_name[key] = str(item.get("description") or "").strip()[:MAX_DESCRIPTION_LENGTH]
    result: list[dict[str, str]] = []
    for tool in tools:
        name = str(tool.get("name") or "")
        result.append({
            "name": name,
            "description": (
                by_name.get(name)
                or str(tool.get("description") or "")[:MAX_DESCRIPTION_LENGTH]
                or "暂未提供工具说明"
            ),
        })
    return result


def generate_draft(
    *,
    tools: list[dict[str, Any]],
    server_info: dict[str, Any] | None = None,
    url: str = "",
    prompt: str | None = None,
    provider: LLMProvider | None = None,
) -> dict[str, Any]:
    """生成中文简介草稿（不落库）。

    ``tools`` 用 ``mcp_onboarding.summarize_tool`` 的摘要结构（含 name/description/parameters）。
    """
    clean_tools = [tool for tool in tools if isinstance(tool, dict) and str(tool.get("name") or "").strip()]
    info = server_info if isinstance(server_info, dict) else {}
    if not clean_tools:
        return _fallback(clean_tools, info, url, "该服务未暴露任何工具，已跳过 AI 生成")
    messages = _build_messages(clean_tools, info, url, prompt)
    try:
        data = (provider or DeepSeekProvider()).structured_output(messages, {"type": "object"})
    except LLMUnavailableError as exc:
        logger.info("MCP 简介生成降级：%s", exc)
        return _fallback(clean_tools, info, url, f"大模型不可用（{exc}），已按原始说明规整")
    except Exception as exc:  # noqa: BLE001 - 生成失败不能阻塞登记流程
        logger.warning("MCP 简介生成异常：%s", exc.__class__.__name__)
        return _fallback(clean_tools, info, url, "大模型生成异常，已按原始说明规整")

    if not isinstance(data, dict):
        return _fallback(clean_tools, info, url, "大模型返回结构非预期，已按原始说明规整")

    name = str(data.get("name") or "").strip()[:60] or _fallback_name(info, url)
    description = str(data.get("description") or "").strip()[:MAX_DESCRIPTION_LENGTH]
    if not description:
        description = _fallback_description(clean_tools, url)
    category = str(data.get("mcp_category") or "").strip()
    if category not in MCP_CATEGORIES:
        category = "other"
    level = str(data.get("data_level") or "").strip().upper()
    if level not in DATA_LEVELS:
        level = "L1"
    return {
        "name": name,
        "description": description,
        "tools": _merge_tools(clean_tools, data.get("tools")),
        "mcp_category": category,
        "data_level": level,
        "generated": True,
        "degraded_reason": None,
    }
