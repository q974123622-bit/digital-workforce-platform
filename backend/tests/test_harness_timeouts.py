"""Harness 超时链与本地工具放开的不变量回归。

五处数字（单轮执行超时 / 工具令牌有效期 / 执行过期判定 / 委派等待 / profile 的
工具调用超时）互相约束。历史上它们散落三个文件、靠注释口头约定，改一处不同步
其余几处就会在真机上表现为「超时调大了但委派静默失败」「长任务跑到一半 MCP 全量
401」「用户在别的会话发条消息把正在跑的任务判成超时」—— 都是很难从日志一眼看出的
问题。这里把约束固化成测试。

同时校验 contract.json 的声明值与代码实际值一致：门禁脚本 dsh-contract-check.js
读的是 contract.json，两边漂移等于门禁失效。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from app.services import config

# 容器内跑测试时通常只挂 backend/app 与 backend/tests，deploy/ 不可见；
# 找不到契约/profile 就跳过文件级断言，函数级不变量仍然强制校验。
_CONTRACT_REL = "deploy/offline/ops/baseline/contract.json"
_PROFILE_REL = "deploy/offline/profile/v1-20260910/cordis.patch.yml"

# 超时链里为「令牌校验、事件落库、答复回传」预留的缓冲（秒），与 contract.json 同值。
_BUFFER_SECONDS = 180


def _repo_path(rel: str) -> Path | None:
    here = Path(__file__).resolve()
    for base in (here, *here.parents):
        candidate = base / rel
        if candidate.is_file():
            return candidate
    return None


def _profile_text() -> str:
    path = _repo_path(_PROFILE_REL)
    if path is None:
        pytest.skip(f"当前运行环境看不到 {_PROFILE_REL}")
    return path.read_text(encoding="utf-8")


def _contract() -> dict:
    path = _repo_path(_CONTRACT_REL)
    if path is None:
        pytest.skip(f"当前运行环境看不到 {_CONTRACT_REL}")
    return json.loads(path.read_text(encoding="utf-8"))


# ---------- 函数级不变量 ----------


def test_token_ttl_covers_single_run():
    """令牌有效期必须 >= 单轮上限，否则长任务中途 MCP 调用全量 401。"""
    assert config.harness_token_ttl_seconds() >= config.harness_timeout_max()


def test_stale_threshold_exceeds_single_run():
    """过期判定必须严格大于单轮上限，否则正常长任务会被误杀成 STALE。"""
    assert config.execution_stale_after_seconds() > config.harness_timeout_max()


def test_slot_wait_within_hard_cap():
    assert 0 <= config.delegation_slot_wait_seconds() <= 300


def test_timeout_defaults_are_raised():
    """默认值必须已放开：300/360/60 是旧的「能力被掐住」配置。"""
    assert config.harness_timeout_role() >= 900
    assert config.harness_timeout_twin() >= 1200
    assert config.delegation_slot_wait_seconds() >= 120


# ---------- 契约与 profile 一致性 ----------


def test_contract_declares_same_timeout_numbers():
    """contract.json 的声明值必须与代码实际值一致，否则门禁校验的是过时数字。"""
    invariant = _contract().get("timeout_invariant")
    assert invariant, "contract.json 缺少 timeout_invariant"
    assert invariant["harness_timeout_role"] == config.harness_timeout_role()
    assert invariant["harness_timeout_twin"] == config.harness_timeout_twin()
    assert invariant["delegation_slot_wait_seconds"] == config.delegation_slot_wait_seconds()
    assert invariant["buffer_seconds"] == _BUFFER_SECONDS


def test_profile_tool_call_timeout_covers_backend_chain():
    """profile 的 toolCallTimeoutMs 必须容得下「委派等待 + 被委派方单轮 + 缓冲」。"""
    match = re.search(r"toolCallTimeoutMs:\s*(\d+)", _profile_text())
    assert match, "profile 缺少 toolCallTimeoutMs"
    required_ms = (
        config.delegation_slot_wait_seconds() + config.harness_timeout_max() + _BUFFER_SECONDS
    ) * 1000
    assert int(match.group(1)) >= required_ms


# ---------- 本地工具放开（防回退） ----------

_ENABLED_TOOLS = ("tool-bash", "tool-fs", "tool-fs-search", "tool-todo", "tool-goal")
_DISABLED_TOOLS = ("tool-web", "tool-skill", "tool-jobs", "tool-subagent", "tool-subagent-fork")


def _tool_disabled(text: str, tool_id: str) -> bool | None:
    match = re.search(
        rf"^- id: {re.escape(tool_id)}\n\s+disabled:\s*(true|false)\s*$",
        text,
        flags=re.MULTILINE,
    )
    return None if match is None else match.group(1) == "true"


def test_profile_enables_local_tools_only():
    text = _profile_text()
    for tool_id in _ENABLED_TOOLS:
        assert _tool_disabled(text, tool_id) is False, f"{tool_id} 应处于启用状态"
    for tool_id in _DISABLED_TOOLS:
        assert _tool_disabled(text, tool_id) is True, f"{tool_id} 应保持禁用"


def test_profile_output_budget_raised():
    match = re.search(r"maxTokens:\s*(\d+)", _profile_text())
    assert match, "profile 缺少 maxTokens"
    assert int(match.group(1)) >= 65536


def test_profile_persona_authorizes_local_tools():
    """persona 与平台 prompt 口径必须一致，否则模型收到自相矛盾的指令。"""
    text = _profile_text()
    assert "不得使用工具目录之外的任何资源" not in text
    assert "本地工作工具" in text
    assert "逐格读取" in text
