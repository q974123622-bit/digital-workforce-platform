"""Sprint 9 Harness 执行引擎测试：Policy 前置 + Docker Harness + 审计。"""

from app import models
from app.services.runtime_adapter import RuntimeResult


def test_harness_execute_allow(client, db_session, monkeypatch):
    class FakeHarness:
        def run(self, *, employee_id, task_prompt, trace_id, context=None):
            return RuntimeResult(mode="harness", ok=True, result="欢迎入职！材料：身份证、学历证明、照片。")

    monkeypatch.setattr("app.routers.internal.DockerHarnessRuntimeAdapter", FakeHarness)
    resp = client.post(
        "/internal/harness/execute",
        json={"employee_no": "VE-0001", "task_prompt": "欢迎任务", "trace_id": "H-1"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["mode"] == "harness" and body["ok"] is True
    assert "身份证" in body["result"]
    # 审计落库
    events = db_session.query(models.AuditEvent).filter(models.AuditEvent.plugin_id == "harness:execute").all()
    assert any(e.trace_id == "H-1" and e.decision == "allow" for e in events)


def test_harness_execute_policy_deny_local(client):
    # 新建一个 local 运行的数字员工：POLICY-HARNESS-001 要求 remote 才 allow
    resp = client.post(
        "/api/v1/employees",
        json={
            "name": "本地助手",
            "type": "virtual",
            "owner_human_no": "E10281",
            "role_prompt": "你是本地助手",
            "runtime_type": "demo",
            "location": "local",
        },
    )
    assert resp.status_code == 201
    no = resp.json()["employee_no"]
    resp = client.post(
        "/internal/harness/execute",
        json={"employee_no": no, "task_prompt": "任务", "trace_id": "H-2"},
    )
    assert resp.status_code == 200
    assert resp.json()["decision"] == "deny"


def test_harness_execute_unknown_employee_404(client):
    resp = client.post(
        "/internal/harness/execute",
        json={"employee_no": "VE-9999", "task_prompt": "任务", "trace_id": "H-3"},
    )
    assert resp.status_code == 404
