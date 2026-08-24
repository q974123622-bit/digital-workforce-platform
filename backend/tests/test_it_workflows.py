from app.services import gateway


def _run(db, employee_id, params, trace_id):
    return gateway.invoke_plugin(db, employee_id=employee_id, plugin_id="it-incident-triage-workflow", action="execute", params=params, trace_id=trace_id)["data"]


def test_it_triage_health_semantics(db_session):
    healthy = _run(db_session, "DT-E10281", {"service_name": "Email", "symptom": "无法登录", "escalate": False}, "T-IT-HEALTHY")
    assert healthy["data"]["service"]["health"] == "healthy"
    degraded = _run(db_session, "DT-E10281", {"service_name": "VPN", "symptom": "延迟", "escalate": False}, "T-IT-DEGRADED")
    assert degraded["data"]["triage"]["shared_incident_possible"] is True
    outage = _run(db_session, "DT-E10281", {"service_name": "HR-System-Demo", "symptom": "无法访问", "escalate": False}, "T-IT-OUTAGE")
    assert outage["data"]["service"]["health"] == "outage"
    maintenance = _run(db_session, "DT-E10281", {"service_name": "Trading-App-Demo", "symptom": "不可用", "escalate": False}, "T-IT-MAINT")
    assert maintenance["data"]["triage"]["maintenance_related"] is True


def test_it_triage_no_escalation_and_collaboration_deny(db_session):
    result = _run(db_session, "DT-E10281", {"service_name": "VPN", "symptom": "延迟", "escalate": False}, "T-IT-NOESC")
    assert result["data"]["collaboration_result"] is None
    intern = _run(db_session, "DT-E20999", {"service_name": "VPN", "symptom": "延迟", "escalate": True}, "T-IT-INTERN")
    assert intern["status"] == "partial"
    assert {step["step_id"]: step for step in intern["steps"]}["it_collaboration"]["decision"] == "deny"
