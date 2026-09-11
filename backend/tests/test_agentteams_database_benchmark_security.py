import json
from pathlib import Path
import re

import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[2]
SECRET_SHAPE = re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b")


def test_benchmark_artifacts_do_not_contain_secret_shaped_values():
    roots = [
        PROJECT_ROOT / ".agents" / "skills",
        PROJECT_ROOT / "backend" / "app",
        PROJECT_ROOT / "docs" / "agentteams",
        PROJECT_ROOT / "mock-data" / "agentteams-benchmark",
    ]
    files = [
        path
        for root in roots
        for path in root.rglob("*")
        if path.is_file()
        and (
            "database" in path.as_posix().lower()
            or path.name in {"adapters.py", "seed.py", "schemas.py", "main.py"}
        )
    ]

    leaks = [str(path.relative_to(PROJECT_ROOT)) for path in files if SECRET_SHAPE.search(
        path.read_text(encoding="utf-8", errors="ignore")
    )]

    assert leaks == []


def test_provider_keys_are_environment_only_and_never_enter_public_configuration():
    from app.services.agentteams_database_benchmark_backend import build_deepseek_database_backend

    secret_values = {
        "DEEPSEEK_API_KEY_MANAGER": "manager-private-value",
        "DEEPSEEK_API_KEY_DB_WRITE": "write-private-value",
        "DEEPSEEK_API_KEY_DB_LOCK": "lock-private-value",
        "DEEPSEEK_API_KEY_DB_VACUUM": "vacuum-private-value",
        "DEEPSEEK_API_KEY_DB_INDEX": "index-private-value",
        "DEEPSEEK_API_KEY_DB_QUERY": "query-private-value",
    }

    class Provider:
        model = "fake-model"

        def structured_output_with_trace(self, _messages, _schema):
            raise AssertionError("not called")

    received = []

    def factory(*, api_key, model, thinking, reasoning_effort, request_timeout):
        received.append((api_key, model, thinking, reasoning_effort, request_timeout))
        return Provider()

    backend = build_deepseek_database_backend(
        project_root=PROJECT_ROOT,
        environ=secret_values,
        provider_factory=factory,
    )
    public = json.dumps({
        "model_name": backend.model_name,
        "model_settings": backend.model_settings,
    }, sort_keys=True)

    assert {item[0] for item in received} == set(secret_values.values())
    assert all(item[2] is False and item[3] == "low" and item[4] == 90.0 for item in received)
    assert all(secret not in public for secret in secret_values.values())


def test_successful_database_observation_is_allow_audited_and_unknown_signal_fails_closed(db_session):
    from app import models
    from app.services.gateway import invoke_plugin

    result = invoke_plugin(
        db_session,
        employee_id="DB-LOCK-01",
        plugin_id="agentteams-database-inspector",
        action="read",
        params={
            "scenario_id": "lock_contention",
            "signal": "locks",
            "filters": {},
            "limit": 20,
        },
        trace_id="SECURITY-AUDIT-1",
    )
    audit = db_session.get(models.AuditEvent, result["audit_ids"][0])
    assert audit is not None
    assert audit.decision == "allow"
    assert audit.plugin_id == "agentteams-database-inspector"

    with pytest.raises(Exception):
        invoke_plugin(
            db_session,
            employee_id="DB-LOCK-01",
            plugin_id="agentteams-database-inspector",
            action="read",
            params={
                "scenario_id": "lock_contention",
                "signal": "credentials",
                "filters": {},
                "limit": 20,
            },
            trace_id="SECURITY-AUDIT-2",
        )
