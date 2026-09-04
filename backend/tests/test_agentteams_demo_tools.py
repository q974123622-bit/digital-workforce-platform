"""AgentTeams 入职演示工具必须经既有 Gateway 与审计链路运行。"""

import json

import pytest
from fastapi import HTTPException

from app import models
from app.services.gateway import invoke_plugin


def _invoke(db_session, employee_id: str, plugin_id: str, params: dict):
    return invoke_plugin(
        db_session,
        employee_id=employee_id,
        plugin_id=plugin_id,
        action="execute",
        params=params,
        trace_id=f"TOOL-{employee_id}-{plugin_id}",
    )


@pytest.mark.parametrize(
    ("employee_id", "plugin_id", "expected_key", "expected_evidence_id"),
    [
        ("VE-0002", "agentteams-onboarding-employee", "materials", "HR-A-001"),
        ("VE-0003", "agentteams-it-readiness", "accounts", "IT-A-001"),
        ("VE-0005", "agentteams-onboarding-evidence", "evidence", "AUD-A-001"),
    ],
)
def test_agentteams_domain_tool_returns_bounded_demo_evidence_and_writes_audit(
    db_session, employee_id, plugin_id, expected_key, expected_evidence_id,
):
    result = _invoke(db_session, employee_id, plugin_id, {"employee_ref": "新员工A"})

    assert result["ok"] is True
    assert result["data"]["source"] == "demo"
    assert result["data"]["employee_ref"] == "新员工A"
    assert result["data"]["evidence_ids"]
    assert expected_evidence_id in result["data"]["evidence_ids"]
    assert expected_key in result["data"]
    assert {"hr_materials", "it_readiness", "audit_evidence"}.isdisjoint(result["data"])
    audit = db_session.get(models.AuditEvent, result["audit_ids"][0])
    assert audit is not None
    assert audit.plugin_id == plugin_id
    assert audit.action == "execute"
    assert audit.decision == "allow"


@pytest.mark.parametrize(
    ("employee_id", "plugin_id"),
    [
        ("VE-0003", "agentteams-onboarding-employee"),
        ("VE-0002", "agentteams-onboarding-evidence"),
    ],
)
def test_agentteams_worker_cannot_call_another_domain_tool_and_denial_is_audited(
    db_session, employee_id, plugin_id,
):
    before = db_session.query(models.AuditEvent).count()

    with pytest.raises(HTTPException) as error:
        _invoke(db_session, employee_id, plugin_id, {"employee_ref": "新员工A"})

    assert error.value.status_code == 403
    audit = db_session.query(models.AuditEvent).order_by(models.AuditEvent.id.desc()).first()
    assert db_session.query(models.AuditEvent).count() == before + 1
    assert audit.plugin_id == plugin_id
    assert audit.action == "execute"
    assert audit.decision == "deny"


@pytest.mark.parametrize(
    "params, expected_error",
    [
        ({"employee_ref": "未知员工"}, "invalid_employee_ref"),
        ({"employee_ref": "新员工A" * 100}, "invalid_employee_ref"),
        ({"employee_ref": "新员工A", "path": "C:/not-allowed.json"}, "invalid_params"),
    ],
)
def test_agentteams_tool_rejects_untrusted_input_without_leaking_fixture(
    db_session, params, expected_error,
):
    with pytest.raises(ValueError, match=expected_error) as error:
        _invoke(db_session, "VE-0002", "agentteams-onboarding-employee", params)

    rendered = str(error.value)
    assert "HR-A-001" not in rendered
    assert "IT-A-001" not in rendered
    assert "AUD-A-001" not in rendered
    assert "onboarding-a.json" not in rendered


def test_agentteams_tool_rejects_wrong_action_through_gateway_and_audits_denial(db_session):
    before = db_session.query(models.AuditEvent).count()

    with pytest.raises(HTTPException) as error:
        invoke_plugin(
            db_session,
            employee_id="VE-0002",
            plugin_id="agentteams-onboarding-employee",
            action="read",
            params={"employee_ref": "新员工A"},
            trace_id="TOOL-WRONG-ACTION",
        )

    assert error.value.status_code == 400
    audit = db_session.query(models.AuditEvent).order_by(models.AuditEvent.id.desc()).first()
    assert db_session.query(models.AuditEvent).count() == before + 1
    assert audit.decision == "deny"
    assert audit.action == "read"


def test_domain_tool_output_never_contains_complete_fixture(db_session):
    result = _invoke(
        db_session, "VE-0002", "agentteams-onboarding-employee", {"employee_ref": "新员工A"},
    )

    rendered = json.dumps(result["data"], ensure_ascii=False)
    assert "IT-A-001" not in rendered
    assert "AUD-A-001" not in rendered
    assert "mock://" not in rendered
