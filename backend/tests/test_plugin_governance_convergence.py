"""插件治理语义收敛的回归用例。

收敛后的三条硬约束：
1. 技能以 ``Plugin(plugin_type='skill')`` + 已发布 ``PluginVersion`` 为唯一载体，
   正文取 ``manifest['instruction']``，上传链路与工作台编辑链路同源；
2. 授权以 ``AgentPluginBinding`` 为唯一真相，管理端一次变更在员工端目录、
   提示词、核验页、网关判定四处同时生效；
3. 「可添加」与「已启用」严格互斥，且不存在的插件返回 404 而不是 403。
"""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from app import models
from app.seed import converge_plugin_governance
from app.services.chat import ChatOrchestrator
from app.services.llm import LLMProvider, LLMResponse
from app.services.plugin_governance import effective_plugins, effective_skills

SKILL_NAME = "周报模板"
SKILL_BODY = "# 周报模板\n先给结论，再列本周关键进展。"


class FakeLLM(LLMProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


def _login(client, username):
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "Demo@123456"})
    assert response.status_code == 200


def _zip(files: dict[str, str]) -> bytes:
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


def _submit_skill(client, *, version="1.0.0", plugin_id=None, scope="personal", data_level="L1"):
    data = {
        "name": SKILL_NAME, "plugin_type": "skill", "scope": scope,
        "deployment_mode": "instruction", "data_level": data_level, "version": version,
        "target_agent_id": "DT-E10281",
    }
    if plugin_id:
        data["plugin_id"] = plugin_id
    package = _zip({
        "plugin.yaml": "name: weekly-report\ndescription: 周报模板\ninstruction_summary: 先给结论\n",
        "SKILL.md": SKILL_BODY,
    })
    return client.post(
        "/api/v1/my/plugins/submissions", data=data,
        files={"file": ("skill.zip", package, "application/zip")},
    )


def _binding(db_session, plugin_id, target="DT-E10281"):
    return db_session.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin_id,
        models.AgentPluginBinding.target_agent_id == target,
    ))


def test_uploaded_skill_is_the_single_carrier(client, db_session):
    """上传的技能必须落 SKILL- 插件 + Skill 行，并且正文进得了提示词。"""
    _login(client, "E10281")
    created = _submit_skill(client)
    assert created.status_code == 201
    plugin_id = created.json()["plugin_id"]
    assert plugin_id.startswith("SKILL-")

    plugin = db_session.get(models.Plugin, plugin_id)
    assert plugin is not None and plugin.plugin_type == "skill" and plugin.type == "skill"
    assert plugin.status == "active" and plugin.current_version == "1.0.0"

    # 工作台编辑视图同源：Skill 行由上传链路同步创建
    skill_row = db_session.get(models.Skill, plugin_id[len("SKILL-"):])
    assert skill_row is not None and skill_row.owner_human_no == "E10281"

    skills = effective_skills(db_session, "DT-E10281")
    mine = next(item for item in skills if item["id"] == plugin_id)
    assert mine["name"] == SKILL_NAME
    assert "先给结论" in mine["content"]

    # 技能正文进入分身人设（chat 兜底链路），而不是只在目录里挂名
    fake = FakeLLM([LLMResponse(content="好的，我按技能回答。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session, employee_no="DT-E10281", message="帮我写周报",
        session_id=None, persist=False, history_override=[],
    )
    assert result.message == "好的，我按技能回答。"
    assert SKILL_NAME in fake.calls[0][0]["content"]


def test_admin_revocation_propagates_to_every_consumer(client, db_session):
    """管理端关掉「管理员授权」后，员工端/提示词/核验页同时失效。"""
    _login(client, "E10281")
    plugin_id = _submit_skill(client).json()["plugin_id"]
    binding = _binding(db_session, plugin_id)
    assert binding is not None and binding.admin_enabled and binding.employee_enabled
    binding_id = binding.id

    _login(client, "admin")
    updated = client.put(
        f"/api/v1/admin/agent-plugin-bindings/{binding_id}", json={"admin_enabled": False},
    )
    assert updated.status_code == 200

    _login(client, "E10281")
    # ① 员工端「已启用」消失
    body = client.get("/api/v1/my/plugins").json()
    assert plugin_id not in {item["plugin_id"] for item in body["effective"]}
    # ② 技能解析出口消失
    assert all(item["id"] != plugin_id for item in effective_skills(db_session, "DT-E10281"))
    # ③ 运行能力核验页标记未授权
    caps = client.get("/api/v1/agents/DT-E10281/effective-capabilities").json()["capabilities"]
    item = next(row for row in caps if row["id"] == f"skill:{plugin_id}")
    assert item["status"] == "unauthorized" and item["authorized"] is False


def test_available_and_effective_are_mutually_exclusive(client, db_session):
    _login(client, "E10281")
    plugin_id = _submit_skill(client).json()["plugin_id"]

    assert client.post(f"/api/v1/my/plugins/{plugin_id}/disable", json={}).status_code == 200
    body = client.get("/api/v1/my/plugins").json()
    effective_ids = {item["plugin_id"] for item in body["effective"]}
    available_ids = {item["id"] for item in body["available"]}
    assert plugin_id in available_ids and plugin_id not in effective_ids
    assert not (effective_ids & available_ids)
    assert plugin_id not in {
        item["plugin_id"] for item in effective_plugins(db_session, "DT-E10281", "E10281")
    }

    # 不存在的插件必须是 404（此前是「管理员尚未授权」403，误导排查方向）
    assert client.post("/api/v1/my/plugins/NOT-A-PLUGIN/enable", json={}).status_code == 404
    # 无绑定的插件不出现在「可添加」
    db_session.add(models.Plugin(
        id="PLG-UNBOUND", name="未绑定插件", type="mcp", plugin_type="mcp",
        status="active", current_version="1.0.0",
    ))
    db_session.commit()
    body = client.get("/api/v1/my/plugins").json()
    assert "PLG-UNBOUND" not in {item["id"] for item in body["available"]}


def test_version_is_validated_and_plugin_id_is_reused(client, db_session):
    _login(client, "E10281")
    created = _submit_skill(client).json()
    plugin_id = created["plugin_id"]

    assert _submit_skill(client, plugin_id=plugin_id, version="../../etc/pwn").status_code == 422
    assert _submit_skill(client, plugin_id=plugin_id, version="1.0.0").status_code == 409

    again = _submit_skill(client, plugin_id=plugin_id, version="1.1.0")
    assert again.status_code == 201
    assert again.json()["plugin_id"] == plugin_id
    # 复用同一插件：不会因为再上传一次就多出一条互不相干的插件
    same_name = db_session.query(models.Plugin).filter(models.Plugin.name == SKILL_NAME).all()
    assert len(same_name) == 1
    versions = db_session.scalars(select(models.PluginVersion.version).where(
        models.PluginVersion.plugin_id == plugin_id)).all()
    assert set(versions) == {"1.0.0", "1.1.0"}


def test_publish_and_rollback_work_on_staged_artifacts(client, db_session):
    """存量插件第一次真正可发布/回滚：空路径与同路径都不再抛 500。"""
    _login(client, "E10281")
    first = _submit_skill(client, scope="shared", data_level="L2").json()
    plugin_id = first["plugin_id"]
    second = _submit_skill(client, plugin_id=plugin_id, version="1.1.0", scope="shared", data_level="L2").json()

    _login(client, "admin")
    for row in (first, second):
        assert client.post(
            f"/api/v1/admin/plugin-submissions/{row['id']}/approve", json={"note": "ok"},
        ).status_code == 200
        published = client.post(
            f"/api/v1/admin/plugins/{plugin_id}/versions/{row['version']}/publish",
        )
        assert published.status_code == 200
    assert db_session.get(models.Plugin, plugin_id).current_version == "1.1.0"

    rolled = client.post(f"/api/v1/admin/plugins/{plugin_id}/versions/1.0.0/rollback")
    assert rolled.status_code == 200
    assert db_session.get(models.Plugin, plugin_id).current_version == "1.0.0"

    assert client.post(f"/api/v1/admin/plugins/{plugin_id}/versions/9.9.9/publish").status_code == 404
    assert client.post("/api/v1/admin/plugin-submissions/999999/approve", json={"note": ""}).status_code == 404


def test_binding_validates_pinned_version_and_personal_ownership(client, db_session):
    _login(client, "E10281")
    plugin_id = _submit_skill(client).json()["plugin_id"]

    _login(client, "admin")
    bad_version = client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "AI-GENERAL", "pinned_version": "9.9.9",
    })
    assert bad_version.status_code == 422
    other_twin = client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "AI-GENERAL",
    })
    assert other_twin.status_code == 422  # 个人插件只能绑给属主本人的分身

    ok = client.post("/api/v1/admin/agent-plugin-bindings", json={
        "plugin_id": plugin_id, "target_agent_id": "DT-E10281", "pinned_version": "1.0.0",
    })
    assert ok.status_code == 409  # 上传时已自动建过绑定


def test_withdraw_disables_bindings_and_is_idempotent(client, db_session):
    _login(client, "E10281")
    plugin_id = _submit_skill(client).json()["plugin_id"]

    _login(client, "admin")
    assert client.post(f"/api/v1/admin/plugins/{plugin_id}/withdraw").status_code == 200
    assert db_session.get(models.Plugin, plugin_id).status == "withdrawn"
    assert _binding(db_session, plugin_id).admin_enabled is False
    assert db_session.get(models.Skill, plugin_id[len("SKILL-"):]).status == "disabled"
    assert client.post(f"/api/v1/admin/plugins/{plugin_id}/withdraw").status_code == 200
    assert client.post("/api/v1/admin/plugins/NOT-A-PLUGIN/withdraw").status_code == 404


def test_resubmit_rejected_submission_reuses_version(client, db_session):
    _login(client, "E10281")
    created = _submit_skill(client, scope="shared", data_level="L2").json()

    _login(client, "admin")
    assert client.post(
        f"/api/v1/admin/plugin-submissions/{created['id']}/reject", json={"note": "缺少输出模板"},
    ).status_code == 200

    _login(client, "E10281")
    detail = client.get(f"/api/v1/my/plugins/{created['plugin_id']}").json()
    assert detail["version"]["review_note"] == "缺少输出模板"

    package = _zip({
        "plugin.yaml": "name: weekly-report\ndescription: 周报模板\ninstruction_summary: 先给结论\n",
        "SKILL.md": SKILL_BODY,
    })
    resent = client.post(
        f"/api/v1/my/plugins/submissions/{created['id']}/resubmit",
        files={"file": ("skill.zip", package, "application/zip")},
    )
    assert resent.status_code == 200
    assert resent.json()["review_status"] == "pending"
    assert resent.json()["review_note"] == ""
    # 复用同一版本行，不产生孤立版本
    versions = db_session.scalars(select(models.PluginVersion.id).where(
        models.PluginVersion.plugin_id == created["plugin_id"])).all()
    assert list(versions) == [created["id"]]


def test_converge_plugin_governance_is_idempotent(db_session):
    """老库迁移幂等，且盈米技能的 SOP 正文已回填（否则收敛后会从提示词里消失）。"""
    stats = converge_plugin_governance(db_session)
    assert all(value == 0 for value in stats.values()), stats

    version = db_session.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == "SKILL-QM-fund-screener",
        models.PluginVersion.publish_status == "published",
    ))
    assert version is not None
    assert str((version.manifest or {}).get("instruction") or "").strip()
    # 技能插件类型已统一，且不再出现「技能插件却带 mcp 类型」的历史脏数据
    plugin = db_session.get(models.Plugin, "SKILL-QM-fund-screener")
    assert plugin.plugin_type == "skill" and plugin.type == "skill"
