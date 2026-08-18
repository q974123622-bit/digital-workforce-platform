"""SandboxManager：真容器优先、local 兜底（Sprint 3 MockExecutor 的升级实现）。

- docker_available()：探测 daemon（`docker version --format {{.Server.Version}}`，超时 3 秒，
  任何异常/非零返回/空输出都视为不可用）；
- execute()：Docker 可用 → 单容器隔离执行：
  `docker run --rm --network none -v <宿主工作区>:/workspace/{employee_id} python:3.11-slim <command>`
  - 宿主工作区默认 backend/sandbox-workspaces/{employee_id}（自动创建）；
  - 执行超时（默认 30 秒）→ docker kill + docker rm，返回 status=timeout，不挂起请求；
  - daemon 探测失败 / docker run 启动失败 / 退出码非 0 / 镜像缺失 → 自动降级 local（复用 MockExecutor 行为）；
- 本模块只做执行隔离，不产出授权决策；授权由路由层 Policy Engine 完成（先 Policy 后执行，被拒不启动容器）。

安全：容器固定 --network none；审计由路由层统一写入，命令/结果只放摘要（前 200 字符），
不记录任何凭据/Key。
"""

import re
import subprocess
import uuid
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT / "backend" / "sandbox-workspaces"
DOCKER_IMAGE = "python:3.11-slim"
DOCKER_PROBE_TIMEOUT = 3.0
EXEC_TIMEOUT = 30.0
_CLEANUP_TIMEOUT = 10.0
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def docker_available(timeout: float = DOCKER_PROBE_TIMEOUT) -> bool:
    """探测 Docker daemon 是否可用；独立函数，便于测试 monkeypatch。"""
    try:
        result = subprocess.run(
            ["docker", "version", "--format", "{{.Server.Version}}"],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            creationflags=_CREATE_NO_WINDOW,
        )
        return result.returncode == 0 and bool((result.stdout or "").strip())
    except Exception:  # noqa: BLE001
        return False


class SandboxManager:
    """真容器优先、local 兜底的 Sandbox 执行器。"""

    def __init__(self, exec_timeout: float = EXEC_TIMEOUT, probe_timeout: float = DOCKER_PROBE_TIMEOUT):
        self.exec_timeout = exec_timeout
        self.probe_timeout = probe_timeout

    def execute(
        self,
        *,
        employee_id: str,
        command: str,
        mount_dir: str = "",
        network: str = "none",
        execution_location: str = "remote",
        trace_id: str = "",
    ) -> dict:
        """执行一次沙箱任务；返回 {mode: docker|local, status: ok|timeout, logs: [...]}。"""
        if not docker_available(self.probe_timeout):
            return self._local(mount_dir, network, execution_location, command, reason="docker daemon 不可用")

        workspace = self._ensure_workspace(employee_id)
        container_name = f"dwp-sbx-{uuid.uuid4().hex[:12]}"
        safe_command = command or 'python -c "print(\'sandbox ok\')"'
        cmd = [
            "docker", "run", "--rm", "--name", container_name,
            "--network", "none",
            "-v", f"{workspace}:/workspace/{employee_id}",
            DOCKER_IMAGE,
            safe_command,
        ]
        try:
            proc = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except Exception:  # noqa: BLE001
            return self._local(mount_dir, network, execution_location, command, reason="docker run 启动失败")

        try:
            stdout, stderr = proc.communicate(timeout=self.exec_timeout)
        except subprocess.TimeoutExpired:
            self._cleanup_container(container_name)
            try:
                proc.kill()
            except Exception:  # noqa: BLE001
                pass
            try:
                proc.wait(timeout=5)
            except Exception:  # noqa: BLE001
                pass
            return {
                "mode": "docker",
                "status": "timeout",
                "logs": [f"[sandbox] 执行超时（{int(self.exec_timeout)} 秒），容器已停止并清理"],
            }

        if proc.returncode != 0:
            return self._local(
                mount_dir,
                network,
                execution_location,
                command,
                reason=f"docker run 退出码 {proc.returncode}",
            )

        logs = [ln for ln in (stdout or "").splitlines() if ln.strip()][:20]
        logs += [ln for ln in (stderr or "").splitlines() if ln.strip()][:20]
        return {"mode": "docker", "status": "ok", "logs": logs or ["[sandbox] docker 执行完成"]}

    def _local(self, mount_dir: str, network: str, execution_location: str, command: str, reason: str) -> dict:
        """local 兜底：复用 MockExecutor 行为（仅演示日志，不启动容器）。"""
        logs = [
            "[sandbox] executor=local-fallback (demo)",
            f"[sandbox] mount: {mount_dir or '/workspace'}, network={network}",
            f"[sandbox] location={execution_location}, command={command[:200] or '(none)'}",
        ]
        if reason:
            logs.append(f"[sandbox] degraded: {reason}")
        logs.append("[sandbox] status=ok (mock)")
        return {"mode": "local", "status": "ok", "logs": logs}

    def _ensure_workspace(self, employee_id: str) -> str:
        """创建并返回宿主工作区 backend/sandbox-workspaces/{employee_id}。"""
        safe = re.sub(r"[^A-Za-z0-9_-]", "_", employee_id) or "unknown"
        ws = WORKSPACE_ROOT / safe
        ws.mkdir(parents=True, exist_ok=True)
        return str(ws)

    def _cleanup_container(self, container_name: str) -> None:
        """超时后清理容器：docker kill + docker rm --force，尽力而为不抛错。"""
        for args in (["docker", "kill", container_name], ["docker", "rm", "--force", container_name]):
            try:
                subprocess.run(
                    args,
                    capture_output=True,
                    timeout=_CLEANUP_TIMEOUT,
                    creationflags=_CREATE_NO_WINDOW,
                )
            except Exception:  # noqa: BLE001
                pass
