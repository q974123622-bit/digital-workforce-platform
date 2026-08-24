"""Sprint 10 工作流角色化 + 参数化测试。"""

from app.services.adapters import REGISTRY, WORKFLOW_META


def test_workflow_meta_has_owner():
    owners = {k: v.get("owner_employee") for k, v in WORKFLOW_META.items()}
    assert owners["adp-onboarding"] == "VE-0001"
    assert owners["leave-request"] == "VE-0002"
    assert owners["rpa-report"] == "RPA-0001"
    assert all(owners.values())


def test_adp_onboarding_no_hardcoded_name():
    handler = REGISTRY["mock://adp/onboarding"]
    out = handler(None, {})
    assert out["employee_name"] != "王小明"
    assert out["employee_name"] == "该员工"
    assert handler(None, {"employee_name": "赵仁杰"})["employee_name"] == "赵仁杰"


def test_workflows_endpoint_returns_owner(client):
    resp = client.get("/api/v1/workflows")
    assert resp.status_code == 200
    adp = next(w for w in resp.json() if w["plugin_id"] == "adp-onboarding")
    assert adp["owner_employee"]["employee_no"] == "VE-0001"
