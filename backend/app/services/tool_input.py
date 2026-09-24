"""MCP 工具「缺参 / 缺鉴权」识别与待补全事实来源。

背景：调用外部 MCP 工具时经常缺两类信息——**凭据类**（应用 key、用户级 JWT/token）
与**参数类**（日期、关键字、单号等模型没识别出的必填业务参数）。平台的做法是
「非阻塞识别 + 前端气泡补全 + 提交后自动续跑」：

1. **事前预检**（首选，省一次失败）：读适配器声明的 ``required_inputs`` 与
   ``mcp_user_credential``，缺失/过期直接产出待补全，不发无效请求；
2. **事后识别**：外部 MCP 返回的错误文本按规则匹配（``Missing required header``、
   ``缺少请求参数``、``keyword 必填``…），映射成字段；
3. 命中后落一条 ``tool_input_request`` 并 emit ``input_required`` 事件，前端据此渲染气泡。

安全：本模块只处理字段名与掩码，**明文凭据永不落库、不进事件 payload、不进 prompt**。
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from . import execution_events, mcp_credentials

logger = logging.getLogger(__name__)

STATUS_PENDING = "pending"
STATUS_SUBMITTED = "submitted"
STATUS_EXPIRED = "expired"
STATUS_CANCELLED = "cancelled"

KIND_SECRET = "secret"
KIND_TEXT = "text"

SOURCE_APP_SECRET = "app_secret"
SOURCE_USER_SECRET = "user_secret"

REASON_MISSING = "missing"
REASON_EXPIRED = "expired"
REASON_TOOL_ERROR = "tool_error"

EVENT_TYPE_INPUT_REQUIRED = "input_required"
EVENT_TITLE_INPUT_REQUIRED = "需要你补充信息"

# 事后识别规则：外部 MCP 的错误文本 → 缺失字段名。
_MISSING_HEADER_RE = re.compile(
    r"missing\s+required\s+header\s*[:：]?\s*['\"]?([A-Za-z0-9\-_]+)", re.IGNORECASE,
)
_MISSING_PARAM_RE = re.compile(
    r"(?:缺少|缺失|未提供|missing)\s*(?:请求)?(?:参数|字段|必填项|field|parameter)\s*[:：]?\s*['\"]?([A-Za-z0-9_\-]+)",
    re.IGNORECASE,
)
_REQUIRED_HINT_RE = re.compile(r"([A-Za-z0-9_\-]+)\s*(?:是|为)?\s*(?:必填|必传|required)", re.IGNORECASE)
# 兴证e家真实返回：schema 未标注必填的业务参数缺失时，服务端抛的是 Java 空指针文案，
# 形如 ``Cannot invoke "String.split(String)" because "keyword" is null``。
# 这条不识别就会漏掉「keyword 实际必填」这个已知坑（2026-09-22 真机实测）。
_NULL_ARG_RE = re.compile(
    r'because\s+"([A-Za-z_][A-Za-z0-9_.\-]*)"\s+is\s+null|"([A-Za-z_][A-Za-z0-9_.\-]*)"\s+(?:不能为空|is\s+null)',
    re.IGNORECASE,
)


def adapter_required_inputs(adapter: Any) -> list[dict[str, Any]]:
    """读取适配器声明的必填输入（适配器未实现该方法时返回空列表）。"""
    fn = getattr(adapter, "required_inputs", None)
    if not callable(fn):
        return []
    try:
        return [item for item in (fn() or []) if isinstance(item, dict) and item.get("key")]
    except Exception:  # noqa: BLE001 - 声明异常不应影响工具调用
        return []


def _declared_field(entry: dict[str, Any], *, masked: str = "", reason: str = REASON_MISSING) -> dict[str, Any]:
    """把适配器声明转成前端可直接渲染的字段描述（只含字段名与掩码）。"""
    return {
        "key": str(entry.get("key") or ""),
        "label": str(entry.get("label") or entry.get("key") or ""),
        "kind": str(entry.get("kind") or KIND_TEXT),
        "required": bool(entry.get("required", True)),
        "hint": str(entry.get("hint") or ""),
        "source": str(entry.get("source") or ""),
        "masked": masked,
        "reason": reason,
    }


def _user_secret_entries(adapter: Any) -> list[dict[str, Any]]:
    return [
        entry for entry in adapter_required_inputs(adapter)
        if str(entry.get("source") or "") == SOURCE_USER_SECRET
    ]


def _entry_applies_to_tool(entry: dict[str, Any], raw_tool_name: str) -> bool:
    """该声明是否适用于这个工具（``exclude_tools`` 里的工具不需要用户级凭据）。"""
    excluded = entry.get("exclude_tools") or []
    if raw_tool_name and raw_tool_name in {str(item) for item in excluded}:
        return False
    return True


def detect_missing_input(
    db: Session, adapter: Any, requester_human_no: str,
    raw_tool_name: str, arguments: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """事前预检：返回缺失的字段描述列表（空列表表示信息齐备）。

    只检查**用户级凭据**：应用级凭据缺失时适配器根本不会出现在工具清单里
    （``configured()`` 为假），也不该让员工去填运维的密钥。
    """
    fields: list[dict[str, Any]] = []
    plugin_id = str(getattr(adapter, "plugin_id", "") or "")
    if not plugin_id:
        return fields
    for entry in _user_secret_entries(adapter):
        if not _entry_applies_to_tool(entry, raw_tool_name):
            continue
        key = str(entry.get("key") or "")
        if not requester_human_no:
            fields.append(_declared_field(entry, reason=REASON_MISSING))
            continue
        row = mcp_credentials.user_credential_row(db, plugin_id=plugin_id, human_no=requester_human_no)
        if row is None:
            fields.append(_declared_field(entry, reason=REASON_MISSING))
            continue
        if mcp_credentials.user_credential_expired(row):
            fields.append(_declared_field(entry, masked=row.secret_hint, reason=REASON_EXPIRED))
            continue
        logger.debug("tool_input 预检通过 plugin=%s tool=%s key=%s", plugin_id, raw_tool_name, key)
    return fields


def detect_missing_from_text(text: str, adapter: Any) -> list[dict[str, Any]]:
    """事后识别：把外部 MCP 的错误文本映射成缺失字段（无法识别时返回空列表）。"""
    value = (text or "").strip()
    if not value:
        return []
    declared = adapter_required_inputs(adapter)
    declared_by_key = {str(entry.get("key") or "").lower(): entry for entry in declared}
    found: list[dict[str, Any]] = []
    seen: set[str] = set()

    def _push(key: str) -> None:
        if not key or key.lower() in seen:
            return
        seen.add(key.lower())
        entry = declared_by_key.get(key.lower())
        if entry is not None:
            found.append(_declared_field(entry, reason=REASON_TOOL_ERROR))
        else:
            # 未声明的字段（业务参数，如 keyword）→ 交给员工在气泡里补文本值。
            found.append({
                "key": key,
                "label": key,
                "kind": KIND_TEXT,
                "required": True,
                "hint": "该字段由服务端要求必填，请补充后继续。",
                "source": "",
                "masked": "",
                "reason": REASON_TOOL_ERROR,
            })

    for match in _MISSING_HEADER_RE.finditer(value):
        _push(match.group(1))
    for match in _MISSING_PARAM_RE.finditer(value):
        _push(match.group(1))
    if not found and re.search(r"keyword[^\n]{0,24}(必填|必传|required)", value, re.IGNORECASE):
        _push("keyword")
    if not found:
        for match in _NULL_ARG_RE.finditer(value):
            _push(match.group(1) or match.group(2) or "")
    if not found:
        for match in _REQUIRED_HINT_RE.finditer(value):
            _push(match.group(1))
    return found


def _adapter_api_key(adapter: Any) -> str:
    """适配器配置来源的应用级凭据（内置源来自 env / secrets 文件）。"""
    fn = getattr(adapter, "configured_api_key", None)
    if not callable(fn):
        return ""
    try:
        return str(fn() or "").strip()
    except Exception:  # noqa: BLE001 - 配置读取异常不应影响工具调用
        return ""


def build_auth_headers(
    db: Session, adapter: Any, requester_human_no: str,
) -> dict[str, str] | None:
    """构造本次调用的鉴权头；``None`` 表示该适配器不需要按人鉴权（走静态客户端）。

    应用级凭据优先取数据库 ``mcp_credential``（自助登记的服务），回落到适配器配置
    （内置源的 env / secrets 文件）；用户级凭据取 ``mcp_user_credential``（按人）。
    用户级凭据缺失/过期时**不放进请求头**（由预检负责提前拦下）。
    """
    entries = adapter_required_inputs(adapter)
    if not entries:
        return None
    plugin_id = str(getattr(adapter, "plugin_id", "") or "")
    headers: dict[str, str] = {}
    for entry in entries:
        source = str(entry.get("source") or "")
        key = str(entry.get("key") or "")
        if not key:
            continue
        if source == SOURCE_APP_SECRET:
            value = mcp_credentials.load_secret(db, plugin_id) or _adapter_api_key(adapter)
        elif source == SOURCE_USER_SECRET:
            if not requester_human_no:
                continue
            row = mcp_credentials.user_credential_row(db, plugin_id=plugin_id, human_no=requester_human_no)
            if row is None or mcp_credentials.user_credential_expired(row):
                continue
            value = mcp_credentials.load_user_credential(db, plugin_id=plugin_id, human_no=requester_human_no)
        else:
            continue
        if value:
            headers[key] = value
    return headers or None


def _field_signature(fields: list[dict[str, Any]]) -> str:
    return ",".join(sorted(str(field.get("key") or "").lower() for field in fields))


def create_request(
    db: Session, *, conversation_id: str, execution_id: str, trace_id: str,
    plugin_id: str, tool_name: str, fields: list[dict[str, Any]], reason: str,
) -> models.ToolInputRequest:
    """落一条待补全记录（同一执行内同工具同字段复用 pending 记录，避免重复气泡）。"""
    signature = _field_signature(fields)
    existing = db.scalars(
        select(models.ToolInputRequest).where(
            models.ToolInputRequest.execution_id == execution_id,
            models.ToolInputRequest.plugin_id == plugin_id,
            models.ToolInputRequest.tool_name == tool_name,
            models.ToolInputRequest.status == STATUS_PENDING,
        )
    ).all()
    for row in existing:
        if _field_signature(list(row.fields or [])) == signature:
            row.fields = fields
            row.reason = reason
            db.add(row)
            db.flush()
            return row
    row = models.ToolInputRequest(
        id=f"TI-{uuid4().hex[:12].upper()}",
        conversation_id=conversation_id,
        execution_id=execution_id,
        trace_id=trace_id,
        plugin_id=plugin_id,
        tool_name=tool_name,
        fields=fields,
        reason=reason,
        status=STATUS_PENDING,
    )
    db.add(row)
    db.flush()
    return row


def emit_input_required(
    db: Session, *, execution_id: str, request: models.ToolInputRequest, actor_employee_id: str,
    source_label: str = "",
) -> None:
    """把待补全落成执行轨迹里的一步（前端据此弹补全气泡）。"""
    keys = "、".join(str(field.get("key") or "") for field in (request.fields or []))
    label = source_label or request.plugin_id
    try:
        execution_events.emit(
            db, execution_id, event_type=EVENT_TYPE_INPUT_REQUIRED, stage="input_required",
            status="running", actor_employee_id=actor_employee_id,
            title=EVENT_TITLE_INPUT_REQUIRED,
            detail=f"{label}需要你补充：{keys}",
            payload={
                "input_request_id": request.id,
                "plugin_id": request.plugin_id,
                "source_label": label,
                "tool_name": request.tool_name,
                "reason": request.reason,
                "fields": request.fields or [],
            },
        )
    except RuntimeError:
        # 执行已取消/不存在：气泡仍可展示（记录已落库），轨迹步骤不写即可。
        logger.info("input_required 事件未写入（执行已结束）request=%s", request.id)


def get_request(db: Session, request_id: str) -> models.ToolInputRequest | None:
    return db.get(models.ToolInputRequest, request_id)


def list_for_execution(db: Session, execution_id: str) -> list[models.ToolInputRequest]:
    return list(db.scalars(
        select(models.ToolInputRequest)
        .where(models.ToolInputRequest.execution_id == execution_id)
        .order_by(models.ToolInputRequest.created_at, models.ToolInputRequest.id)
    ).all())


def list_for_conversation(db: Session, conversation_id: str, limit: int = 100) -> list[models.ToolInputRequest]:
    return list(db.scalars(
        select(models.ToolInputRequest)
        .where(models.ToolInputRequest.conversation_id == conversation_id)
        .order_by(models.ToolInputRequest.created_at.desc(), models.ToolInputRequest.id.desc())
        .limit(limit)
    ).all())


def mark_submitted(
    db: Session, request: models.ToolInputRequest, *,
    submitted_values: dict[str, str], resolved_by: str,
) -> models.ToolInputRequest:
    request.status = STATUS_SUBMITTED
    request.submitted_values = submitted_values
    request.resolved_at = datetime.now()
    request.resolved_by = resolved_by
    db.add(request)
    db.flush()
    return request


def mark_cancelled(
    db: Session, request: models.ToolInputRequest, *, resolved_by: str,
) -> models.ToolInputRequest:
    """用户主动忽略：标记 ``cancelled``。

    必须落库——只在前端本地记住「忽略过」的话，刷新页面气泡会重新冒出来，
    用户会以为自己的操作没生效。
    """
    request.status = STATUS_CANCELLED
    request.resolved_at = datetime.now()
    request.resolved_by = resolved_by
    db.add(request)
    db.flush()
    return request


def field_needs_value(field: dict[str, Any]) -> bool:
    """该字段是否必须由用户重新填写。

    非必填不算；已保存过凭据且未过期的也不算（前端显示「留空则沿用已保存的凭据」）。
    """
    if not field.get("required", True):
        return False
    if str(field.get("reason") or "") == REASON_EXPIRED:
        return True
    return not str(field.get("masked") or "").strip()


def masked_values(fields: list[dict[str, Any]], values: dict[str, str]) -> dict[str, str]:
    """生成可落库的 ``submitted_values``：凭据类只留掩码，参数类保留原值。"""
    out: dict[str, str] = {}
    for field in fields or []:
        key = str(field.get("key") or "")
        if not key:
            continue
        value = (values.get(key) or "").strip()
        if not value:
            continue
        if str(field.get("kind") or "") == KIND_SECRET:
            out[key] = mcp_credentials.mask(value)
        else:
            out[key] = value[:200]
    return out


def build_resume_content(original: str, fields: list[dict[str, Any]], values: dict[str, str]) -> str:
    """拼「原提问 + 补充说明」的续跑内容；敏感值只写「已提供」，不写明文。"""
    parts: list[str] = []
    for field in fields or []:
        key = str(field.get("key") or "")
        label = str(field.get("label") or key)
        if not key:
            continue
        if str(field.get("kind") or "") == KIND_SECRET:
            parts.append(f"{label}（{key}）已提供并安全保存")
        else:
            value = (values.get(key) or "").strip()
            if value:
                parts.append(f"{label}（{key}）为 {value}")
    if not parts:
        return original
    note = "；".join(parts)
    return f"{original}\n\n[补充信息] {note}。请据此重新执行上面的任务，不要再次索要已提供的内容。"
