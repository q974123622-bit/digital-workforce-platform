"""Domain atomic tool tests: gateway, policy, fixtures, and safe audit output."""

from app.services import gateway


def _invoke(db, plugin_id, params, employee_id="DT-E10281", trace_id="T-DOMAIN-ATOMIC"):
    return gateway.invoke_plugin(
        db,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="read",
        params=params,
        trace_id=trace_id,
    )


def test_onboarding_status_found_and_empty(db_session):
    found = _invoke(db_session, "hr-onboarding-status", {"employee_no": "EMP-DEMO-001"})
    assert found["data"]["status"] == "success"
    assert any(row["status"] == "pending" for row in found["data"]["checklist"])
    empty = _invoke(db_session, "hr-onboarding-status", {"employee_no": "NO-SUCH-DEMO"})
    assert empty["data"] == {"source": "demo", "status": "success", "employee_no": "NO-SUCH-DEMO", "checklist": []}


def test_it_service_status_covers_health_semantics(db_session):
    assert _invoke(db_session, "it-service-status", {"service_name": "Email"})["data"]["services"][0]["health"] == "healthy"
    assert _invoke(db_session, "it-service-status", {"service_name": "VPN"})["data"]["services"][0]["health"] == "degraded"
    assert _invoke(db_session, "it-service-status", {"service_name": "Trading-App-Demo"})["data"]["services"][0]["health"] == "maintenance"
    assert _invoke(db_session, "it-service-status", {"service_name": "HR-System-Demo"})["data"]["services"][0]["health"] == "outage"


def test_audit_query_uses_platform_events_and_safe_fields(db_session):
    result = _invoke(db_session, "audit-event-query", {"trace_id": "T-DEMO-001"})
    events = result["data"]["events"]
    assert [event["plugin_id"] for event in events] == ["knowledge-l2", "rpa-report"]
    allowed = {"audit_id", "trace_id", "employee_id", "plugin_id", "action", "decision", "reason", "created_at"}
    assert all(set(event) == allowed for event in events)
    assert all("result_summary" not in event and "token" not in event for event in events)


def test_audit_query_ignores_forged_employee_filter(db_session):
    result = _invoke(db_session, "audit-event-query", {"trace_id": "T-DEMO-002", "employee_id": "DT-E20999"})
    assert result["data"]["events"] == []
