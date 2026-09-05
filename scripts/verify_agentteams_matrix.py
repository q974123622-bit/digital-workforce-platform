"""Independently verify the five-account private Matrix runtime contract.

This operator tool only reads the strict runtime file and talks to the
already-running Matrix server.  It deliberately never starts Synapse, creates
accounts, or prints credentials.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import time
from typing import Any, Callable
from uuid import uuid4


BACKEND_ROOT = Path(__file__).resolve().parents[1] / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.agentteams_gateway import AgentTeamsGateway, AgentTeamsUnavailableError  # noqa: E402
from app.services.agentteams_runtime_config import (  # noqa: E402
    MatrixIdentity,
    StrictRuntimeConfig,
    load_strict_runtime_config,
)


def _gateway(runtime: StrictRuntimeConfig, identity: MatrixIdentity) -> AgentTeamsGateway:
    return AgentTeamsGateway(base_url=runtime.matrix_url, matrix_token=identity.token)


def _safe_failure(code: str) -> dict[str, Any]:
    return {"ok": False, "accounts": [], "event_ids": {}, "reason": code}


def verify_matrix_environment(
    runtime: StrictRuntimeConfig,
    *,
    gateway_factory: Callable[[StrictRuntimeConfig, MatrixIdentity], Any] = _gateway,
    pause: Callable[[float], None] = time.sleep,
    sync_attempts: int = 5,
) -> dict[str, Any]:
    """Prove all five credentials identify distinct joined room members.

    Each account sends a unique harmless ping, then independently syncs until
    all five resulting Matrix event IDs are visible.  The return value contains
    no access tokens, passwords, request bodies, or Matrix response payloads.
    """
    identities = {"platform": runtime.platform, **dict(runtime.agents)}
    expected_mxids = {identity.mxid for identity in identities.values()}
    if len(identities) != 5 or len(expected_mxids) != 5:
        return _safe_failure("invalid_runtime_identities")

    clients: dict[str, Any] = {}
    try:
        for name, identity in identities.items():
            client = gateway_factory(runtime, identity)
            clients[name] = client
            if client.whoami() != identity.mxid:
                return _safe_failure("whoami_mismatch")
        members = clients["platform"].joined_members(runtime.room_id)
        if set(members) != expected_mxids:
            return _safe_failure("room_membership_mismatch")

        event_ids: dict[str, str] = {}
        for name, client in clients.items():
            event_id = client.send_message(
                runtime.room_id, f"AgentTeams verifier ping {name} {uuid4().hex}"
            )
            if not isinstance(event_id, str) or not event_id.startswith("$"):
                return _safe_failure("matrix_send_missing_event_id")
            event_ids[name] = event_id

        expected_events = set(event_ids.values())
        for _ in range(max(1, sync_attempts)):
            all_seen = True
            for client in clients.values():
                batch = client.sync_messages(None, {runtime.room_id})
                seen = {
                    event.event_id
                    for event in getattr(batch, "events", [])
                    if event.room_id == runtime.room_id
                }
                if not expected_events.issubset(seen):
                    all_seen = False
                    break
            if all_seen:
                return {
                    "ok": True,
                    "accounts": sorted(identities),
                    "event_ids": {name: event_ids[name] for name in sorted(event_ids)},
                    "reason": "",
                }
            pause(0.5)
        return _safe_failure("matrix_sync_missing_verifier_events")
    except (AgentTeamsUnavailableError, OSError, ValueError, TypeError):
        return _safe_failure("matrix_verification_failed")
    finally:
        for client in clients.values():
            try:
                client.close()
            except Exception:  # noqa: BLE001 - cleanup cannot expose details or hide result
                pass


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Verify the strict five-account AgentTeams Matrix room")
    parser.add_argument("--runtime-config", type=Path, default=None)
    args = parser.parse_args(argv)
    try:
        runtime = load_strict_runtime_config(args.runtime_config)
    except ValueError:
        print(json.dumps(_safe_failure("invalid_runtime_config"), ensure_ascii=False))
        return 2
    evidence = verify_matrix_environment(runtime)
    print(json.dumps(evidence, ensure_ascii=False))
    return 0 if evidence["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
