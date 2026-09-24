"""MCP 服务自助接入接口（管理端 ``/admin/mcp`` + 员工端 ``/my/mcp``）。

三段式流程：
1. ``POST /probe`` 填地址与鉴权 → 平台完成 MCP 握手并解析工具清单（不落库）；
2. ``POST /draft`` 把探测结果交给大模型补写中文名/简介/工具说明（不落库，
   可带自定义 ``prompt`` 重新生成一版）；
3. ``POST /plugins`` 人工确认后落库：管理端登记即发布，员工端进审核队列。

``GET /plugins`` 返回已登记服务清单（含连通状态与工具数），**不含任何凭据**。

复用既有治理链路：审核用 ``/admin/plugin-submissions/*/approve|reject``，
发布用 ``/admin/plugins/{id}/versions/{v}/publish``，
授权用 ``/admin/agent-plugin-bindings``，员工自启用 ``/my/plugins/{id}/enable``。
"""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import get_db
from ..services import config, mcp_credentials, mcp_intro, mcp_onboarding
from ..services.auth import current_account, require_roles
from ..services.external_mcp_client import ExternalMcpError

my_router = APIRouter(prefix="/my/mcp", tags=["my-mcp"])
admin_router = APIRouter(prefix="/admin/mcp", tags=["mcp-onboarding"])
admin_required = require_roles("agent_admin", "security_admin", "platform_admin")


class ProbeIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    auth_header_name: str | None = None
    # None = 复用已存密钥（编辑场景）；"" = 显式匿名接入。
    api_key: str | None = None
    timeout_seconds: float | None = None
    plugin_id: str | None = None


class ToolMetaIn(BaseModel):
    model_config = ConfigDict(extra="ignore")

    name: str
    description: str = ""
    parameters: list[dict] = Field(default_factory=list)
    required: list[str] = Field(default_factory=list)


class DraftIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tools: list[ToolMetaIn] = Field(default_factory=list)
    server_info: dict | None = None
    url: str = ""
    prompt: str | None = None


class CreateIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    url: str
    name: str
    description: str = ""
    auth_header_name: str | None = None
    api_key: str | None = None
    timeout_seconds: float | None = None
    mcp_category: str | None = None
    data_level: str = "L1"
    scope: str = "shared"
    version: str = "1.0.0"
    # 工具清单：既接受探测摘要（name/description/parameters），也接受完整 inputSchema。
    tools: list[dict] = Field(default_factory=list)
    server_info: dict | None = None
    latency_ms: int | None = None


def _service_out(db: Session, row: models.PluginVersion) -> dict:
    plugin = db.get(models.Plugin, row.plugin_id)
    meta = (plugin.runtime_meta or {}) if plugin else {}
    probe_meta = meta.get("probe") if isinstance(meta.get("probe"), dict) else {}
    runtime = db.scalar(select(models.McpRuntimeInstance).join(
        models.PluginVersion, models.PluginVersion.id == models.McpRuntimeInstance.plugin_version_id,
    ).where(
        models.PluginVersion.plugin_id == row.plugin_id,
        models.PluginVersion.publish_status == "published",
    ).order_by(models.McpRuntimeInstance.updated_at.desc())) if plugin else None
    return {
        "plugin_id": row.plugin_id,
        "name": plugin.name if plugin else row.plugin_id,
        "description": plugin.description if plugin else "",
        "plugin_type": "mcp",
        "scope": plugin.scope if plugin else "shared",
        "status": plugin.status if plugin else "draft",
        "current_version": plugin.current_version if plugin else None,
        "version": row.version,
        "review_status": row.review_status,
        "publish_status": row.publish_status,
        "mcp_category": plugin.mcp_category if plugin else None,
        # 数据等级的授权真相在 Plugin 上（编辑接口与治理接口都写这里），
        # 版本行上的值只是发布时的历史记录，读路径以 Plugin 为准。
        "data_level": (plugin.data_level if plugin else None) or row.data_level,
        "url": meta.get("url"),
        "timeout_seconds": meta.get("timeout_seconds"),
        "tool_prefix": meta.get("tool_prefix"),
        "authenticated": bool(meta.get("authenticated")),
        "auth_header_name": meta.get("auth_header_name"),
        "secret_hint": mcp_credentials.hint_for(db, row.plugin_id),
        "tools_count": len((row.manifest or {}).get("tools") or []),
        "owner_human_no": plugin.owner_human_no if plugin else None,
        **mcp_onboarding.probe_digest(probe_meta, runtime),
    }


def _current_version_row(db: Session, plugin: models.Plugin) -> models.PluginVersion | None:
    """插件当前已发布版本行（出参整形用）；没有已发布版本时回落到最新一行。"""
    if plugin.current_version:
        row = db.scalar(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id,
            models.PluginVersion.version == plugin.current_version,
        ))
        if row is not None:
            return row
    return db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
    ).order_by(models.PluginVersion.id.desc()))


def _owned_url_plugin(db: Session, plugin_id: str, owner_human_no: str) -> models.Plugin:
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None or plugin.owner_human_no != owner_human_no:
        raise HTTPException(status_code=404, detail="MCP 服务不存在或当前账号不可见")
    if not str((plugin.runtime_meta or {}).get("url") or "").strip():
        raise HTTPException(status_code=409, detail="该插件不是 URL 接入的 MCP 服务")
    return plugin


# -- 探测 / 生成草稿（两端共用同一实现） --------------------------------------


async def _probe(db: Session, payload: ProbeIn) -> dict:
    try:
        return await asyncio.to_thread(
            mcp_onboarding.probe, db,
            url=payload.url, header_name=payload.auth_header_name,
            api_key=payload.api_key, timeout_seconds=payload.timeout_seconds,
            plugin_id=payload.plugin_id,
        )
    except ExternalMcpError as exc:
        # 探测失败要给出可读原因（超时/认证失败/端点不存在），而不是裸 500。
        raise HTTPException(status_code=502, detail=f"连接 MCP 服务失败：{exc}") from exc


def _draft(payload: DraftIn) -> dict:
    return mcp_intro.generate_draft(
        tools=[tool.model_dump() for tool in payload.tools],
        server_info=payload.server_info,
        url=payload.url,
        prompt=payload.prompt,
    )


# -- 管理端 ------------------------------------------------------------------


@admin_router.post("/probe")
async def admin_probe(
    payload: ProbeIn, _: models.Account = Depends(admin_required), db: Session = Depends(get_db),
):
    return await _probe(db, payload)


@admin_router.post("/draft")
def admin_draft(payload: DraftIn, _: models.Account = Depends(admin_required)):
    return _draft(payload)


@admin_router.get("/plugins")
def admin_services(_: models.Account = Depends(admin_required), db: Session = Depends(get_db)):
    return {
        "services": mcp_onboarding.list_registered(db),
        "credential_encryption_available": mcp_credentials.encryption_available(),
    }


@admin_router.post("/plugins", status_code=201)
def admin_create(
    payload: CreateIn, account: models.Account = Depends(admin_required), db: Session = Depends(get_db),
):
    """管理端登记即发布：管理员登记本身即授权行为，发布后到「AI员工插件配置」授权。"""
    if payload.scope != "shared":
        raise HTTPException(status_code=422, detail="管理端只能登记企业级共享 MCP 服务")
    row = mcp_onboarding.create_mcp_plugin(
        db, url=payload.url, header_name=payload.auth_header_name, api_key=payload.api_key,
        timeout_seconds=payload.timeout_seconds, name=payload.name, description=payload.description,
        category=payload.mcp_category, data_level=payload.data_level, scope="shared",
        tools=payload.tools, version=payload.version, submitter=account.username,
        auto_publish=True, reviewer=account.username, server_info=payload.server_info,
        probe_meta={"latency_ms": payload.latency_ms},
    )
    return _service_out(db, row)


@admin_router.post("/plugins/{plugin_id}/probe")
async def admin_reprobe(
    plugin_id: str, _: models.Account = Depends(admin_required), db: Session = Depends(get_db),
):
    plugin = db.get(models.Plugin, plugin_id)
    if plugin is None:
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    try:
        return await asyncio.to_thread(mcp_onboarding.refresh_probe, db, plugin)
    except ExternalMcpError as exc:
        raise HTTPException(status_code=502, detail=f"连接 MCP 服务失败：{exc}") from exc


class UpdateIn(BaseModel):
    """编辑已登记 MCP 服务；只提交需要改的字段。

    ``api_key`` 语义：不传 = 不动既有密钥；传空串 = 显式转匿名（删除凭据）；
    传非空 = 轮换。密钥只回掩码，永不回显明文。
    """

    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    mcp_category: str | None = None
    data_level: str | None = None
    url: str | None = None
    timeout_seconds: float | None = None
    auth_header_name: str | None = None
    api_key: str | None = None


@admin_router.put("/plugins/{plugin_id}")
async def admin_update(
    plugin_id: str,
    payload: UpdateIn,
    account: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
):
    """编辑 MCP 服务信息 / 地址 / 超时 / 鉴权；地址或密钥变化时立即重测。

    改地址、超时、鉴权头或密钥都会立刻重新探测（探测失败同样先把失败摘要落库，
    再返回 502 可读原因）；工具清单发生变化时自动发布一个新版本。
    """
    plugin = db.get(models.Plugin, plugin_id)
    if (
        plugin is None
        or plugin.plugin_type != "mcp"
        or not str((plugin.runtime_meta or {}).get("url") or "").strip()
    ):
        raise HTTPException(status_code=404, detail="MCP 服务不存在")
    try:
        outcome = await asyncio.to_thread(
            mcp_onboarding.update_mcp_plugin, db, plugin,
            name=payload.name, description=payload.description,
            category=payload.mcp_category, data_level=payload.data_level,
            url=payload.url, timeout_seconds=payload.timeout_seconds,
            header_name=payload.auth_header_name, api_key=payload.api_key,
            actor=account.username,
        )
    except ExternalMcpError as exc:
        raise HTTPException(status_code=502, detail=f"连接 MCP 服务失败：{exc}") from exc
    db.refresh(plugin)
    row = _current_version_row(db, plugin)
    if row is None:
        raise HTTPException(status_code=409, detail="该服务还没有任何版本，无法编辑")
    body = _service_out(db, row)
    body["reprobed"] = outcome.get("probe") is not None
    body["new_version"] = outcome.get("new_version")
    return body


# -- 员工端 ------------------------------------------------------------------


@my_router.post("/probe")
async def my_probe(
    payload: ProbeIn, account: models.Account = Depends(current_account), db: Session = Depends(get_db),
):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人 MCP 目录")
    if payload.plugin_id:
        _owned_url_plugin(db, payload.plugin_id, account.human_employee_no)
    return await _probe(db, payload)


@my_router.post("/draft")
def my_draft(payload: DraftIn, account: models.Account = Depends(current_account)):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人 MCP 目录")
    return _draft(payload)


def _credential_out(
    db: Session, *, plugin_id: str, human_no: str, name: str, category: str | None,
) -> dict:
    """用户级凭据的到期状态出参：**只有状态与到期时间，没有凭据内容（连掩码都没有）**。"""
    row = mcp_credentials.user_credential_row(db, plugin_id=plugin_id, human_no=human_no)
    info = mcp_credentials.credential_state(row, warn_days=config.mcp_credential_warn_days())
    expires_at = info["expires_at"]
    return {
        "plugin_id": plugin_id,
        "name": name,
        "mcp_category": category,
        "state": info["state"],
        "days_left": info["days_left"],
        "expires_at": expires_at.isoformat() if expires_at is not None else None,
    }


@my_router.get("/credential-alerts")
def my_credential_alerts(
    account: models.Account = Depends(current_account), db: Session = Depends(get_db),
):
    """用户级 MCP 凭据的到期提醒（即将过期 / 已过期）。

    按人反查 ``McpUserCredential`` 并 join 插件取名称——**必须按人查**，因为员工侧
    的 ``GET /my/mcp/plugins`` 只列本人登记的个人 MCP，而按人签发、会过期的令牌
    大多挂在企业级共享 MCP 上（如兴证e家的 X-MCP-JWT），不按人反查就会漏报。
    """
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人 MCP 目录")
    warn_days = config.mcp_credential_warn_days()
    rows = db.scalars(select(models.McpUserCredential).where(
        models.McpUserCredential.human_no == account.human_employee_no,
    )).all()
    alerts: list[dict] = []
    for row in rows:
        plugin = db.get(models.Plugin, row.plugin_id)
        if plugin is None or plugin.status != "active":
            continue
        item = _credential_out(
            db, plugin_id=plugin.id, human_no=account.human_employee_no,
            name=plugin.name, category=plugin.mcp_category,
        )
        if item["state"] in {"expiring", "expired"}:
            alerts.append(item)
    # 已过期的排最前，其次按剩余天数升序。
    alerts.sort(key=lambda item: (item["state"] != "expired", item["days_left"] or 0))
    return {
        "warn_days": warn_days,
        "alerts": alerts,
        "expired_count": sum(1 for item in alerts if item["state"] == "expired"),
        "expiring_count": sum(1 for item in alerts if item["state"] == "expiring"),
    }


@my_router.get("/plugins")
def my_services(account: models.Account = Depends(current_account), db: Session = Depends(get_db)):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人 MCP 目录")
    services = mcp_onboarding.list_registered(db, owner_human_no=account.human_employee_no)
    for item in services:
        item["credential"] = _credential_out(
            db, plugin_id=item["plugin_id"], human_no=account.human_employee_no,
            name=item["name"], category=item.get("mcp_category"),
        )
    return {
        "services": services,
        "credential_encryption_available": mcp_credentials.encryption_available(),
    }


@my_router.post("/plugins", status_code=201)
def my_create(
    payload: CreateIn, account: models.Account = Depends(current_account), db: Session = Depends(get_db),
):
    """员工自助登记个人 MCP：范围锁定 personal，提交后等待管理员审核与发布。"""
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员不能以员工身份提交个人 MCP")
    row = mcp_onboarding.create_mcp_plugin(
        db, url=payload.url, header_name=payload.auth_header_name, api_key=payload.api_key,
        timeout_seconds=payload.timeout_seconds, name=payload.name, description=payload.description,
        category=payload.mcp_category, data_level=payload.data_level, scope="personal",
        tools=payload.tools, version=payload.version, submitter=account.human_employee_no,
        auto_publish=False, reviewer=account.username, server_info=payload.server_info,
        probe_meta={"latency_ms": payload.latency_ms},
    )
    return _service_out(db, row)


@my_router.post("/plugins/{plugin_id}/probe")
async def my_reprobe(
    plugin_id: str, account: models.Account = Depends(current_account), db: Session = Depends(get_db),
):
    if "user" not in (account.roles or []):
        raise HTTPException(status_code=403, detail="管理员账号不拥有个人 MCP 目录")
    plugin = _owned_url_plugin(db, plugin_id, account.human_employee_no)
    try:
        return await asyncio.to_thread(mcp_onboarding.refresh_probe, db, plugin)
    except ExternalMcpError as exc:
        raise HTTPException(status_code=502, detail=f"连接 MCP 服务失败：{exc}") from exc
