"""Sprint 3 Enterprise Resource & Security Layer 测试：
Knowledge Adapter / Knowledge Resource Registry / 安全资源边界 / Sandbox Policy / Audit。
"""


def _search(client, employee_id, kb_id, query, trace_id):
    return client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": employee_id,
            "knowledge_base_id": kb_id,
            "query": query,
            "trace_id": trace_id,
        },
    )


class _FakeInternalAdapter:
    """替身内部知识引擎 Adapter：返回 internal hits，不依赖真实引擎/网络。"""

    def search(self, *, employee_id, knowledge_base_id, query, trace_id):
        return {
            "source": "internal",
            "knowledge_base_id": knowledge_base_id,
            "query": query,
            "hits": [{"title": "内部知识片段", "snippet": "来自内部知识引擎的检索内容"}],
        }


def _patch_internal(monkeypatch):
    from app.services import gateway

    monkeypatch.setattr(gateway, "select_adapter", lambda *args, **kwargs: _FakeInternalAdapter())


# ---- Knowledge Resource Registry ----


def test_registry_resources(client):
    resp = client.get("/api/v1/knowledge-bases")
    assert resp.status_code == 200
    kbs = {kb["id"]: kb for kb in resp.json()}
    assert set(kbs) == {"KB-IT-SERVICE", "KB-SECURITIES", "KB-INVESTMENT-BANKING"}
    assert kbs["KB-IT-SERVICE"]["data_level"] == "L2"
    assert kbs["KB-IT-SERVICE"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-SECURITIES"]["data_level"] == "L2"
    assert kbs["KB-SECURITIES"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-INVESTMENT-BANKING"]["data_level"] == "L2"
    assert kbs["KB-INVESTMENT-BANKING"]["allowed_employment_type"] == ["formal"]


def test_registry_kb_not_found(client):
    assert client.get("/api/v1/knowledge-bases/KB-NOPE").status_code == 404


# ---- 安全资源边界：正式 / 实习 / 虚拟员工 ----


def test_general_employee_read_un_granted_kb_deny(client):
    resp = _search(client, "AI-GENERAL", "KB-SECURITIES", "股票期权", "T-S3-FORMAL-001")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "AGENT-KB-GRANT"


def test_intern_twin_read_internal_kb_deny(client):
    resp = _search(client, "DT-E20999", "KB-SECURITIES", "融资融券流程", "T-S3-INTERN-001")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "POLICY-002"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["knowledge_base_id"] == "KB-SECURITIES"
    assert audit["decision"] == "deny"
    assert audit["employee_id"] == "DT-E20999"


def test_knowledge_grants_do_not_cross_assign(client):
    cases = [
        ("AI-GENERAL", "KB-SECURITIES", "股票期权", "T-P17-CROSS-001"),
        ("AI-INVESTMENT", "KB-IT-SERVICE", "企业邮箱", "T-P17-CROSS-002"),
    ]
    for employee_id, kb_id, query, trace_id in cases:
        resp = _search(client, employee_id, kb_id, query, trace_id)
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "POLICY_DENIED"
        assert body["error"]["detail"]["policy_id"] == "AGENT-KB-GRANT"


def test_search_missing_employee(client):
    resp = _search(client, "VE-9999", "KB-IT-SERVICE", "x", "T-S3-NOEMP-001")
    assert resp.status_code == 404


def test_search_missing_kb(client):
    resp = _search(client, "DT-E10281", "KB-NOPE", "x", "T-S3-NOKB-001")
    assert resp.status_code == 404


# ---- 内部知识引擎检索（真实 adapter，测试用替身） ----


def test_it_service_kb_is_internal(client, monkeypatch):
    _patch_internal(monkeypatch)
    formal = _search(client, "AI-GENERAL", "KB-IT-SERVICE", "VPN 怎么连", "T-P17-IT-001")
    assert formal.status_code == 200
    assert formal.json()["policy_id"] == "POLICY-006"

    intern = _search(client, "DT-E20999", "KB-IT-SERVICE", "VPN 怎么连", "T-P17-IT-002")
    assert intern.status_code == 403
    assert intern.json()["error"]["detail"]["policy_id"] == "POLICY-002"


def test_new_securities_kb_l2_formal_allow(client, monkeypatch):
    _patch_internal(monkeypatch)
    resp = _search(client, "AI-INVESTMENT", "KB-SECURITIES", "融资融券流程", "T-P17-SEC-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-006"
    assert len(body["data"]["hits"]) >= 1


def test_new_securities_kb_l2_intern_deny(client):
    resp = _search(client, "DT-E20999", "KB-SECURITIES", "融资融券流程", "T-P17-SEC-002")
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "POLICY-002"


# ---- Sandbox Policy：remote_only / internet_deny / local_deny ----


def test_sandbox_remote_allow(client, monkeypatch):
    # 固定 local 降级路径：与真实 Docker daemon 状态无关（测试不依赖本机 daemon）
    from app.services import sandbox_manager

    monkeypatch.setattr(sandbox_manager, "docker_available", lambda timeout=3.0: False)
    resp = client.post(
        "/internal/sandbox/run",
        json={
            "employee_id": "DT-E10281",
            "task_id": "T1",
            "command": "run_report",
            "mount_dir": "/workspace/DT-E10281",
            "network": "none",
            "execution_location": "remote",
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["mode"] == "local"
    assert body["status"] == "ok"
    assert any("local" in log for log in body["logs"])


def test_sandbox_local_deny_remote_only(client):
    # remote_only 员工请求本地执行 → POLICY-004 DENY
    resp = client.post(
        "/internal/sandbox/run",
        json={
            "employee_id": "DT-E10281",
            "task_id": "T2",
            "command": "local_script",
            "mount_dir": "",
            "network": "none",
            "execution_location": "local",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "POLICY-004"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["decision"] == "deny"
    assert audit["plugin_id"] == "sandbox:local"


def test_sandbox_internet_deny(client):
    # internet=deny 员工请求非 none 网络 → POLICY-003 DENY
    resp = client.post(
        "/internal/sandbox/run",
        json={
            "employee_id": "DT-E10281",
            "task_id": "T3",
            "command": "curl",
            "mount_dir": "",
            "network": "public",
            "execution_location": "remote",
        },
    )
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["detail"]["policy_id"] == "POLICY-003"


def test_sandbox_missing_employee(client):
    resp = client.post(
        "/internal/sandbox/run",
        json={
            "employee_id": "VE-9999",
            "task_id": "T4",
            "command": "",
            "mount_dir": "",
            "network": "none",
        },
    )
    assert resp.status_code == 404


# ---- Secret / Config ----


def test_credential_refs_are_references_only():
    from app.services import config

    # 引用名必须是环境变量名，不是凭据值本身
    assert config.INTERNAL_KB_ENDPOINT.startswith("DWP_")
    assert config.INTERNAL_KB_CREDENTIAL_REF.startswith("DWP_")
