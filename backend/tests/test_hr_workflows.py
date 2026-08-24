import pytest
from fastapi import HTTPException

from app.services import gateway


def _run(db, employee_id, plugin_id, params, trace_id):
    return gateway.invoke_plugin(db, employee_id=employee_id, plugin_id=plugin_id, action="execute", params=params, trace_id=trace_id)


def test_hr_onboarding_normal_and_missing_checklist(db_session):
    normal = _run(db_session, "DT-E10281", "hr-onboarding-workflow", {"employee_no": "EMP-DEMO-001", "collaborate": False}, "T-HR-ONBOARD-OK")["data"]
    assert normal["status"] == "success"
    assert normal["data"]["missing_items"]
    missing = _run(db_session, "DT-E10281", "hr-onboarding-workflow", {"employee_no": "EMP-DEMO-002"}, "T-HR-ONBOARD-MISSING")["data"]
    assert any(row["status"] == "missing" for row in missing["data"]["missing_items"])
    assert missing["data"]["collaboration_result"]["status"] == "success"


def test_hr_onboarding_policy_deny_and_no_collaboration(db_session):
    with pytest.raises(HTTPException) as exc:
        _run(db_session, "DT-E20999", "hr-onboarding-workflow", {"employee_no": "EMP-DEMO-001"}, "T-HR-ONBOARD-DENY")
    assert exc.value.status_code == 403
    result = _run(db_session, "DT-E10281", "hr-onboarding-workflow", {"employee_no": "EMP-DEMO-001", "collaborate": False}, "T-HR-ONBOARD-NOCOLLAB")["data"]
    assert result["data"]["collaboration_result"] is None


def test_hr_transfer_normal_document_missing_and_policy_deny(db_session):
    normal = _run(db_session, "DT-E10281", "hr-transfer-review-workflow", {"document_name": "hr-transfer-request-demo.md", "employee_no": "EMP-DEMO-001"}, "T-HR-TRANSFER-OK")["data"]
    assert normal["status"] == "success"
    assert normal["data"]["transfer_document"]["status"] == "success"
    missing = _run(db_session, "DT-E10281", "hr-transfer-review-workflow", {"document_name": "missing.md", "employee_no": "EMP-DEMO-001"}, "T-HR-TRANSFER-MISSING")["data"]
    assert missing["reason"] == "document_not_found"
    with pytest.raises(HTTPException) as exc:
        _run(db_session, "DT-E20999", "hr-transfer-review-workflow", {"document_name": "hr-transfer-request-demo.md", "employee_no": "EMP-DEMO-001"}, "T-HR-TRANSFER-DENY")
    assert exc.value.status_code == 403
