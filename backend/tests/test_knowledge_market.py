"""知识库市场测试：货架状态、申请、审批授权、独占冲突。

覆盖用户口径：所有知识库全可见（含被他人独占的，标注占用方），要用就申请、
管理员审批；审批通过必须写 ``AgentKnowledgeGrant``（真实授权），冲突时 409 且不改数据。

注意 seed 的独占分配：16 个目录库已全部落在 5 个岗位 AI 员工名下，两个数字分身
初始为空。因此「可申请」的场景需要管理员先在分配矩阵里释放一个库 —— 这正是
真实流程的样子（管理员决定把某个库从岗位员工改派给个人分身）。
"""

from __future__ import annotations

from sqlalchemy import select

from app import models
from app.services.knowledge_catalog import GRANT_SPECS

TWIN_E10281 = "DT-E10281"
TWIN_E20999 = "DT-E20999"


def _login(client, key: str) -> None:
    assert client.post("/api/v1/auth/key-login", json={"access_key": key}).status_code == 200


def _shelf(client) -> list[dict]:
    response = client.get("/api/v1/my/knowledge-bases")
    assert response.status_code == 200
    return response.json()["items"]


def _by_id(items: list[dict]) -> dict[str, dict]:
    return {item["id"]: item for item in items}


def _request(client, kb_id: str, reason: str = "业务需要"):
    return client.post(f"/api/v1/my/knowledge-bases/{kb_id}/request", json={"reason": reason})


def _approve(client, request_id: int, approve: bool = True):
    return client.post(f"/api/v1/admin/access-requests/{request_id}/approve", json={
        "approve": approve,
    })


def _release(client, kb_id: str) -> str | None:
    """管理员在分配矩阵里把某个库从当前占用者手中释放，返回原占用者。"""
    matrix = client.get("/api/v1/admin/knowledge-grants").json()
    owner = matrix["occupancy"].get(kb_id)
    if owner is None:
        return None
    row = next(item for item in matrix["employees"] if item["employee_id"] == owner)
    keep = [item for item in row["knowledge_base_ids"] if item != kb_id]
    response = client.put(
        f"/api/v1/admin/knowledge-grants/{owner}", json={"knowledge_base_ids": keep})
    assert response.status_code == 200
    return owner


# ---- 货架 ---------------------------------------------------------------


def test_shelf_shows_every_active_knowledge_base(client):
    _login(client, "test-key-e10281")

    items = _shelf(client)

    assert len(items) >= 10
    assert all(item["status"] == "active" for item in items)
    # 全可见：被 AI-INVESTMENT 独占的库也在货架上，并标注占用方
    securities = _by_id(items)["KB-SECURITIES"]
    assert securities["state"] == "occupied"
    assert securities["occupant"] == "AI-INVESTMENT"
    assert securities["occupant_name"]


def test_shelf_reports_source_availability(client):
    _login(client, "test-key-e10281")
    items = _shelf(client)
    assert all("source_available" in item and "active_source" in item for item in items)


def test_shelf_marks_granted_knowledge_base_as_enabled(client, db_session):
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    assert client.put(
        "/api/v1/admin/knowledge-grants/DT-E10281",
        json={"knowledge_base_ids": ["KB-WEALTH"]},
    ).status_code == 200

    _login(client, "test-key-e10281")
    item = _by_id(_shelf(client))["KB-WEALTH"]
    assert item["state"] == "enabled"
    assert item["request_id"] is None


# ---- 申请 → 审批 → 授权 --------------------------------------------------


def test_request_then_approve_grants_knowledge_base(client, db_session):
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    _login(client, "test-key-e10281")

    created = _request(client, "KB-WEALTH", "季度报告需要")
    assert created.status_code == 200
    request_id = created.json()["id"]
    assert created.json()["resource_type"] == "knowledge_base"
    assert created.json()["applicant_no"] == TWIN_E10281

    pending = _by_id(_shelf(client))["KB-WEALTH"]
    assert pending["state"] == "pending" and pending["request_id"] == request_id

    _login(client, "test-key-admin00")
    approved = _approve(client, request_id)
    assert approved.status_code == 200
    assert approved.json()["status"] == "granted"

    grant = db_session.scalar(select(models.AgentKnowledgeGrant).where(
        models.AgentKnowledgeGrant.employee_id == TWIN_E10281,
        models.AgentKnowledgeGrant.knowledge_base_id == "KB-WEALTH",
    ))
    assert grant is not None and grant.status == "active" and grant.action == "read"

    _login(client, "test-key-e10281")
    assert _by_id(_shelf(client))["KB-WEALTH"]["state"] == "enabled"


def test_approval_is_incremental_keeps_existing_grants(client, db_session):
    """审批加库不能把员工原有的其他知识库清空。"""
    _login(client, "test-key-admin00")
    # 先把两个库从岗位员工手里释放，再交给张三的分身
    _release(client, "KB-WEALTH")
    _release(client, "KB-IT-SERVICE")
    assert client.put(
        "/api/v1/admin/knowledge-grants/DT-E10281",
        json={"knowledge_base_ids": ["KB-IT-SERVICE"]},
    ).status_code == 200

    _login(client, "test-key-e10281")
    request_id = _request(client, "KB-WEALTH").json()["id"]
    _login(client, "test-key-admin00")
    assert _approve(client, request_id).status_code == 200

    rows = db_session.scalars(select(models.AgentKnowledgeGrant).where(
        models.AgentKnowledgeGrant.employee_id == TWIN_E10281,
    )).all()
    assert {row.knowledge_base_id for row in rows} == {"KB-IT-SERVICE", "KB-WEALTH"}


# ---- 拒绝分支 -----------------------------------------------------------


def test_request_rejects_occupied_knowledge_base(client):
    _login(client, "test-key-e10281")

    response = _request(client, "KB-SECURITIES")

    assert response.status_code == 409
    detail = response.json()["error"]["detail"]
    assert detail["reason"] == "knowledge_grant_conflict"
    assert detail["conflicts"] == [
        {"knowledge_base_id": "KB-SECURITIES", "employee_id": "AI-INVESTMENT"}
    ]


def test_request_requires_formal_employee(client):
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    _login(client, "test-key-e20999")  # 陈晓萌是实习生

    response = _request(client, "KB-WEALTH")

    assert response.status_code == 403
    assert response.json()["error"]["detail"]["policy_id"] == "ACCESS-FORMAL-ONLY"


def test_request_rejects_unknown_knowledge_base(client):
    _login(client, "test-key-e10281")
    assert _request(client, "KB-NOPE").status_code == 404


def test_duplicate_request_conflicts(client):
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    _login(client, "test-key-e10281")

    first = _request(client, "KB-WEALTH")
    assert first.status_code == 200
    second = _request(client, "KB-WEALTH")

    assert second.status_code == 409
    assert second.json()["error"]["detail"]["request_id"] == first.json()["id"]


def test_request_already_granted_is_rejected(client):
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    client.put("/api/v1/admin/knowledge-grants/DT-E10281",
               json={"knowledge_base_ids": ["KB-WEALTH"]})
    _login(client, "test-key-e10281")

    response = _request(client, "KB-WEALTH")

    assert response.status_code == 409
    assert response.json()["error"]["detail"]["reason"] == "already_granted"


def test_approve_conflict_keeps_data_untouched(client, db_session):
    """审批时的独占闸门：另一个分身已有待批申请时，第二次审批 409 且不改数据。"""
    _login(client, "test-key-admin00")
    _release(client, "KB-WEALTH")
    _login(client, "test-key-e10281")
    first_id = _request(client, "KB-WEALTH").json()["id"]

    # 实习生走不了申请接口，直接在库里造第二张待批申请单来验证审批侧闸门。
    second = models.AccessRequest(
        applicant_no=TWIN_E20999, resource_type="knowledge_base",
        resource_id="KB-WEALTH", reason="", status="pending", approval_chain=[],
    )
    db_session.add(second)
    db_session.commit()
    second_id = second.id

    _login(client, "test-key-admin00")
    assert _approve(client, first_id).status_code == 200
    blocked = _approve(client, second_id)

    assert blocked.status_code == 409
    detail = blocked.json()["error"]["detail"]
    assert detail["reason"] == "knowledge_grant_conflict"
    assert detail["conflicts"] == [
        {"knowledge_base_id": "KB-WEALTH", "employee_id": TWIN_E10281}
    ]
    db_session.expire_all()
    assert db_session.get(models.AccessRequest, second_id).status == "pending"
    assert db_session.scalar(select(models.AgentKnowledgeGrant).where(
        models.AgentKnowledgeGrant.employee_id == TWIN_E20999,
        models.AgentKnowledgeGrant.knowledge_base_id == "KB-WEALTH",
    )) is None


def test_grant_service_is_incremental(db_session):
    """服务层的增量语义：加一个库不丢原有授权。"""
    from app.services import knowledge_grant

    knowledge_grant.replace_grants(db_session, employee_id="AI-GENERAL", knowledge_base_ids=[
        kb for kb in GRANT_SPECS["AI-GENERAL"] if kb != "KB-IT-SERVICE"
    ])
    knowledge_grant.grant_knowledge_base(
        db_session, employee_id="AI-FUND", knowledge_base_id="KB-IT-SERVICE",
    )

    rows = db_session.scalars(select(models.AgentKnowledgeGrant).where(
        models.AgentKnowledgeGrant.employee_id == "AI-FUND",
    )).all()
    assert {row.knowledge_base_id for row in rows} == set(GRANT_SPECS["AI-FUND"]) | {"KB-IT-SERVICE"}
