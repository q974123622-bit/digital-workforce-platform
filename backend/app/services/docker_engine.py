"""Minimal Docker Engine client used by the offline backend container.

The host development path can keep using the Docker CLI. The packaged backend
has no CLI and talks to the mounted Unix socket with the already-installed
httpx dependency. Only the operations required for pre-created Harness
containers are exposed here.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

DOCKER_SOCKET = "/var/run/docker.sock"


@dataclass(frozen=True)
class EngineResult:
    returncode: int
    stdout: str = ""
    stderr: str = ""


class DockerEngineClient:
    def __init__(self, socket_path: str = DOCKER_SOCKET, timeout: float = 15):
        transport = httpx.HTTPTransport(uds=socket_path)
        self.client = httpx.Client(transport=transport, base_url="http://docker", timeout=timeout)

    def close(self) -> None:
        self.client.close()

    def version(self) -> str:
        response = self.client.get("/version")
        response.raise_for_status()
        return str(response.json().get("Version") or "")

    def container_status(self, name: str) -> str | None:
        response = self.client.get(f"/containers/{quote(name, safe='')}/json")
        if response.status_code == 404:
            return None
        response.raise_for_status()
        return str(response.json().get("State", {}).get("Status") or "")

    def image_exists(self, image: str) -> bool:
        response = self.client.get(f"/images/{quote(image, safe='')}/json")
        return response.status_code == 200

    def start_container(self, name: str) -> EngineResult:
        response = self.client.post(f"/containers/{quote(name, safe='')}/start")
        if response.status_code in (204, 304):
            return EngineResult(0)
        return EngineResult(1, stderr=response.text[:500])

    def stop_container(self, name: str, seconds: int = 5) -> EngineResult:
        response = self.client.post(f"/containers/{quote(name, safe='')}/stop", params={"t": seconds})
        if response.status_code in (204, 304):
            return EngineResult(0)
        return EngineResult(1, stderr=response.text[:500])

    def exec(
        self,
        name: str,
        command: list[str],
        *,
        environment: dict[str, str] | None = None,
        workdir: str | None = None,
    ) -> EngineResult:
        payload: dict[str, object] = {
            "AttachStdout": True,
            "AttachStderr": True,
            "Tty": False,
            "Cmd": command,
        }
        if environment:
            payload["Env"] = [f"{key}={value}" for key, value in environment.items()]
        if workdir:
            payload["WorkingDir"] = workdir
        created = self.client.post(
            f"/containers/{quote(name, safe='')}/exec", json=payload
        )
        if created.status_code != 201:
            return EngineResult(1, stderr=created.text[:500])
        exec_id = str(created.json()["Id"])
        started = self.client.post(f"/exec/{exec_id}/start", json={"Detach": False, "Tty": False})
        if started.status_code != 200:
            return EngineResult(1, stderr=started.text[:500])
        stdout, stderr = _demux(started.content)
        inspected = self.client.get(f"/exec/{exec_id}/json")
        exit_code = int(inspected.json().get("ExitCode") or 0) if inspected.status_code == 200 else 1
        return EngineResult(
            exit_code,
            stdout.decode("utf-8", errors="replace"),
            stderr.decode("utf-8", errors="replace"),
        )


def _demux(payload: bytes) -> tuple[bytes, bytes]:
    """Decode Docker's non-TTY stdout/stderr multiplexed stream."""
    stdout = bytearray()
    stderr = bytearray()
    offset = 0
    while offset + 8 <= len(payload) and payload[offset] in (0, 1, 2):
        stream = payload[offset]
        size = int.from_bytes(payload[offset + 4 : offset + 8], "big")
        start, end = offset + 8, offset + 8 + size
        if end > len(payload):
            break
        (stderr if stream == 2 else stdout).extend(payload[start:end])
        offset = end
    if offset == 0:
        stdout.extend(payload)
    return bytes(stdout), bytes(stderr)
