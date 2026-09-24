"""SkillHub 技能包转换器与导入链路测试。

转换器必须把 ``<slug>/SKILL.md`` + ``<slug>/scripts/**`` 打平成平台可接受的
「根目录 plugin.yaml + SKILL.md + scripts」，且生成的包能直接通过 ``validate_zip``；
导入链路必须复用 ``submit_zip → publish_version`` 并做到幂等与失败留痕。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from sqlalchemy import select

from app import models
from app.services import skillhub_client, skillhub_import
from app.services.plugin_governance import market_plugins, validate_zip

SKILL_MD = """---
name: 文档处理
description: 生成、解析与转换 docx 文档
---

# 文档处理

需要转换格式时执行：

```bash
python scripts/office/pack.py unpacked/ output.docx --original document.docx
```
"""


@pytest.fixture(autouse=True)
def _artifact_root(tmp_path, monkeypatch):
    monkeypatch.setenv("DWP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    yield


def _zip_of(files: dict[str, str]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _skillhub_zip(slug: str = "docx", *, files: dict[str, str] | None = None) -> bytes:
    payload = {
        f"{slug}/SKILL.md": SKILL_MD,
        f"{slug}/scripts/office/pack.py": "print('pack')\n",
        f"{slug}/scripts/office/schema.xsd": "<schema/>\n",
        f"{slug}/LICENSE.txt": "MIT\n",
        "unrelated/README.md": "不属于该技能\n",
    }
    payload.update(files or {})
    return _zip_of(payload)


def _skill(slug: str = "docx", version: str = "0.0.2", **overrides) -> skillhub_client.SkillHubSkill:
    payload = {
        "slug": slug, "display_name": "文档处理", "summary": "生成与解析 docx",
        "version": version, "source": "skillhub", "category": "AI大赛-财富管理赛道",
    }
    payload.update(overrides)
    return skillhub_client.SkillHubSkill(**payload)


# ---- 转换器 ----

def test_convert_flattens_root_and_keeps_scripts():
    converted, meta = skillhub_import.convert_bundle("docx", _skillhub_zip())

    with zipfile.ZipFile(io.BytesIO(converted)) as archive:
        names = set(archive.namelist())
        assert {"plugin.yaml", "SKILL.md"} <= names
        assert "scripts/office/pack.py" in names
        assert "scripts/office/schema.xsd" in names
        assert "LICENSE.txt" in names
        # 打平后不应再保留 <slug>/ 这一层
        assert not any(name.startswith("docx/") for name in names)
        manifest = archive.read("SKILL.md").decode("utf-8")
        assert "python scripts/office/pack.py" in manifest

    assert meta["name"] == "文档处理"
    assert meta["description"] == "生成、解析与转换 docx 文档"
    assert meta["instruction_chars"] == len(SKILL_MD)
    assert meta["dropped"] == ["unrelated/README.md"]
    assert meta["sha256"]


def test_converted_bundle_passes_platform_validation():
    converted, meta = skillhub_import.convert_bundle("docx", _skillhub_zip())
    manifest, digest = validate_zip(converted, "skill", "instruction")

    assert manifest["instruction"].startswith("---")
    assert manifest["slug"] == "docx"
    assert digest == meta["sha256"]


def test_convert_falls_back_to_heading_and_paragraph():
    raw = _skillhub_zip("plain", files={"plain/SKILL.md": "# 行情速览\n\n每日盘后生成行情摘要。\n"})
    _, meta = skillhub_import.convert_bundle("plain", raw)
    assert meta["name"] == "行情速览"
    assert meta["description"] == "每日盘后生成行情摘要。"


def test_convert_rejects_bad_packages():
    with pytest.raises(skillhub_import.SkillHubImportError):
        skillhub_import.convert_bundle("docx", b"not a zip")
    with pytest.raises(skillhub_import.SkillHubImportError):
        skillhub_import.convert_bundle("docx", _zip_of({"docx/README.md": "没有 SKILL.md"}))
    with pytest.raises(skillhub_import.SkillHubImportError):
        skillhub_import.convert_bundle("docx", _zip_of({"../escape.md": "x"}))


def test_import_rejects_forbidden_binary_via_platform_validation(db_session, monkeypatch):
    """带可执行二进制的技能包由 ``validate_zip`` 拦下，导入记录如实写失败原因。"""
    _fake_download(monkeypatch, _zip_of({"docx/SKILL.md": SKILL_MD, "docx/tool.exe": "MZ"}))
    result = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")
    assert result["status"] == "failed"
    assert "平台校验未通过" in result["message"]
    assert skillhub_import.imported_slugs(db_session) == {}


# ---- 导入链路 ----

def _fake_download(monkeypatch, raw: bytes) -> dict:
    calls: dict = {"count": 0}

    def _download(*, slug: str, version: str, source: str) -> bytes:
        calls.update({"count": calls["count"] + 1, "slug": slug, "version": version, "source": source})
        return raw

    monkeypatch.setattr(skillhub_client, "download_bundle", _download)
    return calls


def test_import_skill_publishes_and_lands_on_shelf(db_session, monkeypatch):
    calls = _fake_download(monkeypatch, _skillhub_zip())

    result = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")

    assert result["status"] == "imported"
    assert result["plugin_id"].startswith("SKILL-")
    assert result["file_count"] >= 4
    assert calls["version"] == "0.0.2" and calls["source"] == "skillhub"

    plugin = db_session.get(models.Plugin, result["plugin_id"])
    assert plugin is not None and plugin.plugin_type == "skill"
    assert plugin.scope == "shared" and plugin.status == "active"
    version = db_session.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id))
    assert version.publish_status == "published" and version.review_status == "approved"
    assert version.manifest["instruction"].startswith("---")

    # 全可见货架：任何员工都能看到，且处于「可申请」态。
    shelf = market_plugins(db_session, "DT-E10281", "E10281")
    item = next(row for row in shelf if row["plugin_id"] == plugin.id)
    assert item["state"] == "requestable"
    assert item["plugin_type"] == "skill"


def test_import_skill_expands_artifact_directory(db_session, monkeypatch):
    _fake_download(monkeypatch, _skillhub_zip())
    result = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")

    from app.services.plugin_governance import artifact_root

    expanded = artifact_root() / "skills" / "docx"
    assert (expanded / "SKILL.md").is_file()
    assert (expanded / "scripts" / "office" / "pack.py").is_file()
    assert (expanded / ".dwp-source-sha256").read_text(encoding="utf-8").strip() == result["sha256"]


def test_import_skill_is_idempotent(db_session, monkeypatch):
    calls = _fake_download(monkeypatch, _skillhub_zip())
    first = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")
    second = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")

    assert first["status"] == "imported"
    assert second["status"] == "skipped"
    assert second["plugin_id"] == first["plugin_id"]
    assert calls["count"] == 1, "同版本重复导入不应再次下载"


def test_import_skill_records_failure(db_session, monkeypatch):
    def _boom(**_):
        raise skillhub_client.SkillHubError("SkillHub 请求失败：ConnectError")

    monkeypatch.setattr(skillhub_client, "download_bundle", _boom)
    result = skillhub_import.import_skill(db_session, skill=_skill(), actor="SYS-ADMIN")

    assert result["status"] == "failed"
    assert "ConnectError" in result["message"]
    assert skillhub_import.imported_slugs(db_session) == {}


def test_import_skill_skips_download_disabled(db_session, monkeypatch):
    calls = _fake_download(monkeypatch, _skillhub_zip())
    result = skillhub_import.import_skill(
        db_session, skill=_skill(download_disabled=True), actor="SYS-ADMIN")
    assert result["status"] == "failed"
    assert "不可下载" in result["message"]
    assert calls["count"] == 0


# ---- 分类 / 推荐位 / 中文简介 ----

def test_import_persists_category_and_featured_from_tags(db_session, monkeypatch):
    """远端 category 恒为 null，分类与推荐位只能来自 tags。"""
    _fake_download(monkeypatch, _skillhub_zip("kline-query"))
    skill = _skill("kline-query", category="", tags=("数据分析", "推荐"), summary="查询 K 线行情数据")

    result = skillhub_import.import_skill(
        db_session, skill=skill, actor="SYS-ADMIN", generate_intro=False)

    plugin = db_session.get(models.Plugin, result["plugin_id"])
    assert plugin.skill_category == "数据分析"
    assert plugin.featured is True
    shelf = market_plugins(db_session, "DT-E10281", "E10281")
    assert next(row for row in shelf if row["plugin_id"] == plugin.id)["featured"] is True


def test_import_batch_id_lands_on_records(db_session, monkeypatch):
    _fake_download(monkeypatch, _skillhub_zip())
    result = skillhub_import.import_skill(
        db_session, skill=_skill(), actor="SYS-ADMIN", generate_intro=False, batch_id="SHB-TEST")
    assert result["batch_id"] == "SHB-TEST"


EN_SKILL_MD = (
    "---\nname: docx\ndescription: "
    + "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents. " * 4
    + "\n---\n\n# docx\n\nRun python3 scripts/pack.py.\n"
)
EN_SUMMARY = (
    "Use this skill whenever the user wants to create, read, edit, or manipulate Word documents. "
    "Triggers include any mention of Word doc."
)


class _FakeProvider:
    """只回一个固定 JSON 的假模型，避免测试打真实网关。"""

    def __init__(self, payload: dict) -> None:
        self.payload = payload

    def structured_output(self, messages, schema):  # noqa: ANN001, ARG002
        assert all(message.get("source") == "demo" for message in messages), "SAFEMODE 要求每条 message 带 source"
        return self.payload


class _BoomProvider:
    def structured_output(self, messages, schema):  # noqa: ANN001, ARG002
        from app.services.llm import LLMUnavailableError

        raise LLMUnavailableError("未配置模型网关")


def test_import_generates_chinese_intro_when_description_needs_it(db_session, monkeypatch):
    _fake_download(monkeypatch, _skillhub_zip("docx", files={"docx/SKILL.md": EN_SKILL_MD}))
    provider = _FakeProvider({"description": "用于生成、解析与转换 Word 文档，适合文档处理类任务。", "category": "办公效率"})

    result = skillhub_import.import_skill(
        db_session, skill=_skill("docx", category="", tags=("办公效率",), summary=EN_SUMMARY),
        actor="SYS-ADMIN", intro_provider=provider,
    )

    assert result["status"] == "imported"
    assert "已补中文简介" in result["message"]
    plugin = db_session.get(models.Plugin, result["plugin_id"])
    assert plugin.description == "用于生成、解析与转换 Word 文档，适合文档处理类任务。"
    assert plugin.skill_category == "办公效率"
    assert plugin.runtime_meta["intro"]["generated"] is True


def test_import_intro_degrades_when_model_unavailable(db_session, monkeypatch):
    _fake_download(monkeypatch, _skillhub_zip("docx", files={"docx/SKILL.md": EN_SKILL_MD}))

    result = skillhub_import.import_skill(
        db_session, skill=_skill("docx", category="", tags=("办公效率",), summary=EN_SUMMARY),
        actor="SYS-ADMIN", intro_provider=_BoomProvider(),
    )

    assert result["status"] == "imported", "降级不能把导入判成失败"
    assert "简介回落原文首句" in result["message"]
    plugin = db_session.get(models.Plugin, result["plugin_id"])
    assert plugin.description.startswith("Use this skill whenever")
    assert plugin.skill_category == "办公效率"
    assert plugin.runtime_meta["intro"]["generated"] is False


def test_import_keeps_chinese_description_untouched(db_session, monkeypatch):
    """中文短简介质量已经很好，不该调模型（provider 一旦被调用就会抛）。"""
    _fake_download(monkeypatch, _skillhub_zip())
    result = skillhub_import.import_skill(
        db_session, skill=_skill(), actor="SYS-ADMIN", intro_provider=_BoomProvider(),
    )
    assert result["status"] == "imported"
    assert "简介" not in result["message"]
    plugin = db_session.get(models.Plugin, result["plugin_id"])
    assert plugin.description == "生成、解析与转换 docx 文档"
