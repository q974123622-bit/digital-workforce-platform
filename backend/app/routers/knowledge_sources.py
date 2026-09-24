"""管理端知识源治理与知识库独占分配。

两个能力：
1. 数据源状态与手动重测 —— 定时探测之外，管理员可一键重测（只测知识通道，
   或连同已登记的外部 MCP 插件一起测）；探测结果落库并驱动「不可达即摘除工具」。
2. 知识库独占分配 —— 补齐此前完全缺失的管理端写链路（``AgentKnowledgeGrant``
   过去只有 seed 会写）。分配冲突返回 409 并给出被谁占用。
"""

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import knowledge_grant, knowledge_market
from ..services import knowledge_source_health as health
from ..services import knowledge_source_registry as registry
from ..services.auth import current_account, require_roles
from .knowledge import kb_to_out

admin_router = APIRouter(prefix="/admin", tags=["knowledge-governance"])
admin_required = require_roles("agent_admin", "security_admin", "platform_admin")

my_router = APIRouter(prefix="/my", tags=["knowledge-market"])


def _source_label(db: Session, source: str) -> str:
    if source in registry.known_sources():
        return registry.source_label(source)
    plugin_id = source.split(":", 1)[-1]
    plugin = db.get(models.Plugin, plugin_id)
    return plugin.name if plugin is not None else source


def _source_out(db: Session, source: str) -> schemas.KnowledgeSourceStatusOut:
    row = health.get_health(db, source)
    configured = (
        registry.source_configured(source) if source in registry.known_sources() else True
    )
    state = (row.state if row is not None else health.STATE_UNKNOWN) or health.STATE_UNKNOWN
    if not configured:
        # 未配置的通道视作不可用：连不上就是连不上，不该在界面上显示成「未知」。
        state = health.STATE_UNAVAILABLE
    return schemas.KnowledgeSourceStatusOut(
        source=source,
        label=_source_label(db, source),
        environment=registry.source_environment(source),
        environment_label=registry.environment_label(registry.source_environment(source)),
        kind=(registry.SOURCE_META.get(source) or {}).get("kind", "mcp"),
        configured=configured,
        state=state,
        available=state != health.STATE_UNAVAILABLE,
        latency_ms=row.latency_ms if row is not None else 0,
        last_error=row.last_error if row is not None else "",
        detail=(row.detail if row is not None else {}) or {},
        checked_at=row.checked_at if row is not None else None,
    )


@admin_router.get("/knowledge-sources", response_model=list[schemas.KnowledgeSourceStatusOut])
def list_knowledge_sources(
    db: Session = Depends(get_db),
    _: models.Account = Depends(admin_required),
):
    """全部知识源与已登记外部 MCP 的连通状态（含最近检测时间与耗时）。"""
    return [_source_out(db, source) for source in health.registered_sources(db)]


@admin_router.post("/knowledge-sources/probe", response_model=list[schemas.KnowledgeSourceStatusOut])
def probe_knowledge_sources(
    payload: schemas.KnowledgeSourceProbeIn,
    db: Session = Depends(get_db),
    _: models.Account = Depends(admin_required),
):
    """手动重测：指定 ``source`` 测单个源，否则按 ``scope`` 测一批。"""
    if payload.source:
        health.refresh_source(db, payload.source)
    else:
        health.refresh_all(db, include_mcp=payload.scope == "all")
    return [_source_out(db, source) for source in health.registered_sources(db)]


@admin_router.get("/knowledge-grants", response_model=schemas.KnowledgeGrantMatrixOut)
def get_knowledge_grants(
    db: Session = Depends(get_db),
    _: models.Account = Depends(admin_required),
):
    """知识库独占分配矩阵（员工 × 知识库 + 占用关系）。"""
    return _matrix_out(db)


@admin_router.put(
    "/knowledge-grants/{employee_id}", response_model=schemas.KnowledgeGrantMatrixOut,
)
def replace_knowledge_grants(
    employee_id: str,
    payload: schemas.KnowledgeGrantReplaceIn,
    db: Session = Depends(get_db),
    _: models.Account = Depends(admin_required),
):
    """整体替换某个 AI 员工的知识库授权集合；与其他员工冲突时 409 拒绝。"""
    try:
        knowledge_grant.replace_grants(
            db, employee_id=employee_id, knowledge_base_ids=payload.knowledge_base_ids,
        )
    except knowledge_grant.KnowledgeGrantConflict as exc:
        raise HTTPException(
            status_code=409,
            detail={
                "message": str(exc),
                "reason": "knowledge_grant_conflict",
                "conflicts": exc.conflicts,
            },
        ) from exc
    except knowledge_grant.KnowledgeGrantInvalid as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return _matrix_out(db)


@my_router.get("/knowledge-bases", response_model=schemas.KnowledgeMarketOut)
def my_knowledge_bases(
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """知识库市场货架：全部知识库 + 本人分身的当前状态（可申请/申请中/已授权/被占用）。

    全可见是刻意的：被他人独占的库照常展示并标注占用方，员工才知道「库存在但不能用」，
    而不是误以为平台没有这个库。
    """
    items = knowledge_market.market_knowledge_bases(db, account.human_employee_no)
    return schemas.KnowledgeMarketOut(
        items=[schemas.KnowledgeMarketItemOut(**item) for item in items],
    )


@my_router.post("/knowledge-bases/{knowledge_base_id}/request", response_model=schemas.AccessRequestOut)
def request_knowledge_base(
    knowledge_base_id: str,
    payload: schemas.KnowledgeRequestIn,
    account: models.Account = Depends(current_account),
    db: Session = Depends(get_db),
):
    """申请使用某个知识库；审批通过后由管理员侧写入独占授权。"""
    request = knowledge_market.request_knowledge_base(
        db, requester_human_no=account.human_employee_no,
        knowledge_base_id=knowledge_base_id, reason=payload.reason,
    )
    return schemas.AccessRequestOut(
        id=request.id, applicant_no=request.applicant_no, resource_type=request.resource_type,
        resource_id=request.resource_id, reason=request.reason, status=request.status,
        decided_by=request.decided_by, decided_at=request.decided_at,
        created_at=request.created_at,
    )


def _matrix_out(db: Session) -> schemas.KnowledgeGrantMatrixOut:
    data = knowledge_grant.matrix(db)
    return schemas.KnowledgeGrantMatrixOut(
        employees=[schemas.KnowledgeGrantItemOut(**item) for item in data["employees"]],
        knowledge_bases=[kb_to_out(db, kb) for kb in data["knowledge_bases"]],
        occupancy=data["occupancy"],
    )
