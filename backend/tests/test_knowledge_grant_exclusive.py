"""知识库独占分配测试。

覆盖：管理端鉴权、整体替换、跨员工冲突 409、未知知识库 422、
以及 seed 收敛后「任意两个 AI 员工集合两两不相交」。
"""

from app import models
from app.services import knowledge_grant
from app.services.knowledge_catalog import GRANT_SPECS


def _login_admin(client) -> None:
    assert client.post("/api/v1/auth/key-login", json={"access_key": "test-key-admin00"}).status_code == 200


def _login_employee(client) -> None:
    assert client.post("/api/v1/auth/key-login", json={"access_key": "test-key-e10281"}).status_code == 200


def _matrix(client) -> dict:
    response = client.get("/api/v1/admin/knowledge-grants")
    assert response.status_code == 200
    return response.json()


def _replace(client, employee_id: str, kb_ids: list[str]):
    return client.put(
        f"/api/v1/admin/knowledge-grants/{employee_id}",
        json={"knowledge_base_ids": kb_ids},
    )


# ---- 鉴权 ---------------------------------------------------------------


def test_grants_require_admin_role(client):
    _login_employee(client)

    assert client.get("/api/v1/admin/knowledge-grants").status_code == 403
    assert _replace(client, "AI-FUND", []).status_code == 403


def test_matrix_lists_employees_knowledge_bases_and_occupancy(client):
    _login_admin(client)

    matrix = _matrix(client)

    employees = {row["employee_id"]: row for row in matrix["employees"]}
    assert set(employees["AI-INVESTMENT"]["knowledge_base_ids"]) == set(GRANT_SPECS["AI-INVESTMENT"])
    assert {row["id"] for row in matrix["knowledge_bases"]} >= {"KB-IT-SERVICE", "KB-WEALTH"}
    # 占用关系用于前端把「已被他人占用」的库置灰
    assert matrix["occupancy"]["KB-SECURITIES"] == "AI-INVESTMENT"


# ---- 独占校验 -----------------------------------------------------------


def test_seed_grants_are_pairwise_disjoint(client):
    _login_admin(client)

    matrix = _matrix(client)

    seen: dict[str, str] = {}
    for row in matrix["employees"]:
        for kb_id in row["knowledge_base_ids"]:
            assert kb_id not in seen, f"{kb_id} 同时属于 {seen.get(kb_id)} 与 {row['employee_id']}"
            seen[kb_id] = row["employee_id"]


def test_replace_rejects_kb_owned_by_another_employee(client):
    _login_admin(client)

    # KB-SECURITIES 属于 AI-INVESTMENT，不能再给 AI-FUND
    response = _replace(client, "AI-FUND", ["KB-SECURITIES"])

    assert response.status_code == 409
    detail = response.json()["error"]["detail"]
    assert detail["reason"] == "knowledge_grant_conflict"
    assert {"knowledge_base_id": "KB-SECURITIES", "employee_id": "AI-INVESTMENT"} in detail["conflicts"]
    # 冲突时不得改动任何数据
    matrix = _matrix(client)
    fund = next(row for row in matrix["employees"] if row["employee_id"] == "AI-FUND")
    assert set(fund["knowledge_base_ids"]) == set(GRANT_SPECS["AI-FUND"])
    assert matrix["occupancy"]["KB-SECURITIES"] == "AI-INVESTMENT"


def test_replace_rejects_unknown_knowledge_base(client):
    _login_admin(client)

    response = _replace(client, "AI-FUND", ["KB-NOPE"])

    assert response.status_code == 422
    assert "KB-NOPE" in response.json()["error"]["message"]


def test_replace_rejects_unknown_employee(client):
    _login_admin(client)

    assert _replace(client, "AI-NOPE", []).status_code == 422


# ---- 整体替换与改派 ------------------------------------------------------


def test_replace_then_reassign_is_atomic(client):
    _login_admin(client)

    original = next(
        row["knowledge_base_ids"] for row in _matrix(client)["employees"] if row["employee_id"] == "AI-FUND"
    )

    # 1) 释放 AI-FUND 的库
    released = _replace(client, "AI-FUND", [])
    assert released.status_code == 200
    assert next(
        row["knowledge_base_ids"] for row in released.json()["employees"] if row["employee_id"] == "AI-FUND"
    ) == []
    assert "KB-FINANCIAL-PRODUCT" not in released.json()["occupancy"]

    # 2) 释放后可以改派给另一个 AI 员工
    moved = _replace(client, "AI-RESEARCH", [*GRANT_SPECS["AI-RESEARCH"], "KB-FINANCIAL-PRODUCT"])
    assert moved.status_code == 200
    assert moved.json()["occupancy"]["KB-FINANCIAL-PRODUCT"] == "AI-RESEARCH"

    # 3) 再改回来（此时 AI-RESEARCH 仍持有，需要先释放）
    assert _replace(client, "AI-RESEARCH", list(GRANT_SPECS["AI-RESEARCH"])).status_code == 200
    restored = _replace(client, "AI-FUND", original)
    assert restored.status_code == 200
    assert set(restored.json()["occupancy"].items()) >= {("KB-FINANCIAL-PRODUCT", "AI-FUND")}


def test_replace_deduplicates_and_keeps_read_action(client, db_session):
    _login_admin(client)

    response = _replace(client, "AI-FUND", ["KB-FINANCIAL-PRODUCT", "KB-FINANCIAL-PRODUCT"])

    assert response.status_code == 200
    rows = db_session.query(models.AgentKnowledgeGrant).filter_by(employee_id="AI-FUND").all()
    assert [row.knowledge_base_id for row in rows] == ["KB-FINANCIAL-PRODUCT"]
    assert all(row.action == "read" and row.status == "active" for row in rows)


def test_service_conflicts_helper_ignores_own_grants(db_session):
    conflicts = knowledge_grant.conflicts_for(
        db_session, employee_id="AI-INVESTMENT", knowledge_base_ids=["KB-SECURITIES"],
    )

    assert conflicts == []
