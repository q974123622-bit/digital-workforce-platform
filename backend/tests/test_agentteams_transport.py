import pytest

from app.services import config
from app.services.agentteams_gateway import AgentTeamsGateway
from app.services.agentteams_transport import (
    AgentTeamsTransport,
    LocalDemoTransport,
    create_agentteams_transport,
)


def test_agentteams_mode_defaults_to_strict(monkeypatch):
    monkeypatch.delenv("DWP_AGENTTEAMS_MODE", raising=False)

    assert config.agentteams_mode() == "agentteams_strict"


def test_agentteams_mode_rejects_unknown_value_to_strict(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "unexpected")

    assert config.agentteams_mode() == "agentteams_strict"


def test_factory_selects_local_transport(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")

    assert isinstance(create_agentteams_transport(), LocalDemoTransport)


def test_factory_keeps_matrix_transport_strict(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")

    transport = create_agentteams_transport()
    try:
        assert isinstance(transport, AgentTeamsGateway)
    finally:
        transport.close()


def test_all_transports_accept_room_ids_keyword():
    transport: AgentTeamsTransport = create_agentteams_transport("local_demo")

    batch = transport.sync_messages(since=None, room_ids=set())

    assert batch.events == []
    assert batch.next_cursor.startswith("local-")
    assert batch.next_cursor.endswith("-0")


def test_strict_transport_sync_accepts_room_ids_and_gateway_is_closed(monkeypatch):
    class Response:
        status_code = 200

        @staticmethod
        def json():
            return {"next_batch": "s1", "rooms": {"join": {}}}

    transport = create_agentteams_transport("agentteams_strict")
    assert isinstance(transport, AgentTeamsGateway)
    monkeypatch.setattr(transport, "login", lambda: "controlled-token")
    monkeypatch.setattr(transport._client, "get", lambda *_args, **_kwargs: Response())

    try:
        batch = transport.sync_messages(since=None, room_ids=set())
        assert batch.events == []
        assert batch.next_cursor == "s1"
    finally:
        transport.close()

    assert transport._client.is_closed is True


def test_explicit_mode_override_takes_priority_over_environment(monkeypatch):
    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "agentteams_strict")
    local_transport = create_agentteams_transport("local_demo")
    assert isinstance(local_transport, LocalDemoTransport)

    monkeypatch.setenv("DWP_AGENTTEAMS_MODE", "local_demo")
    strict_transport = create_agentteams_transport("agentteams_strict")
    try:
        assert isinstance(strict_transport, AgentTeamsGateway)
    finally:
        strict_transport.close()


def test_strict_construction_failure_never_falls_back_to_local(monkeypatch):
    from app.services import agentteams_transport as transport_module

    def fail_matrix_construction():
        raise RuntimeError("strict_matrix_construction_failed")

    def fail_if_local_constructed():
        raise AssertionError("strict mode must not construct local transport")

    monkeypatch.setattr(transport_module, "AgentTeamsGateway", fail_matrix_construction)
    monkeypatch.setattr(transport_module, "LocalDemoTransport", fail_if_local_constructed)

    with pytest.raises(RuntimeError, match="strict_matrix_construction_failed"):
        transport_module.create_agentteams_transport("agentteams_strict")
