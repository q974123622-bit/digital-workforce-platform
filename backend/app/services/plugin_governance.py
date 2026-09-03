"""Unified Skill/MCP lifecycle, artifact validation and effective-plugin resolution."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

import yaml
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models

MAX_ZIP = 20 * 1024 * 1024
MAX_FILES = 200
MAX_UNPACKED = 100 * 1024 * 1024
MCP_CATEGORIES = {"knowledge", "cloud_information", "fund", "internal_system", "other"}
FORBIDDEN_NAMES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
FORBIDDEN_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh"}
SECRET_KEYS = {"authorization", "token", "api_key", "apikey", "password", "secret"}


def artifact_root() -> Path:
    configured = os.getenv("DWP_ARTIFACT_ROOT")
    root = Path(configured) if configured else Path(__file__).resolve().parents[2] / "artifacts"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _contains_secret(value: object, key: str = "") -> bool:
    if key.lower() in SECRET_KEYS and value not in (None, "", "${SECRET_REF}"):
        return True
    if isinstance(value, dict):
        return any(_contains_secret(v, str(k)) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_secret(v) for v in value)
    return False


def _contains_key(value: object, forbidden: set[str]) -> bool:
    if isinstance(value, dict):
        return any(str(k).lower() in forbidden or _contains_key(v, forbidden) for k, v in value.items())
    if isinstance(value, list):
        return any(_contains_key(v, forbidden) for v in value)
    return False


def validate_zip(data: bytes, plugin_type: str, deployment_mode: str) -> tuple[dict, str]:
    if len(data) > MAX_ZIP:
        raise HTTPException(status_code=422, detail="ZIP 不能超过 20 MiB")
    digest = hashlib.sha256(data).hexdigest()
    try:
        from io import BytesIO
        archive = zipfile.ZipFile(BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise HTTPException(status_code=422, detail="上传文件不是有效 ZIP") from exc
    infos = archive.infolist()
    if len(infos) > MAX_FILES or sum(row.file_size for row in infos) > MAX_UNPACKED:
        raise HTTPException(status_code=422, detail="ZIP 文件数量或解压后大小超限")
    names: set[str] = set()
    for row in infos:
        path = PurePosixPath(row.filename.replace("\\", "/"))
        if path.is_absolute() or ".." in path.parts or not path.parts:
            raise HTTPException(status_code=422, detail="ZIP 包含不安全路径")
        if stat.S_ISLNK(row.external_attr >> 16):
            raise HTTPException(status_code=422, detail="ZIP 不允许符号链接")
        lower = path.name.lower()
        if lower in FORBIDDEN_NAMES or path.suffix.lower() in FORBIDDEN_SUFFIXES:
            raise HTTPException(status_code=422, detail=f"不允许的文件：{path.name}")
        if plugin_type == "skill" and path.suffix.lower() in {".py", ".js", ".mjs", ".cjs"}:
            raise HTTPException(status_code=422, detail="Skill 只能包含说明和静态资源")
        names.add(path.as_posix())
    required = {"plugin.yaml", "SKILL.md"} if plugin_type == "skill" else {"plugin.yaml"}
    if not required.issubset(names):
        raise HTTPException(status_code=422, detail=f"ZIP 缺少：{', '.join(sorted(required - names))}")
    try:
        manifest = yaml.safe_load(archive.read("plugin.yaml").decode("utf-8")) or {}
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(status_code=422, detail="plugin.yaml 无法解析") from exc
    if not isinstance(manifest, dict) or _contains_secret(manifest):
        raise HTTPException(status_code=422, detail="Manifest 不合法或包含真实凭据")
    if plugin_type == "skill":
        if _contains_key(manifest, {"endpoint", "endpoint_ref", "url", "network", "secret_ref"}):
            raise HTTPException(status_code=422, detail="Skill 不允许声明网络、Endpoint 或 Secret")
        try:
            manifest["instruction"] = archive.read("SKILL.md").decode("utf-8")[:8000]
        except UnicodeDecodeError as exc:
            raise HTTPException(status_code=422, detail="SKILL.md 必须是 UTF-8 文本") from exc
    if plugin_type == "mcp" and not manifest.get("tools"):
        raise HTTPException(status_code=422, detail="MCP Manifest 必须声明工具 Schema")
    if deployment_mode == "hosted" and manifest.get("install_command"):
        raise HTTPException(status_code=422, detail="Hosted MCP 不允许任意依赖安装命令")
    return manifest, digest


def next_plugin_id() -> str:
    return f"PLG-{uuid4().hex[:12].upper()}"


def submit_zip(
    db: Session, *, data: bytes, filename: str, name: str, plugin_type: str, scope: str,
    category: str | None, deployment_mode: str, data_level: str, version: str,
    submitter: str, target_agent_id: str | None,
) -> models.PluginVersion:
    if plugin_type not in {"skill", "mcp"} or scope not in {"personal", "shared"}:
        raise HTTPException(status_code=422, detail="插件类型或范围不合法")
    if plugin_type == "mcp" and (category or "other") not in MCP_CATEGORIES:
        raise HTTPException(status_code=422, detail="MCP 分类不合法")
    if deployment_mode not in ({"instruction"} if plugin_type == "skill" else {"external", "hosted"}):
        raise HTTPException(status_code=422, detail="部署模式不适用于该插件类型")
    manifest, digest = validate_zip(data, plugin_type, deployment_mode)
    plugin_id = next_plugin_id()
    submission_id = f"SUB-{uuid4().hex[:12].upper()}"
    relative = Path("staging") / submission_id / (Path(filename).name or "plugin.zip")
    target = artifact_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    auto_publish = (
        plugin_type == "skill" and scope == "personal" and data_level == "L1"
        and target_agent_id == f"DT-{submitter}"
    )
    plugin = models.Plugin(
        id=plugin_id, name=name.strip(), type="skill" if plugin_type == "skill" else "mcp",
        plugin_type=plugin_type, scope=scope, owner_human_no=submitter if scope == "personal" else None,
        mcp_category=category if plugin_type == "mcp" else None, current_version=version if auto_publish else None,
        endpoint_ref="mock://", data_level=data_level, status="active" if auto_publish else "draft",
        description=str(manifest.get("description", "")), runtime_meta={},
    )
    row = models.PluginVersion(
        plugin_id=plugin_id, version=version, deployment_mode=deployment_mode,
        artifact_path=relative.as_posix(), sha256=digest, manifest=manifest, data_level=data_level,
        review_status="approved" if auto_publish else "pending",
        publish_status="published" if auto_publish else "submitted", submitted_by=submitter,
        reviewed_by="automatic-policy" if auto_publish else None,
        reviewed_at=datetime.now() if auto_publish else None,
        published_at=datetime.now() if auto_publish else None,
    )
    db.add(plugin)
    db.add(row)
    db.flush()
    if auto_publish and target_agent_id:
        published = artifact_root() / "published" / plugin_id / version / target.name
        published.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target, published)
        row.artifact_path = published.relative_to(artifact_root()).as_posix()
        db.add(models.AgentPluginBinding(
            plugin_id=plugin_id, target_agent_id=target_agent_id, authorized_by="automatic-policy",
            employee_enabled=True, admin_enabled=True, decision_mode="allow",
        ))
    db.commit()
    db.refresh(row)
    return row


def effective_plugins(db: Session, agent_id: str, requester_human_no: str) -> list[dict]:
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == agent_id,
        models.AgentPluginBinding.admin_enabled.is_(True),
        models.AgentPluginBinding.employee_enabled.is_(True),
        models.AgentPluginBinding.decision_mode == "allow",
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    result: list[dict] = []
    for binding in bindings:
        plugin = db.get(models.Plugin, binding.plugin_id)
        if not plugin or plugin.status != "active" or not plugin.current_version:
            continue
        if plugin.scope == "personal" and plugin.owner_human_no != requester_human_no:
            continue
        version_no = binding.pinned_version or plugin.current_version
        version = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == version_no,
            models.PluginVersion.publish_status == "published",
        ))
        if not version:
            continue
        item = {"plugin_id": plugin.id, "version": version.version, "plugin_type": plugin.plugin_type, "name": plugin.name}
        if plugin.plugin_type == "skill":
            item["instruction_summary"] = str(version.manifest.get("instruction_summary") or plugin.description)[:500]
            item["instruction"] = str(version.manifest.get("instruction") or version.manifest.get("instruction_summary") or plugin.description)[:6000]
        else:
            item.update({"category": plugin.mcp_category, "tools": version.manifest.get("tools", [])})
        result.append(item)
    return result


def publish_version(db: Session, version: models.PluginVersion, reviewer: str) -> None:
    if version.review_status != "approved":
        raise HTTPException(status_code=409, detail="插件版本尚未审核通过")
    plugin = db.get(models.Plugin, version.plugin_id)
    if not plugin:
        raise HTTPException(status_code=404, detail="插件不存在")
    old = db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.publish_status == "published",
    )).all()
    source = artifact_root() / version.artifact_path
    destination = artifact_root() / "published" / plugin.id / version.version / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, destination)
    for row in old:
        row.publish_status = "superseded"
    version.artifact_path = destination.relative_to(artifact_root()).as_posix()
    version.publish_status = "published"
    version.published_at = datetime.now()
    plugin.current_version = version.version
    plugin.status = "active"
    if plugin.plugin_type == "mcp":
        runtime_mode = os.getenv("DWP_MCP_RUNTIME_MODE", "mock")
        build = models.PluginBuildJob(
            id=f"PB-{uuid4().hex[:14].upper()}", plugin_version_id=version.id,
            status="completed" if runtime_mode == "mock" else "pending",
            runtime=runtime_mode,
        )
        db.add(build)
        db.add(models.McpRuntimeInstance(
            plugin_version_id=version.id,
            container_name="" if version.deployment_mode == "external" else f"dwp-mcp-{plugin.id.lower()}-{version.version.replace('.', '-')}",
            state="mock" if runtime_mode == "mock" else "pending",
            health="healthy" if runtime_mode == "mock" else "unknown",
        ))
    db.commit()
