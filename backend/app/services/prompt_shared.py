"""跨链路共享的 Prompt 规范、长度上限与 SAFEMODE 护栏（单一来源）。

背景：平台存在两条真实会进模型的链路——
  1) Harness 主链路（`harness_agent` → DockerHarnessRuntimeAdapter → 容器内 dsh）；
  2) ChatOrchestrator 兜底链路（`chat`，非 Harness 员工 / 群聊非 Harness 成员 / 单测）。

两条链路此前各自维护语气、出处引用与检索上限，导致同一聊天框输出风格割裂、口径漂移。
本模块把「语气 / 出处 / 次数上限 / 长度上限 / 外发来源」收敛为唯一来源，两处同时引用。

SAFEMODE：`llm.py` 的校验只覆盖进程内直连模型的调用；Harness 主链路把 prompt 当命令行
参数交给容器内 dsh，不经过 `llm.py`。因此 Harness 侧必须显式调用本模块的
`guard_outbound_prompt`，否则该约定在主链路上只是纸面规则。
"""

from __future__ import annotations

import os

# AI员工统一人设与输出格式：只给角色定位与正向的格式能力，不做逐条文风管控。
# 旧版此处曾禁止一切 Markdown 符号，导致模型不敢用表格/代码块，结构化内容被空格平铺成一段。
TONE_RULES = (
    "【角色】你是一名AI员工，像真人同事一样用中文对话，称呼用户为「你」；"
    "直接给结论，语气自然简洁，不复述问题、不暴露内部思考过程。"
    "【格式】清单、对比、参数或多列数据用 Markdown 表格或列表呈现；"
    "代码、命令、配置用带语言标注的围栏代码块；不要用空格对齐拼凑表格；"
    "不要以【名字】或任何前缀开头，直接说内容。"
)

# 出处引用规范：凡引用检索结果必须标注来源，证据不足必须如实说明。
CITATION_RULES = (
    "【出处】凡依据知识库或附件检索结果作答，必须注明来源（知识库名称或工具返回的 source 标签）；"
    "引用附件结论时用“[文件名 · 第N页/工作表!范围/片段位置]”格式，只能引用工具实际返回的位置；"
    "不得凭记忆编造知识库内容；工具失败、无权限或证据不足时必须明确说明。"
)

# 缺参/缺鉴权规范：工具报缺参时如实说明，不要编造或反复重试。
# 平台会在识别到缺参后弹出「信息补全」气泡，员工补全即自动续跑，因此模型只需说明清楚。
INPUT_RULES = (
    "【缺参】工具返回「需要补充信息 / dwpInputRequired」或提示缺少鉴权头、缺少必填参数时，"
    "不要编造参数值、不要换个工具绕开、也不要反复重试同一个调用；"
    "直接用一句自然语言告诉用户「需要补充什么」（字段名 + 到哪里获取），"
    "并说明「补充后我会自动继续」，平台会弹出输入气泡让用户就地补全。"
    "用户补充后本轮会自动重跑，无需用户再发一次问题。"
)

# 单次任务允许的知识库检索次数上限。
# 工具定义、提示词（limits.knowledge_searches）与后端拦截共用此常量，避免三处漂移。
MAX_KNOWLEDGE_SEARCHES = 5

# 注入任务上下文的技能 SOP 最大字符数；超出截断并在 envelope 标注 sop_truncated。
MAX_PLAYBOOK_CHARS = 4000

# 兜底链路向 system prompt 注入技能清单时的字符上限。
MAX_SKILL_CHARS = 4000

# harness 容器内技能包的投放根目录：已授权技能的 SKILL.md 与 scripts 落在
# `<root>/<slug>/`。runtime_manager 按此投放、harness_agent 按此提示模型、
# profile 的 persona 也按此说明边界，三处口径必须一致。
CONTAINER_SKILLS_DIR = "/workspace/skills"

# SAFEMODE：只有标记为 demo（虚构或已获准外发）的内容才允许进入模型。
SAFE_SOURCE = "demo"

# 绝不允许出现在外发 prompt 中的受控配置项（只比对值，不读取/回显值本身）。
_SECRET_ENV_NAMES = (
    "DEEPSEEK_API_KEY",
    "DWP_INTERNAL_KB_AUTHORIZATION",
    "DWP_HARNESS_TOOL_SIGNING_SECRET",
    "DWP_WECOM_BOT_SECRET",
    "DWP_QIEMAN_MCP_API_KEY",
    "DWP_EHOME_MCP_API_KEY",
)

# 过短的值（如 "1"、"true"）会误伤正常文本，低于该长度不做包含性比对。
_MIN_SECRET_LEN = 8


class SafeModeViolation(Exception):
    """外发 prompt 违反 SAFEMODE：来源未声明为 demo，或疑似夹带受控凭据。"""


def assert_safemode(messages: list[dict]) -> None:
    """SAFEMODE：进入模型的所有消息必须声明 source=demo。"""
    for msg in messages:
        if msg.get("source") != SAFE_SOURCE:
            raise SafeModeViolation("SAFEMODE 拒绝发送：存在非 demo 来源的 prompt 段")


def assert_no_credentials(text: str) -> None:
    """外发文本不得包含受控凭据。

    Harness 主链路把 prompt 作为命令行参数交给容器执行，一旦夹带凭据会出现在
    进程参数里（可被同机进程读取、可能进入容器日志），因此这里做一次硬校验。
    """
    for name in _SECRET_ENV_NAMES:
        value = (os.environ.get(name) or "").strip()
        if len(value) >= _MIN_SECRET_LEN and value in text:
            raise SafeModeViolation(f"SAFEMODE 拒绝发送：prompt 疑似包含受控凭据（{name}）")


def guard_outbound_prompt(text: str, *, source: str = SAFE_SOURCE) -> None:
    """Harness 主链路的外发护栏：来源必须为 demo，且不得夹带凭据。"""
    if source != SAFE_SOURCE:
        raise SafeModeViolation("SAFEMODE 拒绝发送：来源未声明为 demo")
    assert_no_credentials(text)


def truncate_playbook(content: str) -> tuple[str, bool]:
    """截断超长 SOP，返回 (内容, 是否被截断)。"""
    if len(content) <= MAX_PLAYBOOK_CHARS:
        return content, False
    return content[:MAX_PLAYBOOK_CHARS] + "\n……（SOP 过长，已按上限截断）", True
