"""One persistent DeepSeek Harness container for every active digital identity."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_IMAGE = os.getenv("DWP_HARNESS_IMAGE", "dwp-dsh:rc6")
DOCKER_TIMEOUT = 15


def container_name(employee_id: str) -> str:
    safe = re.sub(r"[^a-z0-9-]+", "-", employee_id.lower()).strip("-")
    return f"dwp-harness-{safe}"


def workspace_ref(employee_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", employee_id)
    return str(REPO_ROOT / "backend" / "harness-workspaces" / safe)


def _docker(*args: str, timeout: int = DOCKER_TIMEOUT) -> subprocess.CompletedProcess[str]:
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise RuntimeError("未找到 Docker CLI")
    try:
        return subprocess.run(
            [docker_bin, *args], capture_output=True, text=True, encoding="utf-8",
            errors="replace", timeout=timeout, stdin=subprocess.DEVNULL,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError(f"Docker 命令执行失败：{exc.__class__.__name__}") from exc


def docker_available() -> bool:
    try:
        return _docker("version", "--format", "{{.Server.Version}}", timeout=5).returncode == 0
    except RuntimeError:
        return False


def _container_status(name: str) -> str | None:
    proc = _docker("inspect", "--format", "{{.State.Status}}", name)
    return proc.stdout.strip() if proc.returncode == 0 else None


def _assert_image() -> None:
    if _docker("image", "inspect", HARNESS_IMAGE).returncode != 0:
        raise RuntimeError(f"Harness 镜像不存在：{HARNESS_IMAGE}")


def ensure_container(employee_id: str) -> str:
    """Create or reuse the employee's stable container and verify dsh is available."""
    if not docker_available():
        raise RuntimeError("Docker Engine 不可用，请先启动 Docker Desktop")
    _assert_image()
    name = container_name(employee_id)
    root = Path(workspace_ref(employee_id))
    dsh_home, workspace = root / "dsh-home", root / "workspace"
    dsh_home.mkdir(parents=True, exist_ok=True)
    workspace.mkdir(parents=True, exist_ok=True)

    status = _container_status(name)
    if status is None:
        api_key = config.get("DEEPSEEK_API_KEY")
        if not api_key:
            raise RuntimeError("DEEPSEEK_API_KEY 未配置")
        model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash"
        env_path = None
        try:
            with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as env_file:
                env_file.write(
                    f"DEEPSEEK_API_KEY={api_key}\nDEEPSEEK_MODEL={model}\n"
                    "DSH_PERMISSION_MODE=read-only\nDSH_HOME=/dsh-home\n"
                )
                env_path = env_file.name
            proc = _docker(
                "run", "-d", "--name", name, "--restart", "unless-stopped",
                "--label", "dwp.managed=true", "--label", f"dwp.employee_id={employee_id}",
                "--env-file", env_path,
                "--mount", f"type=bind,source={dsh_home.resolve()},target=/dsh-home",
                "--mount", f"type=bind,source={workspace.resolve()},target=/workspace",
                "--workdir", "/workspace", "--entrypoint", "sh", HARNESS_IMAGE,
                "-c", (
                    "mkdir -p /dsh-home/profiles/dwp-knowledge-agent-v2 && "
                    "cp /opt/dwp/profile/cordis.yml /opt/dwp/profile/cordis.patch.yml "
                    "/opt/dwp/profile/package.json /dsh-home/profiles/dwp-knowledge-agent-v2/ && "
                    "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"
                ),
                timeout=30,
            )
        finally:
            if env_path:
                try:
                    os.remove(env_path)
                except OSError:
                    pass
        if proc.returncode != 0:
            raise RuntimeError(f"创建 Harness 容器失败：{(proc.stderr or proc.stdout).strip()[:300]}")
    elif status != "running":
        proc = _docker("start", name, timeout=30)
        if proc.returncode != 0:
            raise RuntimeError(f"启动 Harness 容器失败：{(proc.stderr or proc.stdout).strip()[:300]}")

    profile_sync = _docker(
        "exec", name, "sh", "-c",
        "mkdir -p /dsh-home/profiles/dwp-knowledge-agent-v2 && "
        "cp /opt/dwp/profile/cordis.yml /opt/dwp/profile/cordis.patch.yml "
        "/opt/dwp/profile/package.json /dsh-home/profiles/dwp-knowledge-agent-v2/",
    )
    if profile_sync.returncode != 0:
        raise RuntimeError(f"Harness 安全 Profile 同步失败：{name}")

    probe = _docker("exec", name, "sh", "-c", "command -v dsh >/dev/null 2>&1")
    if probe.returncode != 0:
        raise RuntimeError(f"Harness 容器健康检查失败：{name}")
    return name


def ensure_runtime(db: Session, employee_id: str) -> models.AgentRuntime:
    row = db.get(models.AgentRuntime, employee_id)
    if row is None:
        row = models.AgentRuntime(
            employee_id=employee_id, engine="harness", container_name=container_name(employee_id),
            state="stopped", workspace_ref=workspace_ref(employee_id),
        )
        db.add(row)
        db.commit()
        db.refresh(row)
    return row


def start(db: Session, employee_id: str) -> models.AgentRuntime:
    row = ensure_runtime(db, employee_id)
    try:
        row.container_name = ensure_container(employee_id)
        row.engine, row.state, row.last_error = "harness", "ready", ""
    except RuntimeError as exc:
        row.state, row.last_error = "failed", str(exc)[:500]
        db.add(row)
        db.commit()
        raise
    row.last_active_at = datetime.now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def stop(db: Session, employee_id: str) -> models.AgentRuntime:
    row = ensure_runtime(db, employee_id)
    if row.state == "busy":
        raise ValueError("数字员工正在执行任务，不能直接停止")
    status = _container_status(row.container_name) if docker_available() else None
    if status == "running":
        proc = _docker("stop", "--time", "5", row.container_name, timeout=10)
        if proc.returncode != 0:
            raise RuntimeError(f"停止 Harness 容器失败：{(proc.stderr or proc.stdout).strip()[:300]}")
    row.state, row.last_error = "stopped", ""
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def ensure_all_active(db: Session) -> list[models.AgentRuntime]:
    """Fail-fast startup reconciliation for all active twins and role employees."""
    employees = db.scalars(
        select(models.DigitalEmployee).where(models.DigitalEmployee.status == "active")
        .order_by(models.DigitalEmployee.employee_no)
    ).all()
    runtimes = []
    for employee in employees:
        employee.runtime_type = "harness"
        employee.runtime_ref = container_name(employee.employee_no)
        runtimes.append(start(db, employee.employee_no))
    db.commit()
    return runtimes


def mark_busy(db: Session, employee_id: str) -> models.AgentRuntime:
    row = start(db, employee_id)
    row.state, row.last_active_at = "busy", datetime.now()
    db.commit()
    db.refresh(row)
    return row


def mark_ready(db: Session, employee_id: str, error: str = "") -> models.AgentRuntime:
    row = ensure_runtime(db, employee_id)
    row.state = "failed" if error else "ready"
    row.last_error, row.last_active_at = error[:500], datetime.now()
    db.commit()
    db.refresh(row)
    return row


def main() -> None:
    if len(sys.argv) != 2 or sys.argv[1] != "ensure-all":
        raise SystemExit("usage: python -m app.services.runtime_manager ensure-all")
    from ..database import SessionLocal
    from ..seed import seed_if_empty
    seed_if_empty()
    db = SessionLocal()
    try:
        for row in ensure_all_active(db):
            print(f"{row.employee_id}: {row.container_name} ({row.state})")
    finally:
        db.close()


if __name__ == "__main__":
    main()
