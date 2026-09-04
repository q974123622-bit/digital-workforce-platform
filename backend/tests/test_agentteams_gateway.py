from __future__ import annotations

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

    def get(self, url, **_kwargs):
        self.urls.append(url)
        return self.responses.pop(0)

    def close(self):
        pass


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
