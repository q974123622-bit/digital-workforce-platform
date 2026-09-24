"""插件市场（六态货架 + 申请审批闭环）回归用例。

覆盖四条硬约束：

1. **货架可见性**：共享插件可见、他人个人插件不可见、本人草稿为 ``own_draft``、
   已下架与没有已发布版本的共享插件不上架；
2. **申请闭环**：申请 → 管理端审批 → ``AgentPluginBinding`` 建立 →
   ``effective_plugins`` 立即命中（群聊可执行插件与员工端目录同源）；
3. **幂等与越权**：重复申请 409、非属主申请个人插件 403、``decision_mode='deny'``
   为 ``blocked`` 且不可申请、终态不可重复审批；
4. **敏感度分级**：L3 插件申请沿用 ACCESS-FORMAL-ONLY，L1/L2 允许实习生自助申请。
"""

from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

from sqlalchemy import select

from app import models
from app.services.plugin_governance import effective_plugins


def _login(client, username: str) -> None:
    response = client.post("/api/v1/auth/login", json={"username": username, "password": "Demo@123456"})
    assert response.status_code == 200


def _zip(files: dict[str, str]) -> bytes:
    out = BytesIO()
    with ZipFile(out, "w", ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return out.getvalue()


def _publish(
    db_session, plugin_id: str, *, name: str = "共享技能", scope: str = "shared",
    data_level: str = "L2", owner: str | None = None, status: str = "active", published: bool = True,
) -> str:
    """造一个插件（可选带已发布版本），用于货架与申请链路断言。"""
    db_session.add(models.Plugin(
        id=plugin_id, name=name, type="skill", plugin_type="skill", scope=scope,
        owner_human_no=owner, status=status,
        current_version="1.0.0" if published else None,
        data_level=data_level, description="共享工作方法",
    ))
    if published:
        db_session.add(models.PluginVersion(
            plugin_id=plugin_id, version="1.0.0", deployment_mode="instruction",
            manifest={"instruction": "SOP 正文"}, data_level=data_level,
            review_status="approved", publish_status="published", submitted_by=owner or "SYS-ADMIN",
        ))
    db_session.commit()
    return plugin_id


def _market(client, employee: str = "E10281") -> list[dict]:
    _login(client, employee)
    return client.get("/api/v1/my/plugins").json()["market"]


def _item(rows: list[dict], plugin_id: str) -> dict | None:
    return next((row for row in rows if row["plugin_id"] == plugin_id), None)


def test_market_shelf_visibility(client, db_session):
    """共享插件在架，他人个人插件与下架/无版本插件不在架，本人草稿标记为未发布。"""
    _publish(db_session, "PLG-SHARED", name="共享技能")
    _publish(db_session, "PLG-OTHER", name="他人技能", scope="personal", owner="E20999")
    _publish(db_session, "PLG-MY-DRAFT", name="我的草稿", scope="personal", owner="E10281",
             status="draft", published=False)
    _publish(db_session, "PLG-WITHDRAWN", name="已下架插件", status="withdrawn")
    _publish(db_session, "PLG-NOVERSION", name="无已发布版本", published=False)

    rows = _market(client)
    ids = {row["plugin_id"] for row in rows}
    assert "PLG-SHARED" in ids
    assert "PLG-OTHER" not in ids
    assert "PLG-WITHDRAWN" not in ids
    assert "PLG-NOVERSION" not in ids

    shared = _item(rows, "PLG-SHARED")
    assert shared is not None and shared["state"] == "requestable"
    assert shared["version"] == "1.0.0" and shared["data_level"] == "L2"
    draft = _item(rows, "PLG-MY-DRAFT")
    assert draft is not None and draft["state"] == "own_draft" and draft["version"] is None


def test_request_then_admin_approval_grants_and_activates(client, db_session):
    """审批通过即自动授权：员工端状态转「已启用」，执行链路同时生效。"""
    _publish(db_session, "PLG-SHARED", name="共享技能")

    _login(client, "E10281")
    created = client.post("/api/v1/my/plugins/PLG-SHARED/request", json={"reason": "做基金分析需要"})
    assert created.status_code == 201
    request_id = created.json()["id"]
    assert created.json()["applicant_no"] == "DT-E10281"
    assert created.json()["resource_type"] == "plugin"

    body = client.get("/api/v1/my/plugins").json()
    item = _item(body["market"], "PLG-SHARED")
    assert item is not None and item["state"] == "pending" and item["request_id"] == request_id
    # 同一插件已有待审申请时不再重复提交
    assert client.post("/api/v1/my/plugins/PLG-SHARED/request", json={}).status_code == 409

    _login(client, "admin")
    approved = client.post(f"/api/v1/admin/access-requests/{request_id}/approve", json={"approve": True})
    assert approved.status_code == 200
    assert approved.json()["status"] == "granted"

    binding = db_session.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == "PLG-SHARED",
        models.AgentPluginBinding.target_agent_id == "DT-E10281",
    ))
    assert binding is not None
    assert binding.admin_enabled and binding.employee_enabled and binding.decision_mode == "allow"
    assert binding.authorized_by == f"access-request:{request_id}"

    _login(client, "E10281")
    body = client.get("/api/v1/my/plugins").json()
    assert _item(body["market"], "PLG-SHARED")["state"] == "enabled"
    assert "PLG-SHARED" in {row["plugin_id"] for row in body["effective"]}
    # 群聊可执行插件读的就是 effective_plugins，授权一次即同时打通
    assert "PLG-SHARED" in {
        row["plugin_id"] for row in effective_plugins(db_session, "DT-E10281", "E10281")
    }


def test_rejected_request_stays_requestable_and_is_terminal(client, db_session):
    _publish(db_session, "PLG-SHARED", name="共享技能")
    _login(client, "E10281")
    request_id = client.post("/api/v1/my/plugins/PLG-SHARED/request", json={"reason": "试试"}).json()["id"]

    _login(client, "admin")
    rejected = client.post(
        f"/api/v1/admin/access-requests/{request_id}/approve",
        json={"approve": False, "note": "预算未批"},
    )
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["approval_chain"][0]["note"] == "预算未批"
    # 终态不可重复审批
    assert client.post(
        f"/api/v1/admin/access-requests/{request_id}/approve", json={"approve": True},
    ).status_code == 409
    assert client.post("/api/v1/admin/access-requests/999999/approve", json={}).status_code == 404

    assert _item(_market(client), "PLG-SHARED")["state"] == "requestable"
    assert db_session.scalar(select(models.AgentPluginBinding.id).where(
        models.AgentPluginBinding.plugin_id == "PLG-SHARED")) is None


def test_personal_plugin_is_owner_only_and_draft_is_not_requestable(client, db_session):
    _publish(db_session, "PLG-OTHER", name="他人技能", scope="personal", owner="E20999")
    _publish(db_session, "PLG-MY-DRAFT", name="我的草稿", scope="personal", owner="E10281",
             status="draft", published=False)

    _login(client, "E10281")
    assert client.post("/api/v1/my/plugins/PLG-OTHER/request", json={}).status_code == 403
    # 本人草稿没有已发布版本，先提交版本而不是申请
    assert client.post("/api/v1/my/plugins/PLG-MY-DRAFT/request", json={}).status_code == 409
    assert client.post("/api/v1/my/plugins/NOT-A-PLUGIN/request", json={}).status_code == 404


def test_denied_binding_blocks_market_and_request(client, db_session):
    """管理员明确禁止（decision_mode=deny）的插件不能被员工申请绕过。"""
    _publish(db_session, "PLG-SHARED", name="共享技能")
    db_session.add(models.AgentPluginBinding(
        plugin_id="PLG-SHARED", target_agent_id="DT-E10281",
        admin_enabled=True, employee_enabled=True, decision_mode="deny",
    ))
    db_session.commit()

    assert _item(_market(client), "PLG-SHARED")["state"] == "blocked"
    assert client.post("/api/v1/my/plugins/PLG-SHARED/request", json={}).status_code == 409


def test_l3_plugin_requires_formal_employee_but_l1_l2_do_not(client, db_session):
    _publish(db_session, "PLG-L2", name="普通共享技能", data_level="L2")
    _publish(db_session, "PLG-L3", name="敏感共享技能", data_level="L3")

    _login(client, "E20999")
    ids = {row["plugin_id"] for row in client.get("/api/v1/my/plugins").json()["market"]}
    assert {"PLG-L2", "PLG-L3"} <= ids
    # 实习生（E20999）可以自助申请非敏感插件
    assert client.post(
        "/api/v1/my/plugins/PLG-L2/request", json={"reason": "整理会议纪要"},
    ).status_code == 201
    denied = client.post("/api/v1/my/plugins/PLG-L3/request", json={"reason": "敏感数据"})
    assert denied.status_code == 403
    assert denied.json()["error"]["detail"]["policy_id"] == "ACCESS-FORMAL-ONLY"


def test_admin_cannot_use_employee_market_endpoints(client):
    """管理员账号没有个人插件目录，员工侧接口一律 403。"""
    _login(client, "admin")
    assert client.get("/api/v1/my/plugins").status_code == 403
    assert client.post("/api/v1/my/plugins/PLG-SHARED/request", json={}).status_code == 403


def test_legacy_access_request_accepts_non_l3_plugin(client, db_session):
    """老 ``/access-requests`` 通道放开插件 L3 限制，但知识库仍只允许 L3。"""
    _publish(db_session, "PLG-L2", name="普通共享技能", data_level="L2")
    _login(client, "E10281")
    created = client.post(
        "/api/v1/access-requests", params={"applicant_no": "DT-E10281"},
        json={"resource_type": "plugin", "resource_id": "PLG-L2", "reason": "需要"},
    )
    assert created.status_code == 201
    assert created.json()["resource_type"] == "plugin"

    _login(client, "admin")
    request_id = created.json()["id"]
    assert client.post(
        f"/api/v1/admin/access-requests/{request_id}/approve", json={"approve": True},
    ).status_code == 200
    assert db_session.scalar(select(models.AgentPluginBinding.id).where(
        models.AgentPluginBinding.plugin_id == "PLG-L2",
        models.AgentPluginBinding.target_agent_id == "DT-E10281",
    )) is not None


def test_uploaded_skill_zip_still_auto_publishes(client):
    """市场改造不得影响既有的「个人 L1 技能上传即发布」链路。"""
    _login(client, "E10281")
    package = _zip({
        "plugin.yaml": "name: weekly-report\ndescription: 周报模板\n",
        "SKILL.md": "# 周报模板\n先给结论。",
    })
    created = client.post(
        "/api/v1/my/plugins/submissions",
        data={
            "name": "周报模板", "plugin_type": "skill", "scope": "personal",
            "deployment_mode": "instruction", "data_level": "L1", "version": "1.0.0",
            "target_agent_id": "DT-E10281",
        },
        files={"file": ("skill.zip", package, "application/zip")},
    )
    assert created.status_code == 201
    plugin_id = created.json()["plugin_id"]
    item = _item(client.get("/api/v1/my/plugins").json()["market"], plugin_id)
    assert item is not None and item["state"] == "enabled" and item["scope"] == "personal"
