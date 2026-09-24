"""管理端「技能市场」：浏览内网 SkillHub 目录并把技能导入平台货架。

设计口径：

* 这里是**代理 + 导入**，不新建技能事实来源。导入走 ``skillhub_import`` →
  既有 ``submit_zip → publish_version`` 落库三件套，导入后技能即出现在员工侧
  技能市场（``GET /my/plugins``），员工申请、管理员审批后授权到 AI员工/分身。
* 目录与分类由 ``skillhub_client`` 带 TTL 缓存，管理端反复开页不会打爆远端。
* 导入是**幂等**的：同一 ``(slug, version)`` 重复导入直接返回 ``skipped``；
  失败原因逐条回传，便于前端做「成功 / 跳过 / 失败原因」的结果明细。
"""

from __future__ import annotations

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from .. import models, schemas
from ..database import get_db
from ..services import config, skillhub_client, skillhub_import
from ..services.auth import current_account, require_roles

admin_router = APIRouter(prefix="/admin/skillhub", tags=["skillhub"])
admin_required = require_roles("agent_admin", "security_admin", "platform_admin")
MAX_BATCH_SLUGS = 200


class SkillHubImportIn(BaseModel):
    slugs: list[str] = Field(min_length=1, max_length=MAX_BATCH_SLUGS)
    # 是否对英文/超长简介调用模型补中文简介；模型额度紧张时可关掉。
    generate_intro: bool = True


def _ensure_enabled() -> None:
    if not config.skillhub_enabled():
        raise HTTPException(status_code=503, detail={
            "reason": "skillhub_not_configured",
            "message": "SkillHub 未接入：请配置 DWP_SKILLHUB_BASE_URL（或检查 DWP_SKILLHUB_ENABLED）",
        })


def _skill_out(skill: skillhub_client.SkillHubSkill, imported: dict[str, dict]) -> dict:
    payload = skill.to_dict()
    record = imported.get(skill.slug)
    payload["imported"] = record is not None
    payload["imported_version"] = (record or {}).get("version")
    payload["imported_plugin_id"] = (record or {}).get("plugin_id")
    return payload


@admin_router.get("/overview", response_model=schemas.SkillHubOverviewOut)
def overview(_: models.Account = Depends(admin_required)) -> dict:
    """SkillHub 接入状态 + 分类清单（供市场页首屏渲染）。"""
    if not config.skillhub_enabled():
        return {
            "enabled": False, "base_url": config.skillhub_base_url(),
            "categories": [], "message": "未配置 DWP_SKILLHUB_BASE_URL，技能市场不可用",
        }
    try:
        categories = skillhub_client.list_categories()
    except skillhub_client.SkillHubError as exc:
        return {
            "enabled": False, "base_url": config.skillhub_base_url(),
            "categories": [], "message": str(exc),
        }
    return {"enabled": True, "base_url": config.skillhub_base_url(), "categories": categories, "message": ""}


@admin_router.get("/skills", response_model=schemas.SkillHubSkillPageOut)
def list_skills(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=24, ge=1, le=100),
    keyword: str | None = Query(default=None),
    category: str | None = Query(default=None),
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
) -> dict:
    """SkillHub 目录（支持关键字与分类筛选）。

    SkillHub 服务端只支持 ``keyword``，因此分类筛选在平台侧完成：分类命中时
    取全量目录（带缓存）再分页，未指定分类时直接用服务端分页。
    """
    _ensure_enabled()
    imported = skillhub_import.imported_slugs(db)
    wanted = (category or "").strip()
    try:
        if wanted:
            all_skills = skillhub_client.list_all_skills(keyword=keyword)
            matched = [item for item in all_skills if item.category == wanted]
            total = len(matched)
            start = (page - 1) * page_size
            skills = matched[start:start + page_size]
        else:
            skills, total = skillhub_client.list_skills(page=page, page_size=page_size, keyword=keyword)
    except skillhub_client.SkillHubError as exc:
        raise HTTPException(status_code=502, detail={
            "reason": "skillhub_unreachable", "message": str(exc),
        }) from exc
    return {
        "items": [_skill_out(item, imported) for item in skills],
        "total": total, "page": page, "page_size": page_size,
        "imported_total": len(imported),
    }


@admin_router.get("/skills/{slug}", response_model=schemas.SkillHubSkillOut)
def skill_detail(
    slug: str,
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
) -> dict:
    _ensure_enabled()
    try:
        skill = skillhub_client.get_skill(slug)
    except skillhub_client.SkillHubError as exc:
        raise HTTPException(status_code=502, detail={
            "reason": "skillhub_unreachable", "message": str(exc),
        }) from exc
    return _skill_out(skill, skillhub_import.imported_slugs(db))


@admin_router.post("/import", response_model=schemas.SkillHubImportBatchStartOut)
def import_skills(
    payload: SkillHubImportIn,
    background_tasks: BackgroundTasks,
    account: models.Account = Depends(admin_required),
) -> dict:
    """批量导入（单个也走这里）：立即返回批次号，实际导入在后台串行执行。

    为什么不能同步做：nginx ``proxy_read_timeout`` 是 300s，而 131 个技能光下载+
    转换就要数分钟，再加简介生成必然 504。进度查 ``GET /imports/{batch_id}``。
    """
    _ensure_enabled()
    slugs: list[str] = []
    for raw in payload.slugs:
        slug = (raw or "").strip()
        if slug and slug not in slugs:
            slugs.append(slug)
    if not slugs:
        raise HTTPException(status_code=422, detail="请至少提供一个技能标识 slug")

    actor = account.username or f"account-{account.id}"
    batch_id = skillhub_import.start_batch(slugs)
    # 后台任务必须自己开 SessionLocal：请求结束后这里的 db 已经关闭。
    background_tasks.add_task(
        skillhub_import.run_batch, slugs,
        actor=actor, batch_id=batch_id, generate_intro=payload.generate_intro,
    )
    return {"batch_id": batch_id, "total": len(slugs), "status": "running"}


@admin_router.get("/imports", response_model=schemas.SkillHubImportListOut)
def list_imports(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
) -> dict:
    rows = skillhub_import.list_imports(db, status=status, limit=limit)
    return {"items": [skillhub_import.record_out(row) for row in rows]}


@admin_router.get("/imports/{batch_id}", response_model=schemas.SkillHubImportProgressOut)
def import_batch_progress(
    batch_id: str,
    _: models.Account = Depends(admin_required),
    db: Session = Depends(get_db),
) -> dict:
    """批次进度（前端轮询）：状态、已完成条数与逐条明细。"""
    return skillhub_import.batch_progress(db, batch_id)


@admin_router.post("/cache/refresh", response_model=schemas.SkillHubRefreshOut)
def refresh_cache(_: models.Account = Depends(admin_required)) -> dict:
    """清空目录/分类缓存，让市场页立刻看到远端最新内容。"""
    skillhub_client.invalidate_cache()
    return {"refreshed": True}
