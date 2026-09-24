"""Prompt 规范收敛与 SAFEMODE 护栏的回归测试。"""

import json

import pytest
from fastapi import HTTPException

from app import models
from app.services.harness_agent import _prompt, run_agent
from app.services.memory_service import five_complete_rounds
from app.services.prompt_shared import (
    MAX_KNOWLEDGE_SEARCHES,
    MAX_PLAYBOOK_CHARS,
    SafeModeViolation,
    guard_outbound_prompt,
    truncate_playbook,
)


# ---------- prompt_shared ----------


def test_truncate_playbook_only_flags_overflow():
    short, truncated = truncate_playbook("短 SOP")
    assert short == "短 SOP" and truncated is False

    long_sop = "步" * (MAX_PLAYBOOK_CHARS + 100)
    trimmed, truncated = truncate_playbook(long_sop)
    assert truncated is True
    assert len(trimmed) < len(long_sop)


def test_guard_outbound_prompt_requires_demo_source():
    guard_outbound_prompt("正常内容")
    with pytest.raises(SafeModeViolation):
        guard_outbound_prompt("正常内容", source="internal")


def test_guard_outbound_prompt_rejects_embedded_credentials(monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-verysecretvalue1234")
    with pytest.raises(SafeModeViolation):
        guard_outbound_prompt("请使用 sk-verysecretvalue1234 调用模型")


def test_guard_outbound_prompt_ignores_short_secret_values(monkeypatch):
    # 过短的值（如 "1"）做包含性比对会误伤正常文本
    monkeypatch.setenv("DWP_QIEMAN_MCP_API_KEY", "1")
    guard_outbound_prompt("第 1 步：先检索知识库")


# ---------- 记忆窗口 ----------


def test_five_rounds_keeps_unanswered_previous_question():
    dangling = [{"role": "user", "content": "上一轮没答上的问题"}]
    # 与本轮 goal 不同 → 必须保留，不能静默丢弃
    assert five_complete_rounds(dangling, goal="本轮新问题") == dangling
    # 就是本轮 goal 本身 → 剔除，避免与 TASK_ENVELOPE.goal 重复
    assert five_complete_rounds(dangling, goal="上一轮没答上的问题") == []


# ---------- Harness 主链路 prompt ----------


def _harness_prompt(db_session, *, routed_skill=None, knowledge=None, message="VPN 怎么连"):
    return _prompt(
        employee=db_session.get(models.DigitalEmployee, "AI-GENERAL"),
        profile=db_session.get(models.AgentProfile, "AI-GENERAL"),
        requester="E10281",
        message=message,
        history=[],
        knowledge=knowledge if knowledge is not None else [
            {"id": "KB-IT-SERVICE", "name": "IT 服务知识库", "domain": "IT", "data_level": "L1"}
        ],
        depth=0,
        plugins=[],
        routed_skill=routed_skill,
    )


def _envelope(text: str) -> dict:
    return json.loads(text.split("TASK_ENVELOPE:\n", 1)[1])


def test_harness_prompt_reuses_shared_tone_and_citation_rules(db_session):
    text = _harness_prompt(db_session)
    # 人设 + 格式能力 + 出处规范三条共享规则都必须进入主链路 prompt
    assert "【角色】" in text and "【格式】" in text and "【出处】" in text


def test_harness_prompt_authorizes_local_tools(db_session):
    """prompt 必须显式授权本地工具（bash/文件/检索/待办）。

    回归背景（2026-09-23）：旧文案写「只能使用工具目录中出现的工具」，模型会把
    bash、文件读写理解成「不在工具目录里所以不能用」，本地干活能力形同虚设。
    这里做反向断言，防止旧口径被重新引入。
    """
    text = _harness_prompt(db_session)
    assert "只能使用工具目录中出现的工具" not in text
    # 平台能力仍受工具目录约束（边界不能一起松掉）
    assert "只能调用工具目录中出现的工具" in text
    # 本地工具必须被明确授权
    assert "本地工作工具" in text
    for keyword in ("bash", "读写文件", "检索文件内容", "待办"):
        assert keyword in text
    # 处理表格/文件要走脚本，不要逐格读
    assert "python3" in text and "逐格读取" in text


def test_tone_rules_allow_markdown_but_keep_no_prefix_constraint():
    """TONE_RULES 只保留角色与正向格式说明，不再禁止 Markdown。

    回归背景（2026-09-18）：旧版 TONE_RULES 明令「绝对不要使用任何 Markdown 符号」，
    模型因此不敢输出表格/代码块，结构化内容被空格平铺成一段、代码挤成一行。
    这里做反向断言，防止该禁令被重新引入。
    """
    from app.services.prompt_shared import TONE_RULES

    assert "绝对不要使用任何 Markdown 符号" not in TONE_RULES
    assert "不要机械罗列" not in TONE_RULES
    # 保留的产品级硬约束：气泡里不允许出现【名字】前缀
    assert "不要以【名字】或任何前缀开头" in TONE_RULES
    # 正向能力说明：表格与围栏代码块
    assert "表格" in TONE_RULES and "围栏代码块" in TONE_RULES


def test_group_chat_prompt_no_longer_bans_markdown():
    """群聊内联禁令需与 TONE_RULES 口径一致（保留「不要写任何人的名字或【】前缀」）。"""
    import inspect

    from app.services import group_chat

    source = inspect.getsource(group_chat)
    assert "不要使用任何 Markdown 符号" not in source
    assert "不要写任何人的名字或【】前缀" in source


def test_harness_prompt_kb_catalog_carries_domain_and_limit(db_session):
    envelope = _envelope(_harness_prompt(db_session))
    kb = envelope["authorized_knowledge_bases"][0]
    assert kb["domain"] == "IT" and kb["data_level"] == "L1"
    assert envelope["limits"]["knowledge_searches"] == MAX_KNOWLEDGE_SEARCHES


def test_harness_prompt_truncates_long_playbook(db_session):
    routed = {"name": "基金筛选", "content": "步" * (MAX_PLAYBOOK_CHARS + 500), "score": 3}
    text = _harness_prompt(db_session, routed_skill=routed)
    playbook = _envelope(text)["task_playbook"]
    assert playbook["sop_truncated"] is True
    assert len(playbook["sop"]) < len(routed["content"])
    # 强命中才要求严格遵循 SOP
    assert "不要偏离 SOP" in text


def test_harness_prompt_weak_skill_hit_is_advisory_only(db_session):
    routed = {"name": "基金分析", "content": "步骤一：先取业绩。", "score": 1}
    text = _harness_prompt(db_session, routed_skill=routed)
    assert "仅弱关键词命中" in text
    assert "不要偏离 SOP" not in text
    assert "sop_truncated" not in _envelope(text)["task_playbook"]


def test_run_agent_blocks_prompt_carrying_credentials(db_session, monkeypatch):
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-verysecretvalue1234")
    employee = db_session.get(models.DigitalEmployee, "AI-GENERAL")
    employee.role_prompt = "调试用密钥 sk-verysecretvalue1234"
    db_session.commit()

    with pytest.raises(HTTPException) as exc:
        run_agent(db_session, employee_id="AI-GENERAL", requester_human_no="E10281", message="你好")
    assert exc.value.status_code == 503


def test_run_agent_injects_parent_goal_for_delegated_run(db_session, monkeypatch):
    from app.services.runtime_adapter import RuntimeResult

    captured: dict = {}

    class FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **kwargs):
            captured["prompt"] = kwargs["task_prompt"]
            return RuntimeResult(mode="harness", ok=True, result="好的")

    monkeypatch.setattr("app.services.harness_agent.DockerHarnessRuntimeAdapter", FakeRuntime)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_busy", lambda *_args: None)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_ready", lambda *_args: None)

    run_agent(
        db_session, employee_id="AI-GENERAL", requester_human_no="E10281",
        message="帮我确认 IT 制度", depth=1, parent_goal="报销流程怎么走",
    )
    assert _envelope(captured["prompt"])["parent_task"] == "报销流程怎么走"


def test_run_agent_waits_for_busy_employee_slot(db_session, monkeypatch):
    """slot_wait_seconds>0 时应等待槽位，而不是立刻 409。

    回归背景（2026-09-17）：`run_agent` 的 per-employee 锁是 `acquire(blocking=False)`，
    两个数字分身同时委派同一目标时，后到的那个直接 409 且委派路径不重试 —— 发起方仍返回
    completed，用户拿到的是没有知识库依据的答案。

    这里直接在测试线程里占用/释放 `_lock()`，不额外制造并发 DB 访问
    （内存 SQLite 走 StaticPool 共享单连接，多线程同时用同一个 Session 不安全）。
    """
    import threading
    import time

    from app.services import harness_agent
    from app.services.runtime_adapter import RuntimeResult

    class FakeRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=True, result="好的")

    monkeypatch.setattr("app.services.harness_agent.DockerHarnessRuntimeAdapter", FakeRuntime)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_busy", lambda *_args: None)
    monkeypatch.setattr("app.services.harness_agent.runtime_manager.mark_ready", lambda *_args: None)

    employee_lock = harness_agent._lock("AI-GENERAL")

    # 槽位被占且不等待 → 立即 409（保留「AI员工正忙」语义，前端可据此提示稍后重试）
    assert employee_lock.acquire(blocking=False)
    try:
        with pytest.raises(HTTPException) as exc:
            run_agent(db_session, employee_id="AI-GENERAL", requester_human_no="E10281", message="抢槽位")
        assert exc.value.status_code == 409
    finally:
        employee_lock.release()

    # 槽位被占但有等待预算 → 释放后应继续执行并成功
    assert employee_lock.acquire(blocking=False)

    def release_later():
        time.sleep(1.0)
        employee_lock.release()

    releaser = threading.Thread(target=release_later)
    releaser.start()
    try:
        result = run_agent(
            db_session, employee_id="AI-GENERAL", requester_human_no="E10281",
            message="抢槽位", slot_wait_seconds=5,
        )
    finally:
        releaser.join(timeout=5)
    assert result.message == "好的"


# ---------- 最近对话段落 ----------


def _prompt_with_history(db_session, history, message):
    return _prompt(
        employee=db_session.get(models.DigitalEmployee, "AI-GENERAL"),
        profile=db_session.get(models.AgentProfile, "AI-GENERAL"),
        requester="E10281",
        message=message,
        history=history,
        knowledge=[],
        depth=0,
        plugins=[],
    )


def test_harness_prompt_puts_recent_conversation_before_envelope(db_session):
    """最近对话以纯文本段落注入，且在 TASK_ENVELOPE 之前（不影响既有 envelope 解析）。"""
    text = _prompt_with_history(
        db_session,
        [
            {"role": "user", "content": "帮我查一下 VPN"},
            {"role": "assistant", "content": "已按 IT 服务知识库确认。"},
        ],
        "再改一下，加上 Mac 客户端",
    )
    assert "最近对话" in text
    assert text.index("最近对话") < text.index("TASK_ENVELOPE:\n")
    assert "我：帮我查一下 VPN" in text
    assert "同事：已按 IT 服务知识库确认。" in text
    envelope = _envelope(text)
    # envelope 里只留轮次标记，正文不再重复出现（避免重复占用上下文预算）
    assert envelope["recent_conversation_rounds"] == 1
    assert "recent_conversation" not in envelope


def test_harness_prompt_omits_recent_conversation_when_history_empty(db_session):
    text = _harness_prompt(db_session)
    assert "最近对话" not in text
    assert _envelope(text)["recent_conversation_rounds"] == 0
