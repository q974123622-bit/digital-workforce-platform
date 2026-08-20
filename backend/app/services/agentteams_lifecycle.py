"""AgentTeams 生命周期绑定（Sprint 9）。

数字员工/数字分身 <-> AgentTeams worker 容器一一对应：
- 命名规则：dwp-{type}-{工号}，例如 VE-0001 -> dwp-ve-0001、DT-E10281 -> dwp-twin-e10281；
- 通过 controller 容器内的 `agt` CLI 管理 worker/team（v1 走 docker exec，零容器改动）；
- SOUL 注入平台数字员工的 role_prompt；凭据只从环境读取，不落日志。
"""

import json
import shlex
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from . import config
from .agentteams_gateway import AgentTeamsUnavailableError

def worker_name(employee_no: str, employee_type: str) -> str:
    """数字员工工号 -> AgentTeams worker 名（Matrix localpart）。

    VE-0001 -> dwp-ve-0001；DT-E10281 -> dwp-twin-e10281。
    """
    prefix = {"virtual": "ve", "twin": "twin", "rpa": "rpa"}.get(employee_type, "emp")
    raw_no = employee_no.split("-", 1)[-1] if "-" in employee_no else employee_no
    return f"dwp-{prefix}-{raw_no.lower()}"


def matrix_user_id(worker: str) -> str:
    return f"@{worker}:{config.agentteams_matrix_domain()}"


def _agt(args: list[str], timeout: float = 180, input_text: str | None = None) -> str:
    """在 controller 容器内执行 agt CLI；失败统一映射 AgentTeamsUnavailableError。"""
    container = config.agentteams_controller_container()
    docker_bin = shutil.which("docker")
    if not docker_bin:
        raise AgentTeamsUnavailableError("docker 不可用，无法管理 AgentTeams 生命周期")
    try:
        proc = subprocess.run(
            [docker_bin, "exec", container, "agt", *args],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            input=input_text,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        raise AgentTeamsUnavailableError(f"agt 执行失败：{exc.__class__.__name__}") from exc
    if proc.returncode != 0:
        raise AgentTeamsUnavailableError((proc.stderr or proc.stdout or "").strip()[:300])
    return proc.stdout


def _cp_into_container(local_path: Path) -> str:
    """把本地文件复制进 controller 容器，返回容器内路径。"""
    docker_bin = shutil.which("docker")
    container = config.agentteams_controller_container()
    remote = f"/tmp/{local_path.name}"
    subprocess.run(
        [docker_bin, "cp", str(local_path), f"{container}:{remote}"],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return remote


def create_worker(
    *,
    employee_no: str,
    employee_type: str,
    display_name: str,
    soul: str,
    wait_ready: bool = True,
    timeout: float = 240,
) -> str:
    """创建数字员工对应的 AgentTeams worker 容器，返回 worker 名。"""
    name = worker_name(employee_no, employee_type)
    if get_worker(name) is not None:
        return name
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(f"# {display_name}（{employee_no} · AgentTeams Worker: {name}）\n\n{soul}")
        local = Path(f.name)
    try:
        remote = _cp_into_container(local)
        _agt(
            [
                "create", "worker",
                "--name", name,
                "--model", config.agentteams_worker_model(),
                "--runtime", config.agentteams_worker_runtime(),
                "--soul-file", remote,
                "--no-wait",
            ]
        )
    finally:
        local.unlink(missing_ok=True)
    if wait_ready:
        deadline = time.time() + timeout
        while time.time() < deadline:
            w = get_worker(name)
            if w and w.get("phase") == "Running":
                return name
            time.sleep(5)
        raise AgentTeamsUnavailableError(f"worker {name} 创建后未就绪（{timeout}s 超时）")
    return name


def get_worker(name: str) -> dict | None:
    """查询单个 worker；不存在返回 None。"""
    out = _agt(["get", "workers", "-o", "json"], timeout=60)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise AgentTeamsUnavailableError("agt workers 返回了无效 JSON") from exc
    for w in data.get("workers", []):
        if w.get("name") == name:
            return w
    return None


def delete_worker(name: str) -> None:
    """删除 worker（容器/房间由 controller 自动清理）。"""
    if get_worker(name) is None:
        return
    # 团队成员需先从团队移除（controller 拒绝直接删除团队成员）
    team = get_team()
    if team:
        members = [m["name"] for m in team.get("workerMembers", [])]
        if name in members:
            remaining = [m for m in members if m != name]
            if remaining:
                leader = team.get("leaderName")
                if leader == name or leader not in remaining:
                    leader = remaining[0]
                try:
                    ensure_team_members(remaining, leader)
                except AgentTeamsUnavailableError:
                    # detach 旧房间可能 403（Manager 已在房间），但 workerMembers 已更新
                    pass
    _agt(["delete", "worker", name], timeout=180)


def update_worker_soul(name: str, display_name: str, soul: str) -> None:
    """更新 worker 的 SOUL（人设/显示名变更时同步）。"""
    if get_worker(name) is None:
        raise AgentTeamsUnavailableError(f"worker {name} 不存在")
    with tempfile.NamedTemporaryFile(
        "w", suffix=".md", delete=False, encoding="utf-8"
    ) as f:
        f.write(f"# {display_name}（AgentTeams Worker: {name}）\n\n{soul}")
        local = Path(f.name)
    try:
        remote = _cp_into_container(local)
        _agt(["apply", "worker", "--name", name, "--soul-file", remote], timeout=120)
    finally:
        local.unlink(missing_ok=True)


def get_team(team: str | None = None) -> dict | None:
    """查询团队资源（含 workerMembers/leaderName/teamRoomID）。"""
    name = team or config.agentteams_team_name()
    out = _agt(["get", "teams", "-o", "json"], timeout=60)
    try:
        data = json.loads(out)
    except json.JSONDecodeError as exc:
        raise AgentTeamsUnavailableError("agt teams 返回了无效 JSON") from exc
    for t in data.get("teams", []):
        if t.get("name") == name:
            return t
    return None


def add_team_member(worker: str) -> None:
    """把 worker 加入默认团队（保留现有成员与 leader）。"""
    team = get_team()
    if team is None:
        raise AgentTeamsUnavailableError(
            f"AgentTeams 团队 {config.agentteams_team_name()} 不存在"
        )
    members = [m["name"] for m in team.get("workerMembers", [])]
    leader = team.get("leaderName") or (members[0] if members else worker)
    if worker not in members:
        members.append(worker)
    ensure_team_members(members, leader)


def apply_team(team_yaml: str) -> None:
    """应用 Team 资源（workerMembers 变更会触发房间成员关系 reconcile）。"""
    with tempfile.NamedTemporaryFile(
        "w", suffix=".yaml", delete=False, encoding="utf-8"
    ) as f:
        f.write(team_yaml)
        local = Path(f.name)
    try:
        remote = _cp_into_container(local)
        _agt(["apply", "-f", remote], timeout=120)
    finally:
        local.unlink(missing_ok=True)


def ensure_team_members(worker_names: list[str], leader_name: str) -> None:
    """确保 team-onboard 的 workerMembers 与给定名单一致（幂等 apply）。"""
    team = config.agentteams_team_name()
    members = "\n".join(
        [
            f"    - name: {leader_name}\n      role: team_leader",
            *[
                f"    - name: {n}\n      role: worker"
                for n in worker_names
                if n != leader_name
            ],
        ]
    )
    yaml = (
        "apiVersion: agentteams.io/v1beta1\n"
        "kind: Team\n"
        "metadata:\n"
        f"  name: {team}\n"
        "spec:\n"
        "  admin:\n"
        "    name: platform-bot\n"
        "  humanMembers:\n"
        "    - name: manager\n"
        "      role: coordinator\n"
        "  workerMembers:\n"
        f"{members}\n"
    )
    apply_team(yaml)


def reset_agentteams_context() -> None:
    """清空 AgentTeams 侧的记忆与任务状态（平台"清空会话"联动调用）。

    清理范围：Manager 的 state.json（活动任务）、memory/会话文件（历史记忆）、
    MinIO 任务目录（旧任务交付物）。尽力而为，单项失败不抛出。
    """
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return
    access_key = config.get(config.AGENTTEAMS_MINIO_ACCESS_KEY)
    secret_key = config.get(config.AGENTTEAMS_MINIO_SECRET_KEY)
    manager_container = config.get(config.AGENTTEAMS_MANAGER_CONTAINER, "agentteams-manager")
    admin_dm_room_id = config.get(config.AGENTTEAMS_MANAGER_DM_ROOM_ID, "")
    if not access_key or not secret_key:
        # 管理性全局清理必须显式配置凭据，绝不在源码中保留默认密码。
        return
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    state = json.dumps(
        {"active_tasks": [], "updated_at": now, "admin_dm_room_id": admin_dm_room_id},
        ensure_ascii=False,
    )
    alias_command = (
        "mc alias set dwp-admin http://agentteams-controller:9000 "
        f"{shlex.quote(access_key)} {shlex.quote(secret_key)}"
    )
    cleanup_jobs = [
        (
            manager_container,
            (
                "echo '%s' > /root/manager-workspace/state.json && "
                f"{alias_command} >/dev/null 2>&1 && "
                "mc cp /root/manager-workspace/state.json dwp-admin/agentteams-storage/manager/state.json >/dev/null 2>&1"
            )
            % state,
        ),
        (
            manager_container,
            (
                "rm -f /root/manager-workspace/memory/*.md "
                "/root/manager-workspace/.copaw/workspaces/default/memory/*.md "
                "/root/manager-workspace/.copaw/workspaces/default/sessions/*.json "
                "/root/manager-workspace/.copaw/workspaces/default/chats.json "
                "/root/manager-workspace/.copaw/workspaces/default/tool_result/*.txt 2>/dev/null; true"
            ),
        ),
        (
            config.agentteams_controller_container(),
            (
                f"{alias_command} >/dev/null 2>&1; "
                "mc rm -r --force dwp-admin/agentteams-storage/shared/tasks/task-* 2>/dev/null; "
                "mc rm -r --force dwp-admin/agentteams-storage/teams/team-onboard/shared/tasks/task-* 2>/dev/null; true"
            ),
        ),
    ]
    for container, cmd in cleanup_jobs:
        try:
            result = subprocess.run(
                [docker_bin, "exec", container, "sh", "-c", cmd],
                capture_output=True,
                text=True,
                timeout=120,
            )
            if getattr(result, "returncode", 0) != 0:
                continue
        except (subprocess.TimeoutExpired, OSError):
            continue
