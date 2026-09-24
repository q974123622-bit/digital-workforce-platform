"""Unified Skill/MCP lifecycle, artifact validation and effective-plugin resolution."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
import stat
import zipfile
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Literal
from uuid import uuid4

import yaml
from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from .. import models
from .prompt_shared import CONTAINER_SKILLS_DIR

logger = logging.getLogger(__name__)

MAX_ZIP = 20 * 1024 * 1024
# SkillHub 技能包实测 59 条目，脚本型技能条目更多；500 仍远低于「压缩炸弹」的量级。
MAX_FILES = 500
MAX_UNPACKED = 100 * 1024 * 1024
MCP_CATEGORIES = {"knowledge", "cloud_information", "fund", "internal_system", "employee_service", "other"}
FORBIDDEN_NAMES = {"dockerfile", "docker-compose.yml", "docker-compose.yaml"}
# 仅禁止可执行二进制与 shell 脚本：技能允许携带 .py 等说明型脚本（随包投放进
# harness 容器 /workspace/skills/<slug>/，由数字员工按 SKILL.md 的说明调用）。
FORBIDDEN_SUFFIXES = {".exe", ".dll", ".so", ".dylib", ".bat", ".cmd", ".ps1", ".sh"}
SECRET_KEYS = {"authorization", "token", "api_key", "apikey", "password", "secret"}
# 版本号是制品目录名的一部分（``published/<plugin_id>/<version>/``），
# 因此必须是受限字符集，禁止 ``/``、``..`` 等路径成分。
VERSION_PATTERN = re.compile(r"^[0-9A-Za-z][0-9A-Za-z._-]{0,31}$")


def validate_version(version: str) -> str:
    """校验并归一化版本号；不合法时抛 422。"""
    normalized = (version or "").strip()
    if not VERSION_PATTERN.match(normalized):
        raise HTTPException(
            status_code=422,
            detail="版本号不合法：需以字母或数字开头，仅可包含字母、数字、点、下划线和连字符，最长 32 位",
        )
    return normalized


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


def next_plugin_id(plugin_type: str = "mcp") -> str:
    """技能与工具类插件使用不同 ID 前缀：``SKILL-`` / ``PLG-``。

    ``SKILL-<skill.id>`` 同时是工作台 ``Skill`` 行的映射关系
    （见 ``routers/workplace.py::_sync_skill_plugin``），因此技能必须落 ``SKILL-``。
    """
    prefix = "SKILL" if plugin_type == "skill" else "PLG"
    return f"{prefix}-{uuid4().hex[:12].upper()}"


def _skill_row(db: Session, plugin_id: str) -> models.Skill | None:
    """技能插件对应的 ``Skill`` 行（工作台编辑视图）；非技能插件返回 None。

    ``Skill.id`` 与插件 ID 固定满足 ``plugin_id == f"SKILL-{skill.id}"``。
    """
    if not plugin_id.startswith("SKILL-"):
        return None
    return db.get(models.Skill, plugin_id[len("SKILL-"):])


def _sync_skill_row(
    db: Session, plugin_id: str, *, name: str, description: str, content: str, owner_human_no: str,
) -> None:
    """把插件化的技能同步到 ``Skill`` 表（工作台编辑视图）。

    上传链路与工作台内联编辑链路因此共享同一条记录，员工上传的技能能在工作台继续编辑，
    而不是变成两份互不相干的数据。
    """
    skill = _skill_row(db, plugin_id)
    if skill is None and not plugin_id.startswith("SKILL-"):
        # 兼容极早期以 Skill.id 直接作为插件 ID 的历史数据
        skill = db.get(models.Skill, plugin_id)
    if skill is None:
        db.add(models.Skill(
            id=plugin_id[len("SKILL-"):] if plugin_id.startswith("SKILL-") else plugin_id,
            owner_human_no=owner_human_no, name=name,
            description=description, content=content[:8000], status="active",
        ))
        return
    skill.name = name
    skill.description = description
    skill.content = content[:8000]
    skill.status = "active"


def submit_zip(
    db: Session, *, data: bytes, filename: str, name: str, plugin_type: str, scope: str,
    category: str | None, deployment_mode: str, data_level: str, version: str,
    submitter: str, target_agent_id: str | None, plugin_id: str | None = None,
    featured: bool = False,
) -> models.PluginVersion:
    """提交一个插件版本。

    ``plugin_id`` 为空表示新建插件；非空时必须是自己名下的插件，用于「为已有插件提交新版本」。
    此前每次上传都新建一条插件，导致版本机制空转、同名插件互相孤立。

    ``category`` 按类型落不同字段：MCP → ``mcp_category``，技能 → ``skill_category``
    （SkillHub 导入时由 tags 映射得出，员工上传时通常为空）。``featured`` 是运营推荐位，
    只有 SkillHub 导入与管理员治理会置位。
    """
    if plugin_type not in {"skill", "mcp"} or scope not in {"personal", "shared"}:
        raise HTTPException(status_code=422, detail="插件类型或范围不合法")
    if not name.strip():
        raise HTTPException(status_code=422, detail="插件名称不能为空")
    if plugin_type == "mcp" and (category or "other") not in MCP_CATEGORIES:
        raise HTTPException(status_code=422, detail="MCP 分类不合法")
    skill_category = (category or "").strip()[:40] or None
    if plugin_type == "mcp":
        skill_category = None
    if deployment_mode not in ({"instruction"} if plugin_type == "skill" else {"external", "hosted"}):
        raise HTTPException(status_code=422, detail="部署模式不适用于该插件类型")
    version = validate_version(version)
    manifest, digest = validate_zip(data, plugin_type, deployment_mode)

    plugin = db.get(models.Plugin, plugin_id) if plugin_id else None
    if plugin_id and plugin is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    if plugin is not None:
        # 个人插件只有属主能续版本；共享插件只有此前提交过该插件的人能续版本，
        # 避免任何人都能往别人的插件上追加版本。
        authored = db.scalar(select(models.PluginVersion.id).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.submitted_by == submitter,
        ).limit(1)) is not None
        if plugin.owner_human_no != submitter and not authored:
            raise HTTPException(status_code=403, detail="只能为自己提交过的插件提交新版本")
        if plugin.plugin_type != plugin_type:
            raise HTTPException(status_code=422, detail="插件类型与已有插件不一致")
        if db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == version,
        )) is not None:
            raise HTTPException(status_code=409, detail=f"版本 {version} 已存在，请更换版本号")

    new_plugin_id = plugin.id if plugin is not None else next_plugin_id(plugin_type)
    submission_id = f"SUB-{uuid4().hex[:12].upper()}"
    relative = Path("staging") / submission_id / (Path(filename).name or "plugin.zip")
    target = artifact_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    auto_publish = (
        plugin_type == "skill" and scope == "personal" and data_level == "L1"
        and target_agent_id == f"DT-{submitter}"
    )
    description = str(manifest.get("description", ""))
    if plugin is None:
        plugin = models.Plugin(
            id=new_plugin_id, name=name.strip(), type=plugin_type,
            plugin_type=plugin_type, scope=scope,
            owner_human_no=submitter if scope == "personal" else None,
            mcp_category=category if plugin_type == "mcp" else None,
            skill_category=skill_category,
            featured=bool(featured),
            current_version=version if auto_publish else None,
            endpoint_ref="mock://", data_level=data_level,
            status="active" if auto_publish else "draft",
            description=description, runtime_meta={},
        )
        db.add(plugin)
    else:
        plugin.name = name.strip()
        plugin.description = description or plugin.description
        # 分类/推荐位只在显式给出时覆盖：员工续版本（category=None）不应清掉已标注的分类。
        if skill_category and plugin.plugin_type == "skill":
            plugin.skill_category = skill_category
        if featured:
            plugin.featured = True
        if auto_publish:
            plugin.current_version = version
            plugin.status = "active"
    row = models.PluginVersion(
        plugin_id=new_plugin_id, version=version, deployment_mode=deployment_mode,
        artifact_path=relative.as_posix(), sha256=digest, manifest=manifest, data_level=data_level,
        review_status="approved" if auto_publish else "pending",
        publish_status="published" if auto_publish else "submitted", submitted_by=submitter,
        reviewed_by="automatic-policy" if auto_publish else None,
        reviewed_at=datetime.now() if auto_publish else None,
        published_at=datetime.now() if auto_publish else None,
    )
    db.add(row)
    db.flush()
    if auto_publish and target_agent_id:
        published = artifact_root() / "published" / new_plugin_id / version / target.name
        published.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copy2(target, published)
        except shutil.SameFileError:
            pass
        row.artifact_path = published.relative_to(artifact_root()).as_posix()
        if db.scalar(select(models.AgentPluginBinding).where(
            models.AgentPluginBinding.plugin_id == new_plugin_id,
            models.AgentPluginBinding.target_agent_id == target_agent_id,
        )) is None:
            db.add(models.AgentPluginBinding(
                plugin_id=new_plugin_id, target_agent_id=target_agent_id, authorized_by="automatic-policy",
                employee_enabled=True, admin_enabled=True, decision_mode="allow",
            ))
        _sync_skill_row(
            db, new_plugin_id, name=plugin.name,
            description=plugin.description or description,
            content=str(manifest.get("instruction") or ""), owner_human_no=submitter,
        )
    db.commit()
    db.refresh(row)
    if auto_publish:
        expand_skill_artifact(plugin, row)
    return row


def effective_plugins(db: Session, agent_id: str, requester_human_no: str) -> list[dict]:
    """某AI员工当前生效的插件清单（唯一解析出口）。

    判定：存在 ``admin_enabled ∧ employee_enabled ∧ decision_mode='allow'`` 的绑定，
    目标插件 active 且有已发布版本。个人插件仅对属主可见。
    插件与版本一次 ``IN`` 查询批量取回，避免按绑定逐条查询（N+1）。
    """
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == agent_id,
        models.AgentPluginBinding.admin_enabled.is_(True),
        models.AgentPluginBinding.employee_enabled.is_(True),
        models.AgentPluginBinding.decision_mode == "allow",
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    if not bindings:
        return []
    plugin_ids = [binding.plugin_id for binding in bindings if binding.plugin_id]
    plugins = {
        row.id: row
        for row in db.scalars(select(models.Plugin).where(
            models.Plugin.id.in_(plugin_ids),
            models.Plugin.status == "active",
        )).all()
    }
    if not plugins:
        return []
    versions = {
        (row.plugin_id, row.version): row
        for row in db.scalars(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id.in_(list(plugins)),
            models.PluginVersion.publish_status == "published",
        )).all()
    }
    result: list[dict] = []
    for binding in bindings:
        plugin = plugins.get(binding.plugin_id)
        if plugin is None or not plugin.current_version:
            continue
        if plugin.scope == "personal" and plugin.owner_human_no != requester_human_no:
            continue
        version_no = binding.pinned_version or plugin.current_version
        version = versions.get((plugin.id, version_no))
        if version is None:
            continue
        item = {"plugin_id": plugin.id, "version": version.version, "plugin_type": plugin.plugin_type, "name": plugin.name}
        if plugin.plugin_type == "skill":
            # Skill 通过前置路由（task_playbook）注入完整 SOP；此处仅保留名称与描述元数据。
            item["description"] = plugin.description
            item["skill_category"] = plugin.skill_category
            item["featured"] = bool(plugin.featured)
        else:
            item.update({"category": plugin.mcp_category, "tools": (version.manifest or {}).get("tools", [])})
        result.append(item)
    return result


def available_plugins(db: Session, agent_id: str, requester_human_no: str) -> list[dict]:
    """管理员已授权、但该AI员工尚未启用的插件 —— 与 ``effective_plugins`` 严格互斥。

    员工侧「可添加」列表的**唯一来源**：必须存在绑定且 ``admin_enabled``。
    未绑定的插件不再出现（此前点击「启用」必然 403），已启用的也不会重复出现。
    """
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == agent_id,
        models.AgentPluginBinding.admin_enabled.is_(True),
        models.AgentPluginBinding.employee_enabled.is_(False),
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    if not bindings:
        return []
    plugin_ids = [binding.plugin_id for binding in bindings if binding.plugin_id]
    plugins = {
        row.id: row
        for row in db.scalars(select(models.Plugin).where(
            models.Plugin.id.in_(plugin_ids),
            models.Plugin.status == "active",
        )).all()
    }
    result: list[dict] = []
    for binding in bindings:
        plugin = plugins.get(binding.plugin_id)
        if plugin is None:
            continue
        if plugin.scope == "personal" and plugin.owner_human_no != requester_human_no:
            continue
        result.append({
            "id": plugin.id,
            "name": plugin.name,
            "plugin_type": plugin.plugin_type,
            "scope": plugin.scope,
            "mcp_category": plugin.mcp_category,
            "skill_category": plugin.skill_category,
            "featured": bool(plugin.featured),
            "current_version": plugin.current_version,
            "status": plugin.status,
            "description": plugin.description,
        })
    return result


MarketState = Literal["own_draft", "enabled", "granted", "blocked", "pending", "requestable"]


def _market_state(
    plugin: models.Plugin,
    binding: models.AgentPluginBinding | None,
    requester_human_no: str,
    published_versions: list[models.PluginVersion],
    request_id: int | None,
) -> str:
    """六态判定（优先级自上而下），规则见 ``market_plugins`` docstring。"""
    has_published = bool(published_versions)
    if plugin.scope == "personal" and plugin.owner_human_no == requester_human_no:
        # 本人尚未发布（草稿/被驳回/无已发布版本）的个人插件只提供「提交新版本」入口。
        if plugin.status != "active" or not has_published:
            return "own_draft"
    if binding is not None and binding.decision_mode == "deny":
        # 管理员的明确禁止意图，不应被员工申请绕过。
        return "blocked"
    if (
        binding is not None
        and binding.admin_enabled
        and binding.employee_enabled
        and binding.decision_mode == "allow"
        and plugin.status == "active"
        and has_published
    ):
        return "enabled"
    if binding is not None and binding.admin_enabled and not binding.employee_enabled and has_published:
        return "granted"
    if request_id:
        return "pending"
    return "requestable"


def _market_version(
    plugin: models.Plugin, binding: models.AgentPluginBinding | None, published_versions: list[models.PluginVersion],
) -> str | None:
    """卡片上展示的版本号：优先绑定锁定版本，其次当前版本，最后回落到最新已发布版本。"""
    if not published_versions:
        return None
    by_version = {row.version: row for row in published_versions}
    pinned = binding.pinned_version if binding else None
    if pinned and pinned in by_version:
        return pinned
    if plugin.current_version and plugin.current_version in by_version:
        return plugin.current_version
    latest = max(published_versions, key=lambda row: row.published_at or row.created_at)
    return latest.version


def _market_source(plugin: models.Plugin, published_versions: list[models.PluginVersion], version: str | None) -> str:
    """卡片上的「来源」徽标：SkillHub 导入 / 平台共享 / 本人。

    SkillHub 导入的技能包在 ``plugin.yaml`` 里带 ``slug``，落库后存进
    ``manifest['slug']``，据此识别来源，不额外加字段、不引入第二套事实。
    """
    if plugin.scope == "personal":
        return "personal"
    if plugin.plugin_type == "skill" and version:
        chosen = next((row for row in published_versions if row.version == version), None)
        if chosen is not None and str((chosen.manifest or {}).get("slug") or "").strip():
            return "skillhub"
    return "platform"


def market_plugins(
    db: Session, agent_id: str, requester_human_no: str, *, plugin_type: str | None = None,
) -> list[dict]:
    """员工端「插件市场」货架（只读，六态）。

    货架范围 = 「共享 ∧ active ∧ 有已发布版本」∪「本人 personal / 本人提交过版本的插件」；
    他人的 personal 插件一律不出现（与 ``my_plugin_detail`` 的可见性口径一致），
    已下架（withdrawn）插件不上架。

    ``plugin_type`` 用于把货架拆成两个市场（``skill`` 技能市场 / ``mcp`` MCP 市场）；
    不传则返回全部，保持老接口行为不变。**只加过滤，不改六态计算逻辑**。

    ``state`` 由 ``agent_plugin_binding``（授权事实）与 ``access_request``（申请事实）
    合成，优先级自上而下：

    ``own_draft``（本人未发布）→ ``enabled``（已启用）→ ``granted``（已授权待启用）
    → ``blocked``（管理员已禁止）→ ``pending``（申请中）→ ``requestable``（可申请）。

    固定字段：plugin_id, id, name, plugin_type, scope, mcp_category, skill_category,
    data_level, current_version, version, description, owner_human_no, source, featured,
    state, request_id, decision_mode。

    排序：精选（``featured``）稳定前置，其余按名称；前端不再二次排序。
    """
    submitted_ids = set(db.scalars(select(models.PluginVersion.plugin_id).where(
        models.PluginVersion.submitted_by == requester_human_no,
    )).all())
    conditions = [
        models.Plugin.status != "withdrawn",
        or_(
            and_(models.Plugin.scope == "shared", models.Plugin.status == "active"),
            models.Plugin.owner_human_no == requester_human_no,
            models.Plugin.id.in_(list(submitted_ids) or [""]),
        ),
    ]
    wanted = (plugin_type or "").strip()
    if wanted:
        conditions.append(models.Plugin.plugin_type == wanted)
    plugins = db.scalars(select(models.Plugin).where(*conditions).order_by(models.Plugin.name)).all()
    if not plugins:
        return []
    plugin_ids = [row.id for row in plugins]
    published: dict[str, list[models.PluginVersion]] = {}
    for row in db.scalars(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id.in_(plugin_ids),
        models.PluginVersion.publish_status == "published",
    )).all():
        published.setdefault(row.plugin_id, []).append(row)
    bindings: dict[str, models.AgentPluginBinding] = {}
    for row in db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == agent_id,
        models.AgentPluginBinding.plugin_id.in_(plugin_ids),
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all():
        # 同一插件理论上只有一条绑定；出现脏数据时以优先级最高（值最小）的一条为准。
        bindings.setdefault(row.plugin_id, row)
    pending = {
        row.resource_id: row.id
        for row in db.scalars(select(models.AccessRequest).where(
            models.AccessRequest.applicant_no == agent_id,
            models.AccessRequest.resource_type == "plugin",
            models.AccessRequest.resource_id.in_(plugin_ids),
            models.AccessRequest.status == "pending",
        )).all()
    }

    result: list[dict] = []
    for plugin in plugins:
        versions = published.get(plugin.id, [])
        if plugin.scope == "shared" and not versions:
            # 共享插件没有已发布版本时不具备装载价值，不上架。
            continue
        binding = bindings.get(plugin.id)
        request_id = pending.get(plugin.id)
        version = _market_version(plugin, binding, versions)
        result.append({
            "plugin_id": plugin.id,
            "id": plugin.id,
            "name": plugin.name,
            "plugin_type": plugin.plugin_type,
            "scope": plugin.scope,
            "mcp_category": plugin.mcp_category,
            "skill_category": plugin.skill_category,
            "data_level": plugin.data_level,
            "current_version": plugin.current_version,
            "version": version,
            "description": plugin.description,
            "owner_human_no": plugin.owner_human_no,
            "source": _market_source(plugin, versions, version),
            "featured": bool(plugin.featured),
            "state": _market_state(plugin, binding, requester_human_no, versions, request_id),
            "request_id": request_id,
            "decision_mode": binding.decision_mode if binding else None,
        })
    # 精选稳定前置：Python 的 sort 稳定，等价于「分区」且不打乱同组内的名称顺序。
    result.sort(key=lambda row: 0 if row["featured"] else 1)
    return result


def effective_skills(db: Session, agent_id: str) -> list[dict]:
    """某AI员工当前可用的技能（Skill）清单，含 SOP 正文。

    技能可用性的**唯一来源**，判定与 ``effective_plugins`` 完全同源：存在
    ``admin_enabled ∧ employee_enabled ∧ decision_mode='allow'`` 的绑定，且目标插件
    ``plugin_type='skill'``、status=active、并存在已发布版本。

    正文取版本 ``manifest['instruction']``（缺失时回落到 ``instruction_summary``），
    因此「工作台内联编辑」「插件中心上传 ZIP」「历史种子迁移」三种载体收敛到同一条链路。

    返回字典固定含 ``name``/``description``/``content``，Harness 前置路由、
    ``TASK_ENVELOPE.available_skills``、chat 兜底注入与 ``load_skill`` 工具只读这三键，
    所以下游无需改动。
    """
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.target_agent_id == agent_id,
        models.AgentPluginBinding.admin_enabled.is_(True),
        models.AgentPluginBinding.employee_enabled.is_(True),
        models.AgentPluginBinding.decision_mode == "allow",
    ).order_by(models.AgentPluginBinding.priority, models.AgentPluginBinding.id)).all()
    if not bindings:
        return []
    plugin_ids = [binding.plugin_id for binding in bindings if binding.plugin_id]
    plugins = {
        row.id: row
        for row in db.scalars(select(models.Plugin).where(
            models.Plugin.id.in_(plugin_ids),
            models.Plugin.plugin_type == "skill",
            models.Plugin.status == "active",
        )).all()
    }
    if not plugins:
        return []
    result: list[dict] = []
    for binding in bindings:
        plugin = plugins.get(binding.plugin_id)
        if plugin is None:
            continue
        version_no = binding.pinned_version or plugin.current_version
        if not version_no:
            continue
        version = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == version_no,
            models.PluginVersion.publish_status == "published",
        ))
        if version is None:
            continue
        manifest = version.manifest or {}
        content = str(manifest.get("instruction") or manifest.get("instruction_summary") or "")
        item = {
            "id": plugin.id,
            "name": plugin.name,
            "description": plugin.description,
            "content": content,
        }
        # 技能包已展开到制品库时，一并给出容器内的技能包目录：脚本型技能（SkillHub
        # 导入的 `python scripts/...` 类技能）只有拿到这个路径才能真跑。
        package_dir = skill_package_dir(plugin, version)
        if package_dir:
            item["package_dir"] = package_dir
        result.append(item)
    return result


def skill_package_dir(plugin: models.Plugin, version: models.PluginVersion) -> str | None:
    """技能包在 harness 容器内的目录（``/workspace/skills/<slug>``）。

    仅当制品库里确实存在展开目录时返回；否则返回 None，避免给模型一个空路径。
    """
    if plugin.plugin_type != "skill":
        return None
    slug = skill_artifact_slug(plugin, version)
    if not (artifact_root() / "skills" / slug / "SKILL.md").is_file():
        return None
    return f"{CONTAINER_SKILLS_DIR}/{slug}"


def effective_skill_artifacts(db: Session, agent_id: str) -> list[dict]:
    """某AI员工已授权生效技能的**制品展开目录**（容器投放的源目录）。

    判定与 ``effective_skills`` 完全同源（同一组绑定 + 同一发布态），只是额外解析出
    ``artifact_root()/skills/<slug>/`` 这个可读可执行的目录，供 ``runtime_manager``
    幂等投放进容器 ``/workspace/skills/<slug>/``。没有制品文件的技能（历史数据、
    纯文本个人技能）跳过，不阻断投放。
    """
    artifacts: list[dict] = []
    for item in effective_skills(db, agent_id):
        version = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == item["id"],
            models.PluginVersion.publish_status == "published",
        ).order_by(models.PluginVersion.published_at.desc(), models.PluginVersion.id.desc()))
        if version is None:
            continue
        plugin = db.get(models.Plugin, item["id"])
        if plugin is None:
            continue
        slug = skill_artifact_slug(plugin, version)
        source = artifact_root() / "skills" / slug
        if not (source / "SKILL.md").is_file():
            continue
        artifacts.append({
            "plugin_id": plugin.id,
            "name": plugin.name,
            "slug": slug,
            "source": str(source),
            "sha256": version.sha256 or "",
        })
    return artifacts


def skill_artifact_slug(plugin: models.Plugin, version: models.PluginVersion) -> str:
    """技能包展开目录名：优先用 manifest 里的 SkillHub ``slug``，回落插件 ID。"""
    slug = str((version.manifest or {}).get("slug") or "").strip()
    if slug and re.fullmatch(r"[0-9A-Za-z][0-9A-Za-z._-]{0,63}", slug):
        return slug
    return plugin.id


def expand_skill_artifact(plugin: models.Plugin, version: models.PluginVersion) -> Path | None:
    """把已发布技能包展开到 ``artifact_root()/skills/<slug>/``。

    这是技能包「可直接阅读/可直接投放」的源目录：容器投放（``runtime_manager``）
    从这里同步到 ``harness/<EMP>/workspace/skills/<slug>/``，运维也能直接查看
    某个技能的 SKILL.md 与 scripts。幂等：sha256 未变则跳过；失败只记日志，
    绝不阻断发布本身。
    """
    if plugin.plugin_type != "skill" or not version.artifact_path:
        return None
    source = artifact_root() / version.artifact_path
    if not source.is_file():
        return None
    target = artifact_root() / "skills" / skill_artifact_slug(plugin, version)
    marker = target / ".dwp-source-sha256"
    if marker.is_file():
        try:
            if marker.read_text(encoding="utf-8").strip() == (version.sha256 or ""):
                return target
        except OSError:
            pass
    staging = target.parent / f".{target.name}.staging"
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(source) as archive:
            for info in archive.infolist():
                path = PurePosixPath(info.filename.replace("\\", "/"))
                if path.is_absolute() or ".." in path.parts or not path.parts:
                    continue
                destination = staging.joinpath(*path.parts)
                if info.is_dir():
                    destination.mkdir(parents=True, exist_ok=True)
                    continue
                destination.parent.mkdir(parents=True, exist_ok=True)
                destination.write_bytes(archive.read(info))
                mode = (info.external_attr >> 16) & 0o777
                if mode:
                    destination.chmod(mode | 0o600)
        (staging / ".dwp-source-sha256").write_text(version.sha256 or "", encoding="utf-8")
    except (OSError, zipfile.BadZipFile) as exc:
        logger.warning("技能包展开失败 plugin=%s version=%s: %s", plugin.id, version.version, exc)
        shutil.rmtree(staging, ignore_errors=True)
        return None
    shutil.rmtree(target, ignore_errors=True)
    try:
        staging.rename(target)
    except OSError as exc:
        logger.warning("技能包展开目录落位失败 plugin=%s: %s", plugin.id, exc)
        shutil.rmtree(staging, ignore_errors=True)
        return None
    return target


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
    # 历史种子数据与 _sync_skill_plugin 生成的版本没有 artifact_path，
    # 直接拼路径会落到 artifacts 根目录并触发 IsADirectoryError（500）。
    if not version.artifact_path:
        raise HTTPException(
            status_code=409,
            detail="该版本没有制品文件（历史数据缺少 artifact_path），请重新提交该版本后再发布",
        )
    source = artifact_root() / version.artifact_path
    if not source.is_file():
        raise HTTPException(status_code=409, detail="该版本的制品文件不存在，请重新提交该版本")
    destination = artifact_root() / "published" / plugin.id / version.version / source.name
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        shutil.copy2(source, destination)
    except shutil.SameFileError:
        # submit_zip 自动发布已把制品放到目标位置，重复发布视为幂等。
        pass
    for row in old:
        row.publish_status = "superseded"
    version.artifact_path = destination.relative_to(artifact_root()).as_posix()
    version.publish_status = "published"
    version.published_at = datetime.now()
    plugin.current_version = version.version
    plugin.status = "active"
    if plugin.plugin_type == "skill":
        # 技能与工作台 Skill 行联动：发布即恢复可用（下架时会被置为 disabled）。
        skill_row = _skill_row(db, plugin.id)
        if skill_row is not None:
            skill_row.status = "active"
    # 个人插件发布后自动为属主的分身建一条「管理员已授权、员工未启用」的绑定，
    # 员工在「可添加」里自助启用；否则个人技能通过审核后无人能够启用它。
    if plugin.scope == "personal" and plugin.owner_human_no:
        twin_id = f"DT-{plugin.owner_human_no}"
        if db.get(models.DigitalEmployee, twin_id) is not None and db.scalar(
            select(models.AgentPluginBinding).where(
                models.AgentPluginBinding.plugin_id == plugin.id,
                models.AgentPluginBinding.target_agent_id == twin_id,
            )
        ) is None:
            db.add(models.AgentPluginBinding(
                plugin_id=plugin.id, target_agent_id=twin_id, authorized_by=reviewer,
                admin_enabled=True, employee_enabled=False, decision_mode="allow",
            ))
    if plugin.plugin_type == "mcp":
        # Hosted MCP 仍未接入真实运行时：state/health 如实标注未接入，
        # 由前端呈现为「未接入真实运行时」，避免把 mock 状态当成可用能力。
        # External MCP（含 URL 自助接入）是平台直连的现成服务，按探测结果写真实连通状态。
        runtime_mode = os.getenv("DWP_MCP_RUNTIME_MODE", "mock")
        real_runtime = runtime_mode == "real"
        probe_meta = (plugin.runtime_meta or {}).get("probe")
        external_ready = (
            version.deployment_mode == "external"
            and isinstance(probe_meta, dict)
            and probe_meta.get("tools_count") is not None
        )
        build = models.PluginBuildJob(
            id=f"PB-{uuid4().hex[:14].upper()}", plugin_version_id=version.id,
            status="completed" if (real_runtime or external_ready) else "not_configured",
            runtime=runtime_mode,
        )
        db.add(build)
        db.add(models.McpRuntimeInstance(
            plugin_version_id=version.id,
            container_name=(
                f"dwp-mcp-{plugin.id.lower()}-{version.version.replace('.', '-')}"
                if version.deployment_mode == "hosted" else ""
            ),
            state=(
                "running" if external_ready
                else "pending" if real_runtime else "not_configured"
            ),
            health="healthy" if external_ready else "unknown",
        ))
    db.commit()
    if plugin.plugin_type == "skill":
        # 技能包展开到 artifact_root()/skills/<slug>/：容器投放与人工查看的共同源目录。
        expand_skill_artifact(plugin, version)
    # 发布/重新发布后，动态注册表必须立即感知（否则新服务最长要等 TTL 才生效）。
    from . import mcp_registry

    mcp_registry.invalidate(plugin.id)


def withdraw_plugin(db: Session, plugin: models.Plugin) -> models.Plugin:
    """下架插件：置为 withdrawn 并停用全部绑定，五处消费点立即一致地不可见。

    技能插件同时把工作台 ``Skill`` 行置为 disabled，避免员工在工作台编辑时又把它改回可用。
    幂等：重复下架不会报错。
    """
    if plugin.status == "withdrawn":
        return plugin
    plugin.status = "withdrawn"
    bindings = db.scalars(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin.id,
    )).all()
    for binding in bindings:
        binding.admin_enabled = False
    if plugin.plugin_type == "skill":
        skill_row = _skill_row(db, plugin.id)
        if skill_row is not None:
            skill_row.status = "disabled"
    db.commit()
    db.refresh(plugin)
    # 下架后动态注册表立即失效，工具清单与调用分派同步收敛。
    from . import mcp_registry

    mcp_registry.invalidate(plugin.id)
    return plugin


def restore_plugin(db: Session, plugin: models.Plugin) -> models.Plugin:
    """恢复已下架的插件：只把状态改回 active，**不**自动打开被下架停用的绑定。

    下架时停用绑定是管理员的明确决定，恢复上架不等于恢复授权 —— 需要重新授权时
    走 ``/admin/agent-plugin-bindings``。幂等：未下架时直接返回。
    """
    if plugin.status != "withdrawn":
        return plugin
    plugin.status = "active"
    if plugin.plugin_type == "skill":
        skill_row = _skill_row(db, plugin.id)
        if skill_row is not None and skill_row.status == "disabled":
            skill_row.status = "active"
    db.commit()
    db.refresh(plugin)
    from . import mcp_registry

    mcp_registry.invalidate(plugin.id)
    return plugin


def resubmit_version(
    db: Session, version: models.PluginVersion, *, data: bytes, filename: str,
) -> models.PluginVersion:
    """被驳回的版本重新提交：复用同一行与同一版本号，只替换制品与状态。

    这样「驳回 → 修改 → 重提」不会产生一堆孤立版本，审计仍指向同一个版本记录。
    """
    if version.review_status != "rejected":
        raise HTTPException(status_code=409, detail="只有被驳回的提交可以重新提交")
    plugin = db.get(models.Plugin, version.plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="插件不存在")
    manifest, digest = validate_zip(data, plugin.plugin_type, version.deployment_mode)
    submission_id = f"SUB-{uuid4().hex[:12].upper()}"
    relative = Path("staging") / submission_id / (Path(filename).name or "plugin.zip")
    target = artifact_root() / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    version.artifact_path = relative.as_posix()
    version.sha256 = digest
    version.manifest = manifest
    version.review_status = "pending"
    version.publish_status = "submitted"
    version.review_note = ""
    version.reviewed_by = None
    version.reviewed_at = None
    db.commit()
    db.refresh(version)
    return version
