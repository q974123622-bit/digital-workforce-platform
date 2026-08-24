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


# ---- Knowledge Resource Registry ----


def test_registry_eight_resources(client):
    resp = client.get("/api/v1/knowledge-bases")
    assert resp.status_code == 200
    kbs = {kb["id"]: kb for kb in resp.json()}
    assert set(kbs) == {
        "KB-PUBLIC",
        "KB-ONBOARD",
        "KB-INTERNAL",
        "KB-FINTECH",
        "KB-IT-SERVICE",
        "KB-SECURITIES",
        "KB-REG-INTERNAL",
        "KB-REG-EXTERNAL",
        "KB-CUSTOMER-SENSITIVE",
    }
    assert kbs["KB-PUBLIC"]["data_level"] == "L1"
    assert kbs["KB-PUBLIC"]["allowed_employment_type"] == ["formal", "intern"]
    assert kbs["KB-ONBOARD"]["name"] == "入职 Demo 知识库"
    assert kbs["KB-INTERNAL"]["data_level"] == "L2"
    assert kbs["KB-INTERNAL"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-FINTECH"]["department_scope"] == ["金融科技部"]
    assert kbs["KB-INTERNAL"]["resource_type"] == "knowledge"
    assert kbs["KB-IT-SERVICE"]["data_level"] == "L2"
    assert kbs["KB-IT-SERVICE"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-IT-SERVICE"]["doc_path"] == "mock-data/kb/it-service"
    assert kbs["KB-SECURITIES"]["data_level"] == "L2"
    assert kbs["KB-SECURITIES"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-SECURITIES"]["doc_path"] == "mock-data/kb/securities"
    assert kbs["KB-REG-INTERNAL"]["data_level"] == "L2"
    assert kbs["KB-REG-INTERNAL"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-REG-EXTERNAL"]["data_level"] == "L1"
    assert kbs["KB-REG-EXTERNAL"]["allowed_employment_type"] == ["formal", "intern"]
    assert kbs["KB-CUSTOMER-SENSITIVE"]["data_level"] == "L3"
    assert kbs["KB-CUSTOMER-SENSITIVE"]["allowed_employment_type"] == ["formal"]
    assert kbs["KB-CUSTOMER-SENSITIVE"]["resource_type"] == "knowledge"
    assert kbs["KB-CUSTOMER-SENSITIVE"]["doc_path"] == "mock-data/kb/customer-sensitive"


def test_registry_kb_not_found(client):
    assert client.get("/api/v1/knowledge-bases/KB-NOPE").status_code == 404


# ---- 安全资源边界：正式 / 实习 / 虚拟员工 ----


def test_formal_twin_read_internal_kb_allow(client):
    resp = _search(client, "DT-E10281", "KB-INTERNAL", "入职流程", "T-S3-FORMAL-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"
    assert body["data"]["source"] == "demo"
    assert body["data"]["knowledge_base_id"] == "KB-INTERNAL"
    assert len(body["data"]["hits"]) >= 1

    audit = client.get(f"/api/v1/audit/{body['audit_ids'][0]}").json()
    assert audit["knowledge_base_id"] == "KB-INTERNAL"
    assert audit["employee_id"] == "DT-E10281"
    assert audit["decision"] == "allow"
    assert audit["trace_id"] == "T-S3-FORMAL-001"


def test_intern_twin_read_internal_kb_deny(client):
    resp = _search(client, "DT-E20999", "KB-INTERNAL", "入职流程", "T-S3-INTERN-001")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    assert body["error"]["detail"]["policy_id"] == "POLICY-002"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["knowledge_base_id"] == "KB-INTERNAL"
    assert audit["decision"] == "deny"
    assert audit["employee_id"] == "DT-E20999"


def test_virtual_employee_unapproved_kb_deny(client):
    # VE-0002（HR 助理）无 knowledge-l2 授权 → KB-INTERNAL DENY（未授权默认拒绝）
    resp = _search(client, "VE-0002", "KB-INTERNAL", "入职流程", "T-S3-VE-001")
    assert resp.status_code == 403
    body = resp.json()
    assert body["error"]["code"] == "POLICY_DENIED"
    audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
    assert audit["decision"] == "deny"
    assert audit["knowledge_base_id"] == "KB-INTERNAL"
    assert "未授权插件" in audit["reason"]


def test_virtual_employee_onboarding_kb_allow(client):
    # VE-0001 仅授权公共 + 入职 Demo 知识库 → KB-ONBOARD（L1）ALLOW
    resp = _search(client, "VE-0001", "KB-ONBOARD", "第一天做什么", "T-S3-VE-OK-001")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"


def test_virtual_employee_internal_kb_deny(client):
    # VE-0001 无 knowledge-l2 grant（单独授权移除）→ KB-INTERNAL DENY
    resp = _search(client, "VE-0001", "KB-INTERNAL", "内部制度", "T-S3-VE-DENY-001")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "POLICY_DENIED"


def test_public_kb_any_employee_allow(client):
    resp = _search(client, "DT-E20999", "KB-PUBLIC", "员工守则", "T-S3-PUB-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "P-DEFAULT-001"


def test_fintech_kb_unapproved_deny(client):
    # VE-0003 无 knowledge-l2 grant → KB-FINTECH DENY
    resp = _search(client, "VE-0003", "KB-FINTECH", "金融科技材料", "T-S3-FIN-001")
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "POLICY_DENIED"


def test_search_missing_employee(client):
    resp = _search(client, "VE-9999", "KB-PUBLIC", "x", "T-S3-NOEMP-001")
    assert resp.status_code == 404


def test_search_missing_kb(client):
    resp = _search(client, "DT-E10281", "KB-NOPE", "x", "T-S3-NOKB-001")
    assert resp.status_code == 404


# ---- P17：新增模拟知识库（多格式虚构目录） ----


def test_new_it_service_kb_l2_formal_allow_intern_deny(client):
    # P21：IT 服务库改 L2 内部；正式分身 allow（POLICY-001），实习生 deny（POLICY-002）
    resp = _search(client, "DT-E10281", "KB-IT-SERVICE", "VPN 怎么连", "T-P21-IT-FORMAL")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"
    assert body["data"]["source"] == "demo"
    assert len(body["data"]["hits"]) >= 1

    resp = _search(client, "DT-E20999", "KB-IT-SERVICE", "VPN 怎么连", "T-P21-IT-INTERN")
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "POLICY-002"


def test_all_resources_have_three_level_classification(client):
    """P21 分级完整性：9 知识库 + 12 插件全部带 L1/L2/L3 等级。"""
    kbs = client.get("/api/v1/knowledge-bases").json()
    plugins = client.get("/api/v1/plugins").json()
    assert len(kbs) == 9
    assert len(plugins) == 13
    assert all(kb["data_level"] in {"L1", "L2", "L3"} for kb in kbs)
    assert all(p["data_level"] in {"L1", "L2", "L3"} for p in plugins)
    levels = {kb["id"]: kb["data_level"] for kb in kbs}
    assert levels["KB-IT-SERVICE"] == "L2"


def test_new_securities_kb_l2_formal_allow(client):
    resp = _search(client, "DT-E10281", "KB-SECURITIES", "融资融券流程", "T-P17-SEC-001")
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["policy_id"] == "POLICY-001"
    assert len(body["data"]["hits"]) >= 1


def test_new_securities_kb_l2_intern_deny(client):
    resp = _search(client, "DT-E20999", "KB-SECURITIES", "融资融券流程", "T-P17-SEC-002")
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "POLICY-002"


def test_new_internal_reg_kb_l2_formal_allow(client):
    resp = _search(client, "DT-E10281", "KB-REG-INTERNAL", "反洗钱", "T-P17-REG-001")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"
    assert len(resp.json()["data"]["hits"]) >= 1


def test_new_external_reg_kb_l1_any_employee_allow(client):
    resp = _search(client, "DT-E20999", "KB-REG-EXTERNAL", "反垄断", "T-P17-EXT-001")
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"
    assert len(resp.json()["data"]["hits"]) >= 1


def test_multiformat_directory_search_returns_nonempty_hits(client):
    # 目录内 .xlsx 与 .docx 均可被解析并返回片段
    cases = [
        ("DT-E10281", "KB-IT-SERVICE", "企业邮箱"),
        ("DT-E10281", "KB-SECURITIES", "股票期权"),
        ("DT-E10281", "KB-REG-INTERNAL", "适当性"),
        ("DT-E10281", "KB-REG-EXTERNAL", "尽职调查"),
    ]
    for i, (emp, kb_id, query) in enumerate(cases):
        resp = _search(client, emp, kb_id, query, f"T-P17-MF-{i}")
        assert resp.status_code == 200
        assert len(resp.json()["data"]["hits"]) >= 1


# ---- P20：L3 内部敏感演示库（未授权默认拒绝） ----


def test_l3_customer_sensitive_kb_denied_for_all(client):
    for emp in ("DT-E10281", "DT-E20999"):
        resp = _search(client, emp, "KB-CUSTOMER-SENSITIVE", "客户 KYC 信息", f"T-P20-{emp}")
        assert resp.status_code == 403
        body = resp.json()
        assert body["error"]["code"] == "POLICY_DENIED"
        assert body["error"]["detail"]["policy_id"] == "P-DATA-003"
        audit = client.get(f"/api/v1/audit/{body['error']['detail']['audit_id']}").json()
        assert audit["decision"] == "deny"
        assert audit["knowledge_base_id"] == "KB-CUSTOMER-SENSITIVE"


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


# ---- Secret / Config 与 Stub ----


def test_internal_kb_stub_safe_without_config(client):
    # 未配置环境变量：Stub 返回 stub 状态，不包含任何真实内容
    from app.services.knowledge_adapter import InternalKnowledgeAdapterStub

    result = InternalKnowledgeAdapterStub().search(
        employee_id="DT-E10281",
        knowledge_base_id="KB-INTERNAL",
        query="x",
        trace_id="T-S3-STUB-001",
    )
    assert result["source"] == "stub"
    assert result["status"] == "stub"
    assert result["configured"] is False
    assert "真实" in result["message"] or "未接入" in result["message"]


def test_credential_refs_are_references_only():
    from app.services import config

    # 引用名必须是环境变量名，不是凭据值本身
    assert config.INTERNAL_KB_ENDPOINT.startswith("DWP_")
    assert config.INTERNAL_KB_CREDENTIAL_REF.startswith("DWP_")
