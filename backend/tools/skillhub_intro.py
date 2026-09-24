"""回填 SkillHub 技能的中文简介与业务分类（运维 CLI）。

为什么要有这个工具：导入时的简介生成可能被 ``--no-intro`` 关掉、也可能当时模型
不可用而降级成英文原文首句。这里可以事后按需重跑，**只处理不含中文或超过 200 字
的简介**，中文简介质量好的技能不动。

用法（在 ``backend/`` 目录下）::

    # 看看哪些技能需要补（不落库）
    python -m tools.skillhub_intro --dry-run

    # 补全部需要的
    python -m tools.skillhub_intro

    # 只补指定的几个（常用来回填早期导入的 docx / pdf / xlsx）
    python -m tools.skillhub_intro --slug docx --slug pdf --slug xlsx

    # 强制重生成（忽略「已经有中文简介」的判断）
    python -m tools.skillhub_intro --slug docx --force

    # 只补分类、不改简介（简介本身没问题的技能走这个，避免把好简介重写坏）
    python -m tools.skillhub_intro --fill-category

幂等：生成结果与当前值一致时不写库；模型不可用时降级为原文首句并打印降级原因。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from sqlalchemy import select

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.services import config, llm, skill_intro, skillhub_client  # noqa: E402


def _candidates(db, slugs: list[str], limit: int) -> list[tuple[models.Plugin, models.SkillHubImport]]:
    """候选 = 已成功导入的 SkillHub 技能（``skillhub_import`` 是来源判据）。"""
    query = select(models.SkillHubImport).where(
        models.SkillHubImport.status == "imported",
        models.SkillHubImport.plugin_id.is_not(None),
    )
    if slugs:
        query = query.where(models.SkillHubImport.slug.in_(slugs))
    query = query.order_by(models.SkillHubImport.id)
    if limit and limit > 0:
        query = query.limit(limit)
    pairs: list[tuple[models.Plugin, models.SkillHubImport]] = []
    seen: set[str] = set()
    for record in db.scalars(query).all():
        plugin_id = record.plugin_id or ""
        if not plugin_id or plugin_id in seen:
            continue
        plugin = db.get(models.Plugin, plugin_id)
        if plugin is None or plugin.plugin_type != "skill":
            continue
        seen.add(plugin_id)
        pairs.append((plugin, record))
    return pairs


def _source_of(record: models.SkillHubImport, plugin: models.Plugin, db) -> dict:
    """生成用的原始素材：远端目录项优先（有 tags/department），失败回落版本 manifest。"""
    summary = ""
    department = ""
    tags: tuple[str, ...] = ()
    if config.skillhub_enabled():
        try:
            skill = skillhub_client.get_skill(record.slug)
            summary, department, tags = skill.summary, skill.department, skill.tags
        except skillhub_client.SkillHubError as exc:
            print(f"    （远端详情不可用，回落本地版本信息：{exc}）")
    version = db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.version == (plugin.current_version or ""),
    )) or db.scalar(select(models.PluginVersion).where(
        models.PluginVersion.plugin_id == plugin.id,
        models.PluginVersion.publish_status == "published",
    ).order_by(models.PluginVersion.id.desc()))
    manifest = (version.manifest if version else {}) or {}
    return {
        "name": plugin.name,
        "slug": record.slug,
        "summary": summary or str(manifest.get("description") or plugin.description or ""),
        "department": department,
        "tags": tags,
        "instruction": str(manifest.get("instruction") or ""),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="回填 SkillHub 技能的中文简介")
    parser.add_argument("--slug", action="append", default=[], help="只处理指定 slug（可重复）")
    parser.add_argument("--limit", type=int, default=0, help="最多处理多少个（0 表示不限）")
    parser.add_argument("--force", action="store_true", help="忽略「已有中文简介」的判断，强制重生成")
    parser.add_argument(
        "--fill-category", action="store_true",
        help="只给「还没有业务分类」的技能补分类，不改简介（简介本身没问题的技能走这个）",
    )
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调模型不落库")
    args = parser.parse_args()

    # 这个工具全靠模型，没密钥就全是「原文首句」降级，不如直接说清楚。
    if llm.ensure_api_key(allow_secret_file=True) == "missing":
        print(
            "[skillhub-intro] 未找到模型密钥：请设置 DEEPSEEK_API_KEY，"
            "或确认容器内已挂载 /run/secrets/llm_api_key"
        )
        return 2

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        pairs = _candidates(db, args.slug, args.limit)
        if not pairs:
            print("[skillhub-intro] 没有匹配的已导入技能")
            return 0
        if args.fill_category:
            targets = [(plugin, record) for plugin, record in pairs if not plugin.skill_category]
            print(f"[skillhub-intro] 候选 {len(pairs)} 个，需要补分类 {len(targets)} 个")
        else:
            targets = [
                (plugin, record) for plugin, record in pairs
                if args.force or skill_intro.needs_intro(plugin.description)
            ]
            print(f"[skillhub-intro] 候选 {len(pairs)} 个，需要补简介 {len(targets)} 个")
        if args.dry_run:
            for plugin, record in targets:
                print(f"  - {record.slug} | {plugin.skill_category or '未分类'} | {plugin.name}")
            return 0

        generated = degraded = skipped = 0
        for index, (plugin, record) in enumerate(targets, start=1):
            source = _source_of(record, plugin, db)
            draft = skill_intro.generate_draft(**source)
            changed = (
                skill_intro.apply_category_only(plugin, draft) if args.fill_category
                else skill_intro.apply_intro(plugin, draft)
            )
            db.commit()
            if not changed:
                skipped += 1
            elif draft.get("generated"):
                generated += 1
            else:
                degraded += 1
            mark = "AI 生成" if draft.get("generated") else f"降级（{draft.get('degraded_reason')}）"
            print(f"[{index}/{len(targets)}] {record.slug} [{draft.get('category') or '未分类'}] {mark}")
            if not args.fill_category:
                print(f"    {plugin.description[:120]}")
        print(
            f"[skillhub-intro] 完成：AI 生成 {generated}、降级 {degraded}、"
            f"无变化 {skipped}"
        )
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
