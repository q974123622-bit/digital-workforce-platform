from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


@pytest.fixture(scope="module")
def smoke_module():
    script = Path(__file__).resolve().parents[2] / "scripts" / "agentteams_collaboration_smoke.py"
    spec = importlib.util.spec_from_file_location("agentteams_collaboration_smoke_test", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _detail():
    return {
        "nodes": [
            {"id": "AT-1", "parent_task_id": None, "assignee_agent_id": "DT-E10281", "depth": 0},
            {"id": "AT-2", "parent_task_id": "AT-1", "assignee_agent_id": "VE-0002", "depth": 1},
            {"id": "AT-3", "parent_task_id": "AT-1", "assignee_agent_id": "VE-0003", "depth": 1},
        ]
    }


def _message(sender, message_type, event_id, *, direction, delivery_status, body="协作消息", payload=None):
    return {
        "sender_agent_id": sender,
        "message_type": message_type,
        "matrix_event_id": event_id,
        "matrix_room_id": "!room:matrix.test",
        "direction": direction,
        "delivery_status": delivery_status,
        "body": body,
        "payload": payload or {},
    }


def _received_workers():
    return [
        _message("VE-0002", "agent.message", "$worker-1", direction="inbound", delivery_status="received"),
        _message("VE-0003", "agent.message", "$worker-2", direction="inbound", delivery_status="received"),
    ]


def _completed_result():
    return {"status": "completed", "summary": "入职准备已完成"}


def test_build_evidence_rejects_outbound_logical_worker_records(smoke_module):
    """A platform send with a logical Worker sender is not a Worker receipt."""
    outbound_messages = [
        _message("VE-0002", "agent.message", "$outbound-1", direction="outbound", delivery_status="sent"),
        _message("VE-0003", "agent.message", "$outbound-2", direction="outbound", delivery_status="sent"),
    ]

    evidence = smoke_module.build_evidence(
        "AT-1", _detail(), [{"event_type": "task.progress"}], outbound_messages, _completed_result()
    )

    assert evidence["ok"] is False


def test_build_evidence_rejects_detached_database_summary(smoke_module):
    """A completed result needs the Manager's inbound Matrix task.summary evidence."""
    evidence = smoke_module.build_evidence(
        "AT-1", _detail(), [{"event_type": "task.progress"}], _received_workers(), _completed_result()
    )

    assert evidence["ok"] is False


def test_build_evidence_accepts_received_workers_and_manager_summary(smoke_module):
    messages = _received_workers() + [
        _message(
            "DT-E10281",
            "task.summary",
            "$manager-summary",
            direction="inbound",
            delivery_status="received",
            body="入职准备已完成",
            payload={"summary": "入职准备已完成"},
        )
    ]

    evidence = smoke_module.build_evidence(
        "AT-1", _detail(), [{"event_type": "task.progress"}], messages, _completed_result()
    )

    assert evidence["ok"] is True
    assert evidence["participant_ids"] == ["DT-E10281", "VE-0002", "VE-0003"]
    assert evidence["matrix_event_ids"]["DT-E10281"] == ["$manager-summary"]


def test_build_evidence_accepts_nested_protocol_payload_summary(smoke_module):
    messages = _received_workers() + [
        _message(
            "DT-E10281",
            "task.summary",
            "$manager-summary",
            direction="inbound",
            delivery_status="received",
            body="",
            payload={"payload": {"summary": "入职准备已完成"}},
        )
    ]

    evidence = smoke_module.build_evidence(
        "AT-1", _detail(), [{"event_type": "task.progress"}], messages, _completed_result()
    )

    assert evidence["ok"] is True


def test_build_evidence_rejects_manager_self_assignment_as_a_second_worker(smoke_module):
    detail = {
        "nodes": [
            {"id": "AT-1", "parent_task_id": None, "assignee_agent_id": "DT-E10281", "depth": 0},
            {"id": "AT-2", "parent_task_id": "AT-1", "assignee_agent_id": "DT-E10281", "depth": 1},
            {"id": "AT-3", "parent_task_id": "AT-1", "assignee_agent_id": "VE-0002", "depth": 1},
        ]
    }
    messages = [
        _message("DT-E10281", "agent.message", "$manager-child", direction="inbound", delivery_status="received"),
        _message("VE-0002", "agent.message", "$worker-1", direction="inbound", delivery_status="received"),
        _message(
            "DT-E10281",
            "task.summary",
            "$manager-summary",
            direction="inbound",
            delivery_status="received",
            payload={"summary": "入职准备已完成"},
        ),
    ]

    evidence = smoke_module.build_evidence(
        "AT-1", detail, [{"event_type": "task.progress"}], messages, _completed_result()
    )

    assert evidence["ok"] is False


class _FakeResponse:
    def __init__(self, value):
        self.value = value

    def raise_for_status(self):
        return None

    def json(self):
        if isinstance(self.value, Exception):
            raise self.value
        return self.value


class _FakeClient:
    def __init__(self, get_values, post_values=()):
        self.get_values = iter(get_values)
        self.post_values = iter(post_values)

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def get(self, _url):
        return _FakeResponse(next(self.get_values))

    def post(self, _url, json):
        return _FakeResponse(next(self.post_values))


def test_main_returns_safe_preflight_failure_for_non_json_health(smoke_module, monkeypatch, capsys):
    monkeypatch.setattr(smoke_module.sys, "argv", ["agentteams_collaboration_smoke.py"])
    monkeypatch.setattr(
        smoke_module.httpx,
        "Client",
        lambda **_kwargs: _FakeClient([ValueError("do not leak this response")]),
    )

    assert smoke_module.main() == 2
    output = capsys.readouterr().out
    evidence = json.loads(output)
    assert set(evidence) == {
        "task_id", "participant_ids", "matrix_event_ids", "summary", "missing_criteria"
    }
    assert "do not leak" not in output


def test_main_returns_safe_run_failure_for_non_json_result(smoke_module, monkeypatch, capsys):
    messages = _received_workers() + [
        _message(
            "DT-E10281", "task.summary", "$manager-summary", direction="inbound",
            delivery_status="received", payload={"summary": "完成"},
        )
    ]
    monkeypatch.setattr(smoke_module.sys, "argv", ["agentteams_collaboration_smoke.py"])
    monkeypatch.setattr(
        smoke_module.httpx,
        "Client",
        lambda **_kwargs: _FakeClient(
            [
                {"ready_for_demo": True},
                {"status": "completed"},
                _detail(),
                [{"event_type": "task.progress"}],
                messages,
                ValueError("do not leak this result"),
            ],
            [{"id": "AT-1"}],
        ),
    )

    assert smoke_module.main() == 1
    output = capsys.readouterr().out
    evidence = json.loads(output)
    assert set(evidence) == {
        "task_id", "participant_ids", "matrix_event_ids", "summary", "missing_criteria"
    }
    assert "do not leak" not in output
