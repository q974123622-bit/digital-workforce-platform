"""Create the private local Matrix identities used by the strict AgentTeams demo.

This program never prints account passwords, access tokens, or the Synapse
registration secret.  Its only persistent credential output is the ignored
``.agentteams-runtime/runtime.json`` file consumed by the strict runtime.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import sys
import tempfile
from typing import Any, Callable, Mapping

import httpx


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.agentteams_runtime_config import require_safe_path
DEFAULT_MATRIX_URL = "http://127.0.0.1:8008"
TEAM_ID = "TEAM-ONBOARD"
ACCOUNT_NAMES = ("platform", "manager", "hr", "it", "audit")
ROLE_IDENTITIES = {
    "manager": {"agent_id": "VE-0001", "skill": "agentteams-onboarding-readiness", "tools": []},
    "hr": {
        "agent_id": "VE-0002", "skill": "hr-onboarding-assistant",
        "tools": [{"name": "agentteams.hr.lookup_onboarding", "plugin_id": "agentteams-onboarding-employee", "action": "execute", "required_capability": "hr.onboarding.read"}],
    },
    "it": {
        "agent_id": "VE-0003", "skill": "it-service-assistant",
        "tools": [{"name": "agentteams.it.lookup_readiness", "plugin_id": "agentteams-it-readiness", "action": "execute", "required_capability": "it.readiness.read"}],
    },
    "audit": {
        "agent_id": "VE-0005", "skill": "audit-evidence-review",
        "tools": [{"name": "agentteams.audit.lookup_evidence", "plugin_id": "agentteams-onboarding-evidence", "action": "execute", "required_capability": "audit.evidence.read"}],
    },
}
TEAM_CATALOG = {
    "VE-0002": {"capabilities": ["hr.onboarding.read"]},
    "VE-0003": {"capabilities": ["it.readiness.read"]},
    "VE-0005": {"capabilities": ["audit.evidence.read"]},
}


class BootstrapError(RuntimeError):
    """A safe bootstrap failure intended for operators, never raw HTTP output."""


def default_runtime_dir() -> Path:
    return ROOT / ".agentteams-runtime"


def _require_runtime_path(path: Path, *, allow_missing: bool = False) -> None:
    try:
        require_safe_path(path, allow_missing=allow_missing)
    except ValueError as exc:
        raise BootstrapError("invalid_runtime_path") from exc


def _write_private_text(path: Path, value: str) -> None:
    """Atomically replace one runtime secret file without logging its contents.

    ``chmod`` is best-effort only on Windows: processes under the same OS
    account remain inside this local deployment trust boundary, so it is not
    represented as a portable ACL guarantee.
    """
    try:
        _require_runtime_path(path.parent, allow_missing=True)
        path.parent.mkdir(parents=True, exist_ok=True)
        _require_runtime_path(path.parent)
        _require_runtime_path(path, allow_missing=True)
    except ValueError as exc:
        raise BootstrapError("invalid_runtime_path") from exc
    try:
        os.chmod(path.parent, 0o700)
    except OSError:
        pass
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent, text=True)
    temporary = Path(temporary_name)
    temporary_identity: tuple[int, int] | None = None
    try:
        try:
            os.chmod(temporary, 0o600)
        except OSError:
            pass
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as handle:
            metadata = os.fstat(handle.fileno())
            temporary_identity = (metadata.st_dev, metadata.st_ino)
            handle.write(value)
            handle.flush()
            os.fsync(handle.fileno())
        _require_runtime_path(path.parent)
        _require_runtime_path(temporary)
        path_metadata = os.lstat(temporary)
        if (not os.path.isfile(temporary)
                or (path_metadata.st_dev, path_metadata.st_ino) != temporary_identity
                or getattr(path_metadata, "st_file_attributes", 0) & 0x400):
            raise BootstrapError("invalid_runtime_path")
        os.replace(temporary, path)
        final_metadata = os.lstat(path)
        if ((final_metadata.st_dev, final_metadata.st_ino) != temporary_identity
                or getattr(final_metadata, "st_file_attributes", 0) & 0x400):
            raise BootstrapError("invalid_runtime_path")
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    except ValueError as exc:
        raise BootstrapError("invalid_runtime_path") from exc
    finally:
        if temporary.exists():
            try:
                metadata = temporary.stat(follow_symlinks=False)
                if temporary_identity == (metadata.st_dev, metadata.st_ino):
                    temporary.unlink()
            except OSError:
                pass


def ensure_synapse_environment(runtime_dir: Path) -> Path:
    """Create the local shared secret once; later invocations reuse it."""
    target = runtime_dir / "synapse-registration-secret"
    _require_runtime_path(runtime_dir, allow_missing=True)
    _require_runtime_path(target, allow_missing=True)
    if target.is_file():
        if len(target.read_text(encoding="utf-8").strip()) > 48:
            return target
        raise BootstrapError("invalid_synapse_secret_file")
    # One development iteration wrote the same value in an env-file form.
    # Reuse it if present so an existing local Synapse keeps accepting the
    # persisted runtime credentials after upgrading this launcher.
    legacy = runtime_dir / "synapse.env"
    _require_runtime_path(legacy, allow_missing=True)
    if legacy.is_file():
        prefix = "SYNAPSE_REGISTRATION_SHARED_SECRET="
        for line in legacy.read_text(encoding="utf-8").splitlines():
            if line.startswith(prefix) and len(line[len(prefix):].strip()) > 48:
                _write_private_text(target, line[len(prefix):].strip() + "\n")
                return target
    _write_private_text(target, secrets.token_urlsafe(48) + "\n")
    return target


def _read_synapse_secret(runtime_dir: Path) -> str:
    target = ensure_synapse_environment(runtime_dir)
    _require_runtime_path(target)
    value = target.read_text(encoding="utf-8").strip()
    if value:
        return value
    raise BootstrapError("invalid_synapse_secret_file")


def write_runtime_configuration(path: Path, runtime: Mapping[str, Any]) -> None:
    """Persist the strict runtime contract via one private atomic replacement."""
    _write_private_text(path, json.dumps(runtime, ensure_ascii=False, sort_keys=True, separators=(",", ":")) + "\n")


def build_runtime_configuration(credentials: Mapping[str, Mapping[str, str]], room_id: str, *, matrix_url: str = DEFAULT_MATRIX_URL) -> dict[str, Any]:
    """Build exactly the Task 6 strict runtime schema from five Matrix accounts."""
    if set(credentials) != set(ACCOUNT_NAMES) or not isinstance(room_id, str) or not room_id:
        raise BootstrapError("invalid_matrix_bootstrap")
    platform = credentials["platform"]
    if not all(isinstance(platform.get(key), str) and platform[key] for key in ("mxid", "token")):
        raise BootstrapError("invalid_matrix_bootstrap")
    agents: dict[str, dict[str, Any]] = {}
    for role, definition in ROLE_IDENTITIES.items():
        identity = credentials[role]
        if not all(isinstance(identity.get(key), str) and identity[key] for key in ("mxid", "token")):
            raise BootstrapError("invalid_matrix_bootstrap")
        agents[role] = {
            "agent_id": definition["agent_id"], "mxid": identity["mxid"], "token": identity["token"],
            "allowed_skill_ids": [definition["skill"]], "safe_tool_catalog": definition["tools"],
        }
    return {
        "matrix_url": matrix_url.rstrip("/"), "room_id": room_id, "team_id": TEAM_ID,
        "platform": {"mxid": platform["mxid"], "token": platform["token"]},
        "agents": agents, "team_catalog": TEAM_CATALOG,
    }


class SynapseAdminClient:
    """The small, explicit Client-Server/Admin surface needed by bootstrap."""

    def __init__(self, base_url: str, registration_secret: str, *, known_accounts: Mapping[str, Mapping[str, str]] | None = None, known_room_id: str | None = None):
        self.base_url = base_url.rstrip("/")
        self.registration_secret = registration_secret
        self.known_accounts = {name: dict(value) for name, value in (known_accounts or {}).items()}
        self.known_room_id = known_room_id
        self.client = httpx.Client(timeout=10.0)

    def close(self) -> None:
        self.client.close()

    def ensure_account(self, name: str, password: str) -> dict[str, str]:
        existing = self.known_accounts.get(name)
        if existing and self._whoami(existing["token"]) == existing["mxid"]:
            return existing
        try:
            nonce_response = self.client.get(f"{self.base_url}/_synapse/admin/v1/register")
            nonce = nonce_response.json().get("nonce") if nonce_response.status_code == 200 else None
            if not isinstance(nonce, str) or not nonce:
                raise BootstrapError("matrix_registration_unavailable")
            payload = {"nonce": nonce, "username": name, "password": password, "admin": False}
            payload["mac"] = hmac.new(
                self.registration_secret.encode("utf-8"), f"{nonce}\x00{name}\x00{password}\x00notadmin".encode("utf-8"), hashlib.sha1
            ).hexdigest()
            response = self.client.post(f"{self.base_url}/_synapse/admin/v1/register", json=payload)
            data = response.json() if response.status_code == 200 else {}
            mxid, token = data.get("user_id"), data.get("access_token")
            if not isinstance(mxid, str) or not isinstance(token, str) or not mxid.startswith("@") or not token:
                raise BootstrapError("matrix_account_registration_failed")
            identity = {"mxid": mxid, "token": token}
            self.known_accounts[name] = identity
            return identity
        except httpx.HTTPError as exc:
            raise BootstrapError("matrix_registration_unavailable") from exc

    def ensure_private_room(self, platform: Mapping[str, str], aliases: list[str]) -> str:
        if self.known_room_id:
            return self.known_room_id
        try:
            response = self.client.post(
                f"{self.base_url}/_matrix/client/v3/createRoom", headers=self._headers(platform["token"]),
                json={"preset": "private_chat", "is_direct": False, "invite": [], "name": "AgentTeams private demo", "room_alias_name": aliases[0]},
            )
            data = response.json() if response.status_code == 200 else {}
            room_id = data.get("room_id")
            if not isinstance(room_id, str) or not room_id:
                raise BootstrapError("matrix_room_creation_failed")
            self.known_room_id = room_id
            return room_id
        except httpx.HTTPError as exc:
            raise BootstrapError("matrix_room_creation_failed") from exc

    def invite_and_join(self, room_id: str, identity: Mapping[str, str]) -> None:
        try:
            invitation = self.client.post(
                f"{self.base_url}/_matrix/client/v3/rooms/{room_id}/invite", headers=self._headers(self.known_accounts["platform"]["token"]), json={"user_id": identity["mxid"]},
            )
            if invitation.status_code not in {200, 403}:
                raise BootstrapError("matrix_room_invite_failed")
            joined = self.client.post(f"{self.base_url}/_matrix/client/v3/join/{room_id}", headers=self._headers(identity["token"]), json={})
            if joined.status_code != 200:
                raise BootstrapError("matrix_room_join_failed")
        except httpx.HTTPError as exc:
            raise BootstrapError("matrix_room_join_failed") from exc

    def joined_members(self, room_id: str, platform: Mapping[str, str]) -> set[str]:
        try:
            response = self.client.get(f"{self.base_url}/_matrix/client/v3/rooms/{room_id}/joined_members", headers=self._headers(platform["token"]))
            joined = response.json().get("joined") if response.status_code == 200 else None
            if not isinstance(joined, dict):
                raise BootstrapError("matrix_membership_verification_failed")
            return {value for value in joined if isinstance(value, str)}
        except httpx.HTTPError as exc:
            raise BootstrapError("matrix_membership_verification_failed") from exc

    def _whoami(self, token: str) -> str:
        try:
            response = self.client.get(f"{self.base_url}/_matrix/client/v3/account/whoami", headers=self._headers(token))
            user_id = response.json().get("user_id") if response.status_code == 200 else None
            return user_id if isinstance(user_id, str) else ""
        except httpx.HTTPError:
            return ""

    @staticmethod
    def _headers(token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}


def bootstrap_matrix(matrix: Any, *, password_factory: Callable[[str], str] | None = None) -> dict[str, Any]:
    """Ensure five accounts share one private room and verify the membership set."""
    make_password = password_factory or (lambda _name: secrets.token_urlsafe(32))
    accounts = {name: matrix.ensure_account(name, make_password(name)) for name in ACCOUNT_NAMES}
    room_id = matrix.ensure_private_room(accounts["platform"], ["agentteams-onboard"])
    for name in ACCOUNT_NAMES:
        matrix.invite_and_join(room_id, accounts[name])
    expected = {identity["mxid"] for identity in accounts.values()}
    if matrix.joined_members(room_id, accounts["platform"]) != expected:
        raise BootstrapError("matrix_membership_verification_failed")
    return {"accounts": accounts, "room_id": room_id}


def _existing_runtime(path: Path) -> tuple[dict[str, dict[str, str]], str | None]:
    try:
        _require_runtime_path(path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        accounts = {"platform": raw["platform"]}
        accounts.update(raw["agents"])
        selected = {name: {"mxid": accounts[name]["mxid"], "token": accounts[name]["token"]} for name in ACCOUNT_NAMES}
        return selected, raw.get("room_id") if isinstance(raw.get("room_id"), str) else None
    except (OSError, KeyError, TypeError, ValueError):
        return {}, None


def print_bootstrap_success(runtime: Mapping[str, Any]) -> None:
    names = ", ".join(ACCOUNT_NAMES)
    print(f"Matrix bootstrap complete: accounts={names}; room_id={runtime['room_id']}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Bootstrap the private local AgentTeams Synapse room")
    parser.add_argument("--runtime-dir", type=Path, default=default_runtime_dir())
    parser.add_argument("--matrix-url", default=DEFAULT_MATRIX_URL)
    parser.add_argument("--prepare-synapse-env", action="store_true")
    args = parser.parse_args(argv)
    runtime_dir = args.runtime_dir.absolute()
    try:
        ensure_synapse_environment(runtime_dir)
        if args.prepare_synapse_env:
            print("Synapse bootstrap secret prepared.")
            return 0
        path = runtime_dir / "runtime.json"
        known_accounts, known_room = _existing_runtime(path)
        client = SynapseAdminClient(args.matrix_url, _read_synapse_secret(runtime_dir), known_accounts=known_accounts, known_room_id=known_room)
        try:
            result = bootstrap_matrix(client)
        finally:
            client.close()
        runtime = build_runtime_configuration(result["accounts"], result["room_id"], matrix_url=args.matrix_url)
        write_runtime_configuration(path, runtime)
        print_bootstrap_success(runtime)
        return 0
    except BootstrapError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except (OSError, ValueError):
        print("matrix_bootstrap_io_failed", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
