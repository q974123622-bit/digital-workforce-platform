from __future__ import annotations

import json

import pytest

from app.services.agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError


class _Response:
    def __init__(self, status_code, payload):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class _Client:
    def __init__(self, responses):
        self.responses = list(responses)
        self.urls = []
        self.calls = []

    def get(self, url, **kwargs):
        self.urls.append(url)
        self.calls.append((url, kwargs))
        return self.responses.pop(0)

    def close(self):
        pass


def test_loopback_matrix_gateway_ignores_process_proxy_environment(monkeypatch):
    captured = {}

    class Client:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr("app.services.agentteams_gateway.httpx.Client", Client)

    AgentTeamsGateway(base_url="http://127.0.0.1:8008", matrix_token="secret")

    assert captured["trust_env"] is False


def test_gateway_reads_only_whoami_and_joined_member_mxids():
    gateway = AgentTeamsGateway(base_url="http://matrix", matrix_token="secret")
    gateway._client = _Client([
        _Response(200, {"user_id": "@platform:test", "access_token": "must-not-return"}),
        _Response(200, {"joined": {"@manager:test": {}, "@hr:test": {}}}),
    ])

    assert gateway.whoami() == "@platform:test"
    assert gateway.joined_members("!team:test") == {"@manager:test", "@hr:test"}
    assert all("secret" not in url for url in gateway._client.urls)


def test_gateway_maps_membership_http_failure_without_response_body():
    gateway = AgentTeamsGateway(base_url="http://matrix", matrix_token="secret")
    gateway._client = _Client([_Response(403, {"error": "token=secret"})])

    with pytest.raises(AgentTeamsUnavailableError, match="joined_members.*HTTP 403"):
        gateway.joined_members("!team:test")


def test_sync_requests_a_bounded_room_timeline_large_enough_for_agent_bursts():
    gateway = AgentTeamsGateway(base_url="http://matrix", matrix_token="secret")
    gateway._client = _Client([
        _Response(200, {
            "next_batch": "s2",
            "rooms": {"join": {"!team:test": {"timeline": {"events": []}}}},
        }),
    ])

    gateway.sync_messages("s1", {"!team:test"})

    params = gateway._client.calls[0][1]["params"]
    matrix_filter = json.loads(params["filter"])
    assert matrix_filter["room"]["timeline"]["limit"] >= 100


def test_sync_rejects_a_limited_timeline_instead_of_advancing_past_missing_events():
    gateway = AgentTeamsGateway(base_url="http://matrix", matrix_token="secret")
    gateway._client = _Client([
        _Response(200, {
            "next_batch": "s2",
            "rooms": {
                "join": {
                    "!team:test": {
                        "timeline": {"limited": True, "prev_batch": "gap", "events": []}
                    }
                }
            },
        }),
    ])

    with pytest.raises(AgentTeamsUnavailableError, match="limited"):
        gateway.sync_messages("s1", {"!team:test"})


def test_initial_cursor_uses_an_empty_timeline_and_accepts_the_matrix_baseline():
    gateway = AgentTeamsGateway(base_url="http://matrix", matrix_token="secret")
    gateway._client = _Client([
        _Response(200, {
            "next_batch": "fresh-cursor",
            "rooms": {
                "join": {
                    "!team:test": {
                        "timeline": {"limited": True, "prev_batch": "history", "events": []}
                    }
                }
            },
        }),
    ])

    assert gateway.initial_sync_cursor({"!team:test"}) == "fresh-cursor"

    params = gateway._client.calls[0][1]["params"]
    matrix_filter = json.loads(params["filter"])
    assert matrix_filter["room"]["timeline"]["limit"] == 0
