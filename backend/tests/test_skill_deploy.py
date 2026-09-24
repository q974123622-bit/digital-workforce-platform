"""技能包投放进 harness 容器工作区的测试。

「已授权」必须在容器里可执行才算数：技能包（SKILL.md + scripts）要落到
``/workspace/skills/<slug>/``，且投放是幂等的、撤回授权后会被回收。
"""

from __future__ import annotations

import io
import json
import zipfile

import pytest
from sqlalchemy import select

from app import models
from app.services import runtime_manager, skillhub_client, skillhub_import
from app.services.plugin_governance import (
    effective_skill_artifacts,
    effective_skills,
    skill_package_dir,
)

SKILL_MD = """---
name: 文档处理
description: 生成、解析与转换 docx 文档
---

# 文档处理

```bash
python3 scripts/office/pack.py unpacked/ output.docx
```
"""

AGENT = "DT-E10281"


@pytest.fixture(autouse=True)
def _isolated_roots(tmp_path, monkeypatch):
    monkeypatch.setenv("DWP_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setattr(runtime_manager, "REPO_ROOT", tmp_path / "repo")
    yield


def _bundle(slug: str = "docx") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{slug}/SKILL.md", SKILL_MD)
        archive.writestr(f"{slug}/scripts/office/pack.py", "print('pack')\n")
    return buffer.getvalue()


def _publish_skill(db_session, slug: str = "docx") -> str:
    monkeypatch_target = skillhub_client.download_bundle
    skillhub_client.download_bundle = lambda **_: _bundle(slug)
    try:
        result = skillhub_import.import_skill(
            db_session,
            skill=skillhub_client.SkillHubSkill(
                slug=slug, display_name="文档处理", summary="生成与解析 docx",
                version="0.0.2", source="skillhub",
            ),
            actor="SYS-ADMIN",
        )
    finally:
        skillhub_client.download_bundle = monkeypatch_target
    assert result["status"] == "imported"
    return result["plugin_id"]


def _bind(db_session, plugin_id: str, agent_id: str = AGENT, **overrides) -> models.AgentPluginBinding:
    binding = models.AgentPluginBinding(
        plugin_id=plugin_id, target_agent_id=agent_id, authorized_by="SYS-ADMIN",
        **overrides,
    )
    db_session.add(binding)
    db_session.commit()
    return binding


def test_artifact_resolution_and_package_dir(db_session):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)

    artifacts = effective_skill_artifacts(db_session, AGENT)
    assert [item["slug"] for item in artifacts] == ["docx"]
    assert artifacts[0]["sha256"]

    skills = effective_skills(db_session, AGENT)
    assert skills[0]["package_dir"] == "/workspace/skills/docx"

    plugin = db_session.get(models.Plugin, plugin_id)
    version = db_session.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin_id))
    assert skill_package_dir(plugin, version) == "/workspace/skills/docx"


def test_deploy_skills_copies_and_is_idempotent(db_session):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)

    first = runtime_manager.deploy_skills(db_session, AGENT)
    assert first["deployed"] == ["docx"] and first["failed"] == []

    target = runtime_manager.skills_root(AGENT) / "docx"
    assert (target / "SKILL.md").read_text(encoding="utf-8") == SKILL_MD
    assert (target / "scripts" / "office" / "pack.py").is_file()

    manifest = json.loads(
        (runtime_manager.skills_root(AGENT) / runtime_manager._DEPLOY_MANIFEST).read_text(encoding="utf-8"))
    assert set(manifest) == {"docx"}

    second = runtime_manager.deploy_skills(db_session, AGENT)
    assert second["skipped"] == ["docx"] and second["deployed"] == []


def test_deploy_skills_revokes_unauthorized_skill(db_session):
    plugin_id = _publish_skill(db_session)
    binding = _bind(db_session, plugin_id)
    runtime_manager.deploy_skills(db_session, AGENT)
    assert (runtime_manager.skills_root(AGENT) / "docx").is_dir()

    binding.admin_enabled = False
    db_session.commit()
    result = runtime_manager.deploy_skills(db_session, AGENT)

    assert result["removed"] == ["docx"]
    assert not (runtime_manager.skills_root(AGENT) / "docx").exists()


def test_deploy_skills_never_raises(db_session, monkeypatch):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)

    def _boom(*_, **__):
        raise RuntimeError("制品库挂了")

    monkeypatch.setattr(
        "app.services.plugin_governance.effective_skill_artifacts", _boom)
    result = runtime_manager.deploy_skills(db_session, AGENT)
    assert result["failed"] == ["*"]


def test_enable_endpoint_triggers_deploy(client, db_session):
    """「启用」才是技能真正生效的时刻：这一步必须同步把技能包投放到容器工作区。

    只绑不发（审批只建 admin_enabled 绑定）时容器里不该有包；员工点启用后才落地。
    这是端到端验证里真实踩到的缺口。
    """
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id, employee_enabled=False)
    assert not (runtime_manager.skills_root(AGENT) / "docx").exists()

    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-e10281"},
    ).status_code == 200
    response = client.post(f"/api/v1/my/plugins/{plugin_id}/enable", json={})

    assert response.status_code == 200
    assert (runtime_manager.skills_root(AGENT) / "docx" / "SKILL.md").is_file()
    assert (runtime_manager.skills_root(AGENT) / "docx" / "scripts" / "office" / "pack.py").is_file()


def test_disable_endpoint_reclaims_skill_package(client, db_session):
    """停用即回收：容器里不该留下已停用技能的脚本。"""
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)
    runtime_manager.deploy_skills(db_session, AGENT)
    assert (runtime_manager.skills_root(AGENT) / "docx").is_dir()

    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-e10281"},
    ).status_code == 200
    assert client.post(f"/api/v1/my/plugins/{plugin_id}/disable", json={}).status_code == 200

    assert not (runtime_manager.skills_root(AGENT) / "docx").exists()


# -- 投放态（真源 = 工作区文件系统，不进数据库）-------------------------------


def test_deploy_state_missing_then_deployed(db_session):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)

    # 还没投放：授权没问题，但容器工作区里没有包
    state = runtime_manager.deploy_state(db_session, AGENT)
    assert state["docx"]["state"] == "missing"
    assert state["docx"]["detail"]

    runtime_manager.deploy_skills(db_session, AGENT)
    state = runtime_manager.deploy_state(db_session, AGENT)
    assert state["docx"]["state"] == "deployed"
    assert state["docx"]["deployed_at"]


def test_deploy_state_marks_stale_when_workspace_drifts(db_session):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)
    runtime_manager.deploy_skills(db_session, AGENT)

    # 模拟「授权版本换了 / 工作区被改」：目录里的 sha 标记与授权版本不一致
    marker = runtime_manager.skills_root(AGENT) / "docx" / ".dwp-source-sha256"
    marker.write_text("deadbeef", encoding="utf-8")
    state = runtime_manager.deploy_state(db_session, AGENT)
    assert state["docx"]["state"] == "stale"


def test_deploy_state_reports_failure_reason(db_session, monkeypatch):
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)

    def _boom(*_, **__):
        raise OSError("磁盘写满")

    monkeypatch.setattr(runtime_manager.shutil, "copytree", _boom)
    result = runtime_manager.deploy_skills(db_session, AGENT)
    assert result["failed"] == ["docx"]

    state = runtime_manager.deploy_state(db_session, AGENT)
    assert state["docx"]["state"] == "failed"
    assert "磁盘写满" in state["docx"]["detail"]


def test_deploy_state_reports_removed_after_revoke(db_session):
    plugin_id = _publish_skill(db_session)
    binding = _bind(db_session, plugin_id)
    runtime_manager.deploy_skills(db_session, AGENT)

    binding.admin_enabled = False
    db_session.commit()
    runtime_manager.deploy_skills(db_session, AGENT)

    state = runtime_manager.deploy_state(db_session, AGENT)
    assert state["docx"]["state"] == "removed"


def test_effective_capabilities_expose_deploy_state(client, db_session):
    """核验页与档案页共用同一份字段：技能能力项带投放态、失败原因与时间。"""
    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)
    runtime = runtime_manager.ensure_runtime(db_session, AGENT)
    runtime.state = "ready"
    db_session.commit()

    def _skill_item() -> dict:
        response = client.get(f"/api/v1/agents/{AGENT}/effective-capabilities")
        assert response.status_code == 200, response.text
        return next(
            item for item in response.json()["capabilities"]
            if item["id"] == f"skill:{plugin_id}"
        )

    item = _skill_item()
    assert item["deploy_state"] == "missing"
    assert item["installed"] is False
    # 授权与版本都就绪、但包没落到容器里 → 实际用不了，按运行环境问题呈现
    assert item["status"] == "runtime_unavailable"

    runtime_manager.deploy_skills(db_session, AGENT)
    item = _skill_item()
    assert item["deploy_state"] == "deployed"
    assert item["installed"] is True
    assert item["status"] == "available"
    assert item["deployed_at"]


def test_prompt_tells_model_where_the_scripts_are(db_session):
    from app.services.harness_agent import _prompt

    plugin_id = _publish_skill(db_session)
    _bind(db_session, plugin_id)
    employee = db_session.get(models.DigitalEmployee, AGENT)
    profile = db_session.get(models.AgentProfile, AGENT)
    assert employee is not None and profile is not None

    prompt = _prompt(
        employee=employee, profile=profile, requester="E10281",
        message="把 document.doc 转成 docx", history=[], knowledge=[],
        depth=0, plugins=[], skills=effective_skills(db_session, AGENT),
    )
    assert "/workspace/skills/docx" in prompt
    assert "SKILL.md" in prompt


def test_prompt_omits_package_hint_without_scripts(db_session):
    from app.services.harness_agent import _prompt

    employee = db_session.get(models.DigitalEmployee, AGENT)
    profile = db_session.get(models.AgentProfile, AGENT)
    prompt = _prompt(
        employee=employee, profile=profile, requester="E10281",
        message="你好", history=[], knowledge=[], depth=0, plugins=[],
        skills=[{"id": "SKILL-X", "name": "话术", "description": "", "content": "打招呼"}],
    )
    assert "/workspace/skills" not in prompt
