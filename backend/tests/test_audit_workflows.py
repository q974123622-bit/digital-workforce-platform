import pytest
from fastapi import HTTPException

from app.services import gateway


def _run(db, employee_id, params, trace_id):
    return gateway.invoke_plugin(db, employee_id=employee_id, plugin_id="audit-evidence-review-workflow", action="execute", params=params, trace_id=trace_id)["data"]


def test_audit_evidence_complete_trace_and_safe_output(db_session):
    result = _run(db_session, "DT-E10281", {"document_name": "audit-evidence-demo.md", "collaborate": False}, "T-AUDIT-OK")
    assert result["status"] == "success"
    assert [event["plugin_id"] for event in result["data"]["audit_events"]] == ["knowledge-l2", "rpa-report"]
    assert all("result_summary" not in event for event in result["data"]["audit_events"])


def test_audit_evidence_trace_missing_document_missing_and_no_collaboration(db_session):
    no_trace = _run(db_session, "DT-E10281", {"document_name": "audit-evidence-demo.md", "trace_id": "T-NOT-FOUND", "collaborate": False}, "T-AUDIT-NOTRACE")
    assert no_trace["data"]["audit_events"] == []
    assert no_trace["data"]["evidence_gaps"]
    missing_document = _run(db_session, "DT-E10281", {"document_name": "no-evidence.md"}, "T-AUDIT-MISSING")
    assert missing_document["reason"] == "document_not_found"
    assert missing_document["data"]["document"]["status"] == "not_found"


def test_audit_evidence_policy_deny(db_session):
    with pytest.raises(HTTPException) as exc:
        _run(db_session, "DT-E20999", {"document_name": "audit-evidence-demo.md"}, "T-AUDIT-DENY")
    assert exc.value.status_code == 403
