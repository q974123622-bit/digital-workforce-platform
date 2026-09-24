"""One persistent DeepSeek Harness container for every active digital identity."""

from __future__ import annotations

import json
import logging
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import config
from .docker_engine import DockerEngineClient, EngineResult, self_container_id

logger = logging.getLogger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[3]
HARNESS_IMAGE = os.getenv("DWP_HARNESS_IMAGE", "dwp-dsh:rc6")
DOCKER_TIMEOUT = 15

# ---- L1 框架层（dsh runtime 目录）----
# 容器化 backend（堡垒机）没有 docker CLI，走 DockerEngineClient，Harness 容器由 compose 预建，
# runtime 挂载由 compose.yaml 负责，本节只在「宿主机有 docker CLI」的开发分支生效。
DSH_RUNTIME_MOUNT = "/opt/dsh-runtime"
DSH_RUNTIME_HOST = os.getenv("DWP_DSH_RUNTIME_HOST", "").strip()
DEFAULT_RUNTIME_PATH = REPO_ROOT / "deploy" / "offline" / "runtime" / "current"
# 保持与 compose.yaml x-harness-common.environment.PATH 一致
CONTAINER_PATH = (
    f"{DSH_RUNTIME_MOUNT}/bin:"
    "/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
)


def container_name(employee_id: str) -> str:
    safe = re.sub(r"[^a-z0-9-]+", "-", employee_id.lower()).strip("-")
    return f"dwp-harness-{safe}"


def workspace_ref(employee_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", employee_id)
    return str(REPO_ROOT / "backend" / "harness-workspaces" / safe)


# ---- 技能包投放 ----
# 容器的 workspace 是 bind mount（compose: ./harness/<EMP>/workspace:/workspace），
# backend 侧同一目录挂在 /app/backend/harness-workspaces/<EMP>/workspace，因此「投放」
# 就是往这个目录写文件：不需要 docker exec、不需要容器在跑，容器重建也不会丢。
SKILLS_DIRNAME = "skills"
_DEPLOY_MANIFEST = ".dwp-deployed-skills.json"
# 旁挂的「最近一次投放结果」：不改既有幂等清单的格式（slug→sha），
# 失败原因与时间单独记一份，供运行能力核验页与AI员工档案页展示。
_DEPLOY_STATE = ".dwp-deploy-state.json"

# 投放态取值：deployed / stale / missing / failed / removed。
# 中文文案只在 `frontend/src/features/plugins/pluginMeta.ts` 定义一处，后端不重复维护。


def skills_root(employee_id: str) -> Path:
    """容器内 ``/workspace/skills`` 对应的宿主/backend 侧目录。"""
    return Path(workspace_ref(employee_id)) / "workspace" / SKILLS_DIRNAME


def _load_deploy_manifest(root: Path) -> dict[str, str]:
    path = root / _DEPLOY_MANIFEST
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    if not isinstance(raw, dict):
        return {}
    return {str(key): str(value) for key, value in raw.items()}


def _write_deploy_manifest(root: Path, manifest: dict[str, str]) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / _DEPLOY_MANIFEST).write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("技能投放清单写入失败 %s: %s", root, exc)


def _load_deploy_state(root: Path) -> dict:
    path = root / _DEPLOY_STATE
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _write_deploy_state(root: Path, payload: dict) -> None:
    try:
        root.mkdir(parents=True, exist_ok=True)
        (root / _DEPLOY_STATE).write_text(
            json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    except OSError as exc:
        logger.warning("技能投放状态写入失败 %s: %s", root, exc)


def _record_deploy_state(
    root: Path, artifacts: list[dict], result: dict[str, Any], errors: dict[str, str],
) -> None:
    """把最近一次投放结果旁挂落盘（失败原因与时间只在这里，不进幂等清单）。"""
    now = datetime.now().isoformat()
    failed = set(result.get("failed") or [])
    deployed = set(result.get("deployed") or [])
    skills: dict[str, dict] = {}
    for artifact in artifacts:
        slug = str(artifact.get("slug") or "")
        if not slug:
            continue
        if slug in failed:
            skills[slug] = {
                "state": "failed",
                "error": errors.get(slug, "投放失败"),
                "sha256": artifact.get("sha256") or "",
                "at": now,
            }
        else:
            skills[slug] = {
                "state": "deployed" if slug in deployed else "kept",
                "sha256": artifact.get("sha256") or "",
                "at": now,
            }
    _write_deploy_state(root, {
        "checked_at": now,
        "skills": skills,
        "removed": list(result.get("removed") or []),
        "errors": errors,
    })


def deploy_skills(db: Session, employee_id: str) -> dict[str, Any]:
    """把该AI员工已授权生效的技能包幂等投放进 ``/workspace/skills/<slug>/``。

    幂等：源目录的 ``.dwp-source-sha256`` 与目标已投放的 sha 一致时跳过；不再授权的
    技能目录会被清掉（只清本函数登记过的，绝不误删容器里的其他文件）。
    全程不抛异常：投放失败只记日志并计入 ``failed``，不阻断对话主流程。
    """
    from .plugin_governance import effective_skill_artifacts

    root = skills_root(employee_id)
    previous = _load_deploy_manifest(root)
    result: dict[str, Any] = {"deployed": [], "skipped": [], "removed": [], "failed": []}
    errors: dict[str, str] = {}
    try:
        artifacts = effective_skill_artifacts(db, employee_id)
    except Exception as exc:  # noqa: BLE001 - 投放是尽力而为，不能拖垮对话
        logger.warning("技能包投放：解析已授权技能失败 employee=%s: %s", employee_id, exc)
        result["failed"].append("*")
        errors["*"] = f"解析已授权技能失败：{exc}"
        _record_deploy_state(root, [], result, errors)
        return result

    manifest: dict[str, str] = {}
    for artifact in artifacts:
        slug, source = artifact["slug"], Path(artifact["source"])
        sha = artifact["sha256"] or ""
        manifest[slug] = sha
        target = root / slug
        marker = target / ".dwp-source-sha256"
        if previous.get(slug) == sha and marker.is_file():
            result["skipped"].append(slug)
            continue
        try:
            if target.exists():
                shutil.rmtree(target)
            shutil.copytree(source, target)
            result["deployed"].append(slug)
        except OSError as exc:
            logger.warning("技能包投放失败 employee=%s slug=%s: %s", employee_id, slug, exc)
            manifest.pop(slug, None)
            result["failed"].append(slug)
            errors[slug] = f"写入工作区失败：{exc}"

    for slug in set(previous) - set(manifest):
        try:
            shutil.rmtree(root / slug)
            result["removed"].append(slug)
        except OSError as exc:
            logger.warning("技能包回收失败 employee=%s slug=%s: %s", employee_id, slug, exc)
            errors[slug] = f"回收失败：{exc}"

    _write_deploy_manifest(root, manifest)
    _record_deploy_state(root, artifacts, result, errors)
    if result["deployed"] or result["removed"]:
        logger.info(
            "技能包投放 employee=%s deployed=%s removed=%s failed=%s",
            employee_id, result["deployed"], result["removed"], result["failed"],
        )
    return result


def deploy_state(db: Session, employee_id: str) -> dict[str, dict[str, Any]]:
    """技能投放态，**按 slug 返回** ``{state, detail, deployed_at, sha256}``。

    真源是文件系统：``effective_skill_artifacts`` 给出的 sha256 与目标目录
    ``.dwp-source-sha256`` / 幂等清单逐条比对；失败原因与时间取自旁挂的
    ``.dwp-deploy-state.json``（最近一次投放运行）。不额外查库，也不加数据库列。

    以 slug 为键（而不是 plugin_id）是因为文件系统本来就是 slug 目录；
    调用方持有已发布版本行，用 ``plugin_governance.skill_artifact_slug`` 即可换算，
    这样也避免为「已回收」的历史技能逐条回查全部技能插件。
    """
    from .plugin_governance import effective_skill_artifacts

    root = skills_root(employee_id)
    previous = _load_deploy_manifest(root)
    sidecar = _load_deploy_state(root)
    sidecar_skills = sidecar.get("skills") if isinstance(sidecar.get("skills"), dict) else {}
    errors = sidecar.get("errors") if isinstance(sidecar.get("errors"), dict) else {}
    try:
        artifacts = effective_skill_artifacts(db, employee_id)
    except Exception as exc:  # noqa: BLE001 - 状态展示是只读旁路，失败按未知处理
        logger.warning("技能投放态：解析已授权技能失败 employee=%s: %s", employee_id, exc)
        return {}

    result: dict[str, dict[str, Any]] = {}
    for artifact in artifacts:
        slug = str(artifact.get("slug") or "")
        sha = str(artifact.get("sha256") or "")
        if not slug:
            continue
        target = root / slug
        marker = target / ".dwp-source-sha256"
        recorded = sidecar_skills.get(slug) if isinstance(sidecar_skills.get(slug), dict) else {}
        marker_sha = ""
        if marker.is_file():
            try:
                marker_sha = marker.read_text(encoding="utf-8").strip()
            except OSError:
                marker_sha = ""
        deployed_at = str(recorded.get("at") or "") or None
        if not target.is_dir():
            if recorded.get("state") == "failed" or slug in errors:
                state = "failed"
                detail = str(errors.get(slug) or recorded.get("error") or "投放失败")
            else:
                state = "missing"
                detail = "尚未投放到该AI员工的工作区"
        elif recorded.get("state") == "failed":
            state = "failed"
            detail = str(recorded.get("error") or errors.get(slug) or "投放失败")
        elif previous.get(slug) != sha or marker_sha != sha:
            state = "stale"
            detail = "授权版本已更新，等待下一次投放"
        else:
            state = "deployed"
            detail = "已投放到工作区"
        if deployed_at is None:
            try:
                deployed_at = datetime.fromtimestamp(target.stat().st_mtime).isoformat()
            except OSError:
                deployed_at = None
        result[slug] = {"state": state, "detail": detail, "deployed_at": deployed_at, "sha256": sha}

    # 已回收：上一次投放存在过、本次已不在授权范围，且目录确实已被清掉。
    for slug in sidecar.get("removed") or []:
        if slug in result or (root / str(slug)).exists():
            continue
        result[str(slug)] = {
            "state": "removed",
            "detail": "授权已撤回，工作区内的技能包已回收",
            "deployed_at": str(sidecar.get("checked_at") or "") or None,
            "sha256": "",
        }
    return result


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


def _has_docker_cli() -> bool:
    return shutil.which("docker") is not None


def exec_in_container(
    name: str,
    command: list[str],
    *,
    environment: dict[str, str] | None = None,
    workdir: str | None = None,
    timeout: int = DOCKER_TIMEOUT,
) -> subprocess.CompletedProcess[str] | EngineResult:
    """Execute through the host CLI or the mounted Engine socket."""
    if _has_docker_cli():
        env_path = None
        try:
            args = ["exec"]
            if workdir:
                args.extend(["--workdir", workdir])
            if environment:
                with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as env_file:
                    for key, value in environment.items():
                        env_file.write(f"{key}={value.replace(chr(10), '').replace(chr(13), '')}\n")
                    env_path = env_file.name
                args.extend(["--env-file", env_path])
            return _docker(*args, name, *command, timeout=timeout)
        finally:
            if env_path:
                try:
                    os.remove(env_path)
                except OSError:
                    pass
    try:
        engine = DockerEngineClient(timeout=timeout)
        try:
            return engine.exec(name, command, environment=environment, workdir=workdir)
        finally:
            engine.close()
    except (OSError, httpx.HTTPError) as exc:
        raise RuntimeError(f"Docker Engine 调用失败：{exc.__class__.__name__}") from exc


def docker_available() -> bool:
    if _has_docker_cli():
        try:
            return _docker("version", "--format", "{{.Server.Version}}", timeout=5).returncode == 0
        except RuntimeError:
            return False
    try:
        engine = DockerEngineClient(timeout=5)
        try:
            return bool(engine.version())
        finally:
            engine.close()
    except (OSError, httpx.HTTPError):
        return False


def _container_status(name: str) -> str | None:
    if _has_docker_cli():
        proc = _docker("inspect", "--format", "{{.State.Status}}", name)
        return proc.stdout.strip() if proc.returncode == 0 else None
    engine = DockerEngineClient(timeout=DOCKER_TIMEOUT)
    try:
        return engine.container_status(name)
    finally:
        engine.close()


def resolve_runtime_dir() -> Path | None:
    """返回宿主机上的 dsh runtime 目录（L1 框架层）。

    仅在「宿主机有 docker CLI」的开发分支使用；容器化 backend 的 runtime 挂载由
    compose.yaml 负责。目录不存在时返回 None，表示回落底座镜像内置的 dsh（灾备路径）。
    """
    candidate = DSH_RUNTIME_HOST or str(DEFAULT_RUNTIME_PATH)
    path = Path(candidate)
    try:
        if (path / "bin" / "dsh").exists():
            return path.resolve()
    except OSError:
        return None
    return None


def _assert_image() -> None:
    if _has_docker_cli():
        exists = _docker("image", "inspect", HARNESS_IMAGE).returncode == 0
    else:
        engine = DockerEngineClient(timeout=DOCKER_TIMEOUT)
        try:
            exists = engine.image_exists(HARNESS_IMAGE)
        finally:
            engine.close()
    if not exists:
        raise RuntimeError(f"Harness 镜像不存在：{HARNESS_IMAGE}")


# ---- 容器漂移自愈：容器被误删时按编排口径自建 ----
# compose.yaml 里 harness 服务的四要素（镜像 / 4 条 bind / 标签 / 网络）必须逐字一致，
# 否则 runtime_registry 认不出、runtime_manager 会重复建容器。
_HARNESS_WORKSPACES_DEST = "/app/backend/harness-workspaces"
DWP_PROFILE_MOUNT = "/opt/dwp/profile"
MANAGED_LABEL = "dwp.managed=true"
# 探测结果进程内短缓存：与 runtime_registry.PROBE_TIMEOUT 同口径，避免每次启动都 inspect。
HOST_LAYOUT_TTL = 3.0
# compose 的默认网络名（项目名 dwp-ai-employee-platform + 网络 dwp）；只在探测不到时兜底。
DEFAULT_HARNESS_NETWORK = "dwp-ai-employee-platform_dwp"

# 与 compose.yaml x-harness-common.command 完全一致：先守卫挂载，再同步 profile，最后常驻。
# 守卫是必要的：Docker 对不存在的 bind 源会静默建空目录，不守卫就会「起来了但 dsh 不在」。
HARNESS_ENTRYPOINT_SCRIPT = (
    "if [ ! -x /opt/dsh-runtime/bin/dsh ]; then "
    'echo "FATAL: 未找到 /opt/dsh-runtime/bin/dsh；DWP_DSH_RUNTIME 挂载缺失或目录结构不对" >&2; '
    "exit 1; fi; "
    "if [ ! -f /opt/dwp/profile/cordis.yml ]; then "
    'echo "FATAL: 未找到 /opt/dwp/profile/cordis.yml；DWP_PROFILE 挂载缺失" >&2; exit 1; fi; '
    "mkdir -p /dsh-home/profiles/dwp-knowledge-agent-v2 /workspace && "
    "cp /opt/dwp/profile/cordis.yml /opt/dwp/profile/cordis.patch.yml "
    "/opt/dwp/profile/package.json /dsh-home/profiles/dwp-knowledge-agent-v2/ && "
    "trap 'exit 0' TERM INT; while :; do sleep 3600 & wait $!; done"
)


@dataclass(frozen=True)
class HostLayout:
    """自建容器所需的宿主侧信息（backend 容器里看不到这些路径）。"""

    harness_root: str
    runtime_host: str
    profile_host: str
    network_mode: str
    security_opt: tuple[str, ...]
    runtime_label: str
    profile_label: str


_host_layout_cache: tuple[float, HostLayout] | None = None


def _mount_map(payload: dict) -> dict[str, str]:
    """容器 Mounts 的 ``Destination -> Source`` 映射（Destination 去掉尾斜杠）。"""
    result: dict[str, str] = {}
    for item in payload.get("Mounts") or []:
        if not isinstance(item, dict):
            continue
        destination = str(item.get("Destination") or "").rstrip("/")
        source = str(item.get("Source") or "")
        if destination and source:
            result[destination] = source
    return result


def _probe_host_layout() -> HostLayout:
    """解析宿主机上的 bind 源路径：先 inspect 自身/兄弟容器，再读环境变量。

    backend 容器里看到的 ``/app/backend/harness-workspaces`` 不是宿主路径，而
    ``POST /containers/create`` 只认宿主绝对路径。解析顺序：

    1. inspect **自身**容器的 Mounts：``/app/backend/harness-workspaces`` 那条的 Source
       即宿主的 ``./harness`` 根；顺带拿到 backend 所在网络；
    2. inspect 任一 ``dwp.managed=true`` **兄弟**容器：取 ``/opt/dsh-runtime`` 与
       ``/opt/dwp/profile`` 的 Source，以及它的网络与 SecurityOpt；
    3. 仍缺则读 ``DWP_HARNESS_HOST_ROOT`` / ``DWP_DSH_RUNTIME_HOST`` /
       ``DWP_PROFILE_HOST`` / ``DWP_HARNESS_NETWORK``（冷启动兜底）；
    4. 再缺就抛可读错误并指明该配哪个变量 —— **不做猜测**，因为 Docker 对不存在的
       bind 源会静默建空目录，猜错比报错更难查。
    """
    harness_root = ""
    network_mode = ""
    runtime_host = ""
    profile_host = ""
    security_opt: tuple[str, ...] = ()
    runtime_label = ""
    profile_label = ""
    try:
        engine = DockerEngineClient(timeout=DOCKER_TIMEOUT)
        try:
            self_id = self_container_id()
            if self_id:
                payload = engine.inspect_container(self_id)
                if payload:
                    harness_root = _mount_map(payload).get(_HARNESS_WORKSPACES_DEST, "")
                    network_mode = str((payload.get("HostConfig") or {}).get("NetworkMode") or "")
            for item in engine.list_containers(MANAGED_LABEL):
                sibling = str((item.get("Names") or [""])[0]).lstrip("/") or str(item.get("Id") or "")
                payload = engine.inspect_container(sibling) if sibling else None
                if not payload:
                    continue
                mounts = _mount_map(payload)
                host_config = payload.get("HostConfig") or {}
                if not harness_root:
                    home = mounts.get("/dsh-home", "")
                    if home:
                        harness_root = str(Path(home).parent.parent)
                runtime_host = runtime_host or mounts.get(DSH_RUNTIME_MOUNT, "")
                profile_host = profile_host or mounts.get(DWP_PROFILE_MOUNT, "")
                if not network_mode:
                    network_mode = str(host_config.get("NetworkMode") or "")
                if not security_opt:
                    security_opt = tuple(str(value) for value in (host_config.get("SecurityOpt") or []))
                labels = payload.get("Config", {}).get("Labels") or {}
                runtime_label = runtime_label or str(labels.get("dwp.dsh.runtime") or "")
                profile_label = profile_label or str(labels.get("dwp.dsh.profile") or "")
                if harness_root and runtime_host and profile_host:
                    break
        finally:
            engine.close()
    except (OSError, httpx.HTTPError) as exc:
        logger.warning("宿主机挂载路径探测失败，改用环境变量兜底：%s", exc)

    harness_root = harness_root or os.getenv("DWP_HARNESS_HOST_ROOT", "").strip()
    runtime_host = runtime_host or DSH_RUNTIME_HOST
    profile_host = profile_host or os.getenv("DWP_PROFILE_HOST", "").strip()
    network_mode = network_mode or os.getenv("DWP_HARNESS_NETWORK", "").strip() or DEFAULT_HARNESS_NETWORK
    missing = [
        name for name, value in (
            ("DWP_HARNESS_HOST_ROOT（宿主上的 deploy/offline/harness）", harness_root),
            ("DWP_DSH_RUNTIME_HOST（宿主上的 deploy/offline/runtime/current）", runtime_host),
            ("DWP_PROFILE_HOST（宿主上的 deploy/offline/profile/current）", profile_host),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            "无法自动探测宿主机上的 Harness 挂载路径，请在 deploy/offline/config.env 里配置："
            + "；".join(missing)
        )
    return HostLayout(
        harness_root=harness_root,
        runtime_host=runtime_host,
        profile_host=profile_host,
        network_mode=network_mode,
        security_opt=security_opt or ("no-new-privileges:true",),
        runtime_label=runtime_label or runtime_host,
        profile_label=profile_label or (os.getenv("DWP_PROFILE_REV", "").strip() or "unknown"),
    )


def host_layout() -> HostLayout:
    """带短 TTL 缓存的宿主路径解析（启动一批AI员工时只探测一次）。"""
    global _host_layout_cache
    now = time.monotonic()
    if _host_layout_cache is not None and now - _host_layout_cache[0] < HOST_LAYOUT_TTL:
        return _host_layout_cache[1]
    layout = _probe_host_layout()
    _host_layout_cache = (now, layout)
    return layout


def _container_spec(employee_id: str, layout: HostLayout) -> dict[str, Any]:
    """按 compose.yaml 的 harness 服务口径构造 create 请求体（四要素逐字一致）。"""
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "_", employee_id)
    harness_root = Path(layout.harness_root)
    host_home = harness_root / safe / "dsh-home"
    host_workspace = harness_root / safe / "workspace"
    return {
        "Image": HARNESS_IMAGE,
        "Entrypoint": ["sh", "-c"],
        "Cmd": [HARNESS_ENTRYPOINT_SCRIPT],
        "WorkingDir": "/workspace",
        "Env": [
            "DSH_HOME=/dsh-home",
            "DSH_PERMISSION_MODE="
            + (os.getenv("DWP_DSH_PERMISSION_MODE", "").strip() or "workspace-write"),
            f"PATH={CONTAINER_PATH}",
            "DSH_TELEMETRY_MODE=" + (os.getenv("DWP_DSH_TELEMETRY_MODE", "").strip() or "DISABLED"),
        ],
        "Labels": {
            "dwp.managed": "true",
            "dwp.employee_id": employee_id,
            "dwp.dsh.runtime": layout.runtime_label,
            "dwp.dsh.profile": layout.profile_label,
        },
        "HostConfig": {
            # 顺序与 compose 一致；Docker 对不存在的 bind 源会静默建空目录，
            # 因此这里只用探测/显式变量得到的宿主路径（entrypoint 守卫再兜一道）。
            "Binds": [
                f"{layout.runtime_host}:{DSH_RUNTIME_MOUNT}:ro",
                f"{layout.profile_host}:{DWP_PROFILE_MOUNT}:ro",
                f"{host_home}:/dsh-home",
                f"{host_workspace}:/workspace",
            ],
            "NetworkMode": layout.network_mode,
            "RestartPolicy": {"Name": "unless-stopped"},
            "SecurityOpt": list(layout.security_opt),
        },
    }


def _create_container_via_engine(employee_id: str, name: str) -> None:
    """无 docker CLI 时经 Docker socket 自建容器（create + start）。

    自建后照常走既有的 profile 同步与健康检查；任一步失败都抛可读错误，
    不静默降级（否则会出现「接口说启动成功、实际没有可用容器」）。
    """
    layout = host_layout()
    engine = DockerEngineClient(timeout=30)
    try:
        created = engine.create_container(name, _container_spec(employee_id, layout))
        if created.returncode != 0:
            raise RuntimeError(f"创建 Harness 容器失败：{created.stderr.strip()[:300]}")
        started = engine.start_container(name)
        if started.returncode != 0:
            raise RuntimeError(f"启动 Harness 容器失败：{started.stderr.strip()[:300]}")
    finally:
        engine.close()
    logger.warning(
        "Harness 容器不存在，已按编排口径自建 employee=%s container=%s network=%s",
        employee_id, name, layout.network_mode,
    )


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
        if not _has_docker_cli():
            # 容器化 backend 没有 docker CLI：容器被误删时经 Docker socket 按编排口径自建。
            _create_container_via_engine(employee_id, name)
        else:
            api_key = config.get("DEEPSEEK_API_KEY")
            if not api_key:
                raise RuntimeError("DEEPSEEK_API_KEY 未配置")
            model = config.get("DEEPSEEK_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash"
            runtime_dir = resolve_runtime_dir()
            env_path = None
            try:
                with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as env_file:
                    env_file.write(
                        f"DEEPSEEK_API_KEY={api_key}\nDEEPSEEK_MODEL={model}\n"
                        # 与 compose 一致：技能脚本要能真跑，沙箱必须是可写工作区。
                        "DSH_PERMISSION_MODE=workspace-write\nDSH_HOME=/dsh-home\n"
                        f"PATH={CONTAINER_PATH}\n"
                    )
                    env_path = env_file.name
                runtime_mounts = (
                    ["--mount", f"type=bind,source={runtime_dir},target={DSH_RUNTIME_MOUNT},readonly"]
                    if runtime_dir is not None else []
                )
                proc = _docker(
                    "run", "-d", "--name", name, "--restart", "unless-stopped",
                    "--label", "dwp.managed=true", "--label", f"dwp.employee_id={employee_id}",
                    "--label", f"dwp.dsh.runtime={runtime_dir or 'image-baked'}",
                    "--env-file", env_path,
                    "--mount", f"type=bind,source={dsh_home.resolve()},target=/dsh-home",
                    "--mount", f"type=bind,source={workspace.resolve()},target=/workspace",
                    *runtime_mounts,
                    "--workdir", "/workspace", "--entrypoint", "sh", HARNESS_IMAGE,
                    "-c", HARNESS_ENTRYPOINT_SCRIPT,
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
        if _has_docker_cli():
            proc = _docker("start", name, timeout=30)
        else:
            engine = DockerEngineClient(timeout=30)
            try:
                proc = engine.start_container(name)
            finally:
                engine.close()
        if proc.returncode != 0:
            raise RuntimeError(f"启动 Harness 容器失败：{(proc.stderr or proc.stdout).strip()[:300]}")

    profile_sync = exec_in_container(
        name,
        ["sh", "-c", "mkdir -p /dsh-home/profiles/dwp-knowledge-agent-v2 && "
         "cp /opt/dwp/profile/cordis.yml /opt/dwp/profile/cordis.patch.yml "
         "/opt/dwp/profile/package.json /dsh-home/profiles/dwp-knowledge-agent-v2/"],
    )
    if profile_sync.returncode != 0:
        raise RuntimeError(f"Harness 安全 Profile 同步失败：{name}")

    runtime_probe = exec_in_container(
        name, ["sh", "-c", f"test -x {DSH_RUNTIME_MOUNT}/bin/dsh"],
    )
    if runtime_probe.returncode != 0:
        raise RuntimeError(
            f"Harness 容器未挂载 dsh runtime（{DSH_RUNTIME_MOUNT}/bin/dsh 缺失）：{name}；"
            "请检查 deploy/offline/versions.env 的 DWP_DSH_RUNTIME 与 runtime 目录"
        )

    probe = exec_in_container(name, ["sh", "-c", "command -v dsh >/dev/null 2>&1"])
    if probe.returncode != 0:
        raise RuntimeError(f"Harness 容器健康检查失败：{name}")
    return name


def sync_skills(db: Session, employee_id: str) -> dict[str, Any]:
    """容器就绪后的技能包补投（幂等）。

    容器重建、员工重新启用、审批授权变化后都走这里，保证
    ``/workspace/skills/<slug>/`` 与「当前已授权技能」一致；失败只记日志。
    """
    try:
        return deploy_skills(db, employee_id)
    except Exception as exc:  # noqa: BLE001 - 补投失败不影响容器可用性
        logger.warning("技能包补投失败 employee=%s: %s", employee_id, exc)
        return {"deployed": [], "skipped": [], "removed": [], "failed": [employee_id]}


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
    # 容器就绪后补投技能包：容器重建/换镜像都不会丢已授权技能。
    sync_skills(db, employee_id)
    row.last_active_at = datetime.now()
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def stop(db: Session, employee_id: str) -> models.AgentRuntime:
    row = ensure_runtime(db, employee_id)
    if row.state == "busy":
        raise ValueError("AI员工正在执行任务，不能直接停止")
    status = _container_status(row.container_name) if docker_available() else None
    if status == "running":
        if _has_docker_cli():
            proc = _docker("stop", "--time", "5", row.container_name, timeout=10)
        else:
            engine = DockerEngineClient(timeout=10)
            try:
                proc = engine.stop_container(row.container_name, seconds=5)
            finally:
                engine.close()
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


# node:22-slim 底座里没有 procps（pkill / pgrep / ps 全部缺失），因此不能用 pkill 终止任务。
# 这里改为遍历 /proc 读 cmdline 后 kill，纯 POSIX shell、零外部依赖。
#
# 两个匹配标记都在运行时用字符串拼接构造（'d'+'sh'），这样这段 shell 自身的 cmdline
# 里不会出现完整标记，避免「自己杀自己」：
#   - "dsh --profile"       覆盖 runtime 挂载与镜像内置两种 dsh 入口
#   - "/opt/dsh-runtime"    兜底覆盖任何从框架层挂载点启动的进程
_TERMINATE_SCRIPT = (
    "M1='d''sh'; M1=\"$M1 --profile\"; "
    "M2='/opt/d''sh-runtime'; "
    "for d in /proc/[0-9]*; do "
    "pid=${d#/proc/}; "
    "[ \"$pid\" -gt 1 ] 2>/dev/null || continue; "
    "c=$(tr '\\000' ' ' < \"$d/cmdline\" 2>/dev/null) || continue; "
    "case \"$c\" in *\"$M1\"*|*\"$M2\"*) kill -9 \"$pid\" 2>/dev/null || true;; esac; "
    "done"
)


def terminate_task(employee_id: str) -> None:
    """终止员工容器内正在运行的 dsh 进程（任务取消/超时回收时调用）。

    只杀 dsh 进程、不停止容器，避免影响后续任务的容器复用。
    容器或 Docker Engine 不可达时静默降级，不打断取消流程。
    """
    name = container_name(employee_id)
    try:
        exec_in_container(name, ["sh", "-c", _TERMINATE_SCRIPT], timeout=15)
    except RuntimeError:
        return


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
