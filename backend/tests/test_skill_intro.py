"""技能中文简介生成服务测试。

只覆盖「该不该调模型」与三种降级分支，不打真实模型网关。
"""

from __future__ import annotations

import os

import pytest

from app import models
from app.services import llm, skill_intro
from app.services.llm import LLMUnavailableError


class _FakeProvider:
    def __init__(self, payload=None, error: Exception | None = None) -> None:
        self.payload = payload
        self.error = error
        self.messages: list[dict] | None = None

    def structured_output(self, messages, schema):  # noqa: ANN001, ARG002
        self.messages = messages
        if self.error is not None:
            raise self.error
        return self.payload


def test_needs_intro_detects_empty_english_and_long():
    assert skill_intro.needs_intro("") is True
    assert skill_intro.needs_intro(None) is True
    assert skill_intro.needs_intro("Use this skill to create Word documents.") is True
    assert skill_intro.needs_intro("基" * (skill_intro.LONG_DESCRIPTION_CHARS + 1)) is True
    # 中文且简短的简介质量已经很好，不该调模型
    assert skill_intro.needs_intro("按业绩与风险偏好筛出候选基金，并给出持仓建议。") is False


def test_generate_draft_returns_chinese_description_and_category():
    provider = _FakeProvider({"description": "用于行情复盘与盘后摘要生成。", "category": "数据分析"})

    draft = skill_intro.generate_draft(
        name="market-brief", slug="market-brief", summary="Market brief generator.",
        tags=("数据分析",), instruction="# Market brief", provider=provider,
    )

    assert draft == {
        "description": "用于行情复盘与盘后摘要生成。",
        "category": "数据分析",
        "generated": True,
        "degraded_reason": None,
    }
    # SAFEMODE：每条 message 都必须带 source
    assert all(message.get("source") == "demo" for message in provider.messages or [])


def test_generate_draft_falls_back_to_first_sentence_on_llm_error():
    provider = _FakeProvider(error=LLMUnavailableError("未配置模型网关"))

    draft = skill_intro.generate_draft(
        name="docx", slug="docx",
        summary="Use this skill to create Word documents. Triggers include Word doc.",
        tags=("办公效率",), provider=provider,
    )

    assert draft["generated"] is False
    assert "大模型不可用" in draft["degraded_reason"]
    assert draft["description"].startswith("Use this skill to create Word documents")
    # 降级时分类仍靠 tags 映射，不落空
    assert draft["category"] == "办公效率"


def test_generate_draft_falls_back_when_model_returns_empty_description():
    provider = _FakeProvider({"description": "  ", "category": "办公效率"})

    draft = skill_intro.generate_draft(name="docx", summary="生成 Word 文档。", provider=provider)

    assert draft["generated"] is False
    assert "未给出简介" in draft["degraded_reason"]
    assert draft["description"] == "生成 Word 文档。"


def test_generate_draft_rejects_category_outside_vocabulary():
    provider = _FakeProvider({"description": "用于行情复盘。", "category": "模型编的分类"})

    draft = skill_intro.generate_draft(
        name="market-brief", summary="生成盘后摘要。", tags=("数据分析",), provider=provider,
    )

    assert draft["category"] == "数据分析", "分类不在词表内时回落到 tags 映射"


def test_generate_draft_tolerates_non_dict_payload():
    draft = skill_intro.generate_draft(name="docx", summary="生成 Word 文档。", provider=_FakeProvider(["nope"]))
    assert draft["generated"] is False
    assert "结构非预期" in draft["degraded_reason"]


def test_apply_intro_writes_description_category_and_provenance():
    plugin = models.Plugin(
        id="SKILL-TEST", name="docx", type="skill", plugin_type="skill",
        description="Use this skill to create Word documents.",
        runtime_meta={}, data_level="L1", status="active",
    )

    changed = skill_intro.apply_intro(plugin, {
        "description": "用于生成与解析 Word 文档。", "category": "办公效率",
        "generated": True, "degraded_reason": None,
    })

    assert changed is True
    assert plugin.description == "用于生成与解析 Word 文档。"
    assert plugin.skill_category == "办公效率"
    assert plugin.runtime_meta["intro"]["generated"] is True
    assert plugin.runtime_meta["intro"]["generated_at"]


def test_apply_intro_is_idempotent():
    plugin = models.Plugin(
        id="SKILL-TEST", name="docx", type="skill", plugin_type="skill",
        description="用于生成与解析 Word 文档。", runtime_meta={}, data_level="L1", status="active",
    )

    changed = skill_intro.apply_intro(plugin, {
        "description": "用于生成与解析 Word 文档。", "category": "",
        "generated": False, "degraded_reason": "大模型不可用",
    })

    assert changed is False
    assert plugin.runtime_meta["intro"]["degraded_reason"] == "大模型不可用"


def test_ensure_api_key_prefers_environment(monkeypatch):
    monkeypatch.setenv(llm.DEEPSEEK_API_KEY, "from-env")
    assert llm.ensure_api_key(allow_secret_file=True) == "env"


def test_ensure_api_key_falls_back_to_mounted_secret(monkeypatch, tmp_path):
    """运维 CLI 以 docker exec 起独立进程，拿不到 entrypoint 注入的环境变量。"""
    monkeypatch.delenv(llm.DEEPSEEK_API_KEY, raising=False)
    secret = tmp_path / "llm_api_key"
    secret.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv(llm.DEEPSEEK_API_KEY_FILE, str(secret))

    assert llm.ensure_api_key(allow_secret_file=True) == "secret_file"
    assert os.environ[llm.DEEPSEEK_API_KEY] == "from-file"


def test_ensure_api_key_ignores_secret_file_unless_allowed(monkeypatch, tmp_path):
    monkeypatch.delenv(llm.DEEPSEEK_API_KEY, raising=False)
    secret = tmp_path / "llm_api_key"
    secret.write_text("from-file\n", encoding="utf-8")
    monkeypatch.setenv(llm.DEEPSEEK_API_KEY_FILE, str(secret))

    assert llm.ensure_api_key() == "missing"


def test_ensure_api_key_reports_missing(monkeypatch, tmp_path):
    monkeypatch.delenv(llm.DEEPSEEK_API_KEY, raising=False)
    monkeypatch.setenv(llm.DEEPSEEK_API_KEY_FILE, str(tmp_path / "nope"))

    assert llm.ensure_api_key(allow_secret_file=True) == "missing"


def test_apply_category_only_keeps_existing_description():
    plugin = models.Plugin(
        id="SKILL-TEST", name="基金筛选", type="skill", plugin_type="skill",
        description="按业绩与风险偏好筛出候选基金。", skill_category=None,
        runtime_meta={}, data_level="L1", status="active",
    )

    changed = skill_intro.apply_category_only(plugin, {
        "description": "模型写的新简介（不该被采用）", "category": "数据分析", "generated": True,
    })

    assert changed is True
    assert plugin.description == "按业绩与风险偏好筛出候选基金。"
    assert plugin.skill_category == "数据分析"
    assert plugin.runtime_meta["intro"]["category_source"] == "ai"


def test_apply_category_only_never_overwrites_existing_category():
    plugin = models.Plugin(
        id="SKILL-TEST", name="基金筛选", type="skill", plugin_type="skill",
        description="按业绩筛基金。", skill_category="办公效率",
        runtime_meta={}, data_level="L1", status="active",
    )

    assert skill_intro.apply_category_only(plugin, {"category": "数据分析", "generated": True}) is False
    assert plugin.skill_category == "办公效率"


def test_apply_category_only_ignores_empty_category():
    plugin = models.Plugin(
        id="SKILL-TEST", name="未命名", type="skill", plugin_type="skill",
        description="说明", skill_category=None, runtime_meta={}, data_level="L1", status="active",
    )
    assert skill_intro.apply_category_only(plugin, {"category": "", "generated": True}) is False
    assert plugin.skill_category is None


@pytest.mark.parametrize("text,expected", [
    ("第一句。第二句。", "第一句。"),
    ("First sentence! Second one.", "First sentence!"),
    ("", ""),
])
def test_first_sentence_helper(text, expected):
    assert skill_intro._first_sentence(text) == expected
