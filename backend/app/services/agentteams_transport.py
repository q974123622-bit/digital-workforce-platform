"""Explicit transport boundary for persisted AgentTeams collaboration."""

from __future__ import annotations

from typing import Protocol

from . import config
from .agentteams_gateway import AgentTeamsGateway, MatrixSyncBatch
from .agentteams_local_demo import LocalDemoTransport


class AgentTeamsTransport(Protocol):
    """Transport operations consumed by ``AgentTeamsCollaborationRunner``."""

    def send_protocol_message(
        self,
        room_id: str,
        text: str,
        protocol_line: str,
    ) -> str: ...

    def sync_messages(
        self,
        since: str | None,
        room_ids: set[str],
    ) -> MatrixSyncBatch: ...

    def close(self) -> None: ...


def create_agentteams_transport(mode: str | None = None) -> AgentTeamsTransport:
    """Construct exactly the selected transport without cross-mode fallback."""
    selected_mode = config.agentteams_mode() if mode is None else mode
    if selected_mode == "local_demo":
        return LocalDemoTransport()
    return AgentTeamsGateway()
