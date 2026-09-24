"""把内网 SkillHub 技能批量同步进平台货架（运维 CLI，可重跑、可续跑）。

为什么要有这个工具：管理端「技能市场」适合日常挑选安装，但首次接入需要把
131 个技能一次性灌进货架。逐条下载 → 转换 → 入库，串行执行、单条失败不影响
其他条目，结果逐条打印并落 ``skillhub_import`` 表。

用法（在 ``backend/`` 目录下）::

    # 看看会同步哪些（不落库；会打印分类与推荐位）
    python -m tools.skillhub_sync --dry-run

    # 只同步一个分类，最多 20 个
    python -m tools.skillhub_sync --category "AI大赛-财富管理赛道" --limit 20

    # 全量同步；中断后重跑会自动跳过已导入的
    python -m tools.skillhub_sync --resume

    # 只重试指定的几个
    python -m tools.skillhub_sync --slug docx --slug xlsx

    # 不调模型补中文简介（省额度，之后可单独回填）
    python -m tools.skillhub_sync --resume --no-intro

幂等：同一 ``(slug, version)`` 已成功导入时直接跳过（``--resume`` 时连远端目录
都不再查详情）。**失败可重跑**，不需要先清理数据。
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app import models  # noqa: E402
from app.database import Base, SessionLocal, engine  # noqa: E402
from app.services import config, llm, skillhub_client, skillhub_import  # noqa: E402


def _resolve_targets(args: argparse.Namespace) -> list[skillhub_client.SkillHubSkill]:
    """按命令行参数确定要同步的技能清单。"""
    if args.slug:
        # 目录里能查到的就用目录项（带 tags/version，分类与推荐位靠它推导）；
        # 目录不可达时退回裸 slug，导入流程自己会去取详情。
        catalog: dict[str, skillhub_client.SkillHubSkill] = {}
        try:
            catalog = {item.slug: item for item in skillhub_client.list_all_skills(page_size=args.page_size)}
        except skillhub_client.SkillHubError:
            catalog = {}
        return [
            catalog.get(slug) or skillhub_client.SkillHubSkill(
                slug=slug, display_name=slug, summary="", version="", source="",
            )
            for slug in args.slug
        ]
    skills = skillhub_client.list_all_skills(keyword=args.keyword, page_size=args.page_size)
    if args.category:
        wanted = args.category.strip()
        skills = [item for item in skills if item.category == wanted]
    return skills


def main() -> int:
    parser = argparse.ArgumentParser(description="批量同步 SkillHub 技能到平台货架")
    parser.add_argument("--slug", action="append", default=[], help="只同步指定 slug（可重复）")
    parser.add_argument("--category", default="", help="只同步该分类")
    parser.add_argument("--keyword", default="", help="按关键字过滤（服务端支持）")
    parser.add_argument("--limit", type=int, default=0, help="最多同步多少个（0 表示不限）")
    parser.add_argument("--resume", action="store_true", help="跳过已成功导入的技能")
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不下载不落库")
    parser.add_argument("--actor", default="SYS-ADMIN", help="导入者（写入导入记录与审计）")
    parser.add_argument("--page-size", type=int, default=100, help="目录翻页大小")
    parser.add_argument(
        "--no-intro", action="store_true",
        help="不对英文/超长简介调用模型补中文简介（之后可用 tools.skillhub_intro 回填）",
    )
    parser.add_argument(
        "--refresh-meta", action="store_true",
        help="只按目录 tags 回填已导入技能的业务分类与推荐位（不下载、不覆盖人工标注的分类）",
    )
    args = parser.parse_args()

    if not config.skillhub_enabled():
        print("[skillhub-sync] 未配置 DWP_SKILLHUB_BASE_URL（或已用 DWP_SKILLHUB_ENABLED=0 关闭），退出")
        return 2

    Base.metadata.create_all(engine)
    db = SessionLocal()
    try:
        try:
            targets = _resolve_targets(args)
        except skillhub_client.SkillHubError as exc:
            print(f"[skillhub-sync] 拉取 SkillHub 目录失败：{exc}")
            return 2
        if args.limit and args.limit > 0:
            targets = targets[:args.limit]
        if not targets:
            print("[skillhub-sync] 没有匹配的技能")
            return 0

        imported = skillhub_import.imported_slugs(db)
        if args.refresh_meta:
            return _refresh_meta(db, targets, imported)
        if args.resume:
            targets = [item for item in targets if item.slug not in imported]
            print(f"[skillhub-sync] --resume 跳过已导入 {len(imported)} 个，剩余 {len(targets)} 个")
        if not targets:
            print("[skillhub-sync] 全部已导入，无需同步")
            return 0

        if args.dry_run:
            print(f"[skillhub-sync] 计划同步 {len(targets)} 个技能：")
            for item in targets:
                mark = "已导入" if item.slug in imported else "待导入"
                category = item.category or "未分类"
                star = "★精选 " if skillhub_client.is_featured_tags(item.tags) else ""
                print(f"  - {item.slug}@{item.version or '?'} [{mark}] [{category}] {star}{item.display_name}")
            return 0

        generate_intro = not args.no_intro
        if generate_intro and llm.ensure_api_key(allow_secret_file=True) == "missing":
            # 裸跑 CLI 拿不到 entrypoint 注入的环境变量，兜底读挂载的密钥文件；都没有就明确告知，
            # 而不是让每条技能都悄悄降级成原文首句。
            print(
                "[skillhub-sync] 未找到模型密钥（DEEPSEEK_API_KEY 或 /run/secrets/llm_api_key），"
                "本次不生成中文简介；可用 --no-intro 显式跳过这条提示"
            )
            generate_intro = False
        summary = {"imported": 0, "skipped": 0, "failed": 0}
        for index, item in enumerate(targets, start=1):
            # 走 import_slug：它会自己取详情，取不到也能落一条 failed 记录。
            result = skillhub_import.import_slug(
                db, slug=item.slug, actor=args.actor, generate_intro=generate_intro,
            )
            summary[result["status"]] = summary.get(result["status"], 0) + 1
            label = {"imported": "导入成功", "skipped": "跳过", "failed": "失败"}[result["status"]]
            detail = f" -> {result['plugin_id']}" if result["status"] == "imported" else f" ({result['message']})"
            print(f"[{index}/{len(targets)}] {item.slug}@{item.version or '?'} {label}{detail}")

        print(
            f"[skillhub-sync] 完成：成功 {summary['imported']}、跳过 {summary['skipped']}、"
            f"失败 {summary['failed']}（失败项可直接重跑本命令重试）"
        )
        return 0 if summary["failed"] == 0 else 1
    finally:
        db.close()


def _refresh_meta(db, targets, imported: dict[str, dict]) -> int:
    """按目录 tags 回填已导入技能的分类与推荐位。

    为什么需要单独一个开关：远端**详情接口不返回 tags**，早期版本据此导入的技能
    分类会整片为空；tags 也是唯一能拿到分类的线索（``category`` 字段恒为 null）。
    这里只补「还没有分类」的行，绝不覆盖管理端人工标注的结果。
    """
    updated = 0
    for index, item in enumerate(targets, start=1):
        record = imported.get(item.slug)
        if record is None or not record.get("plugin_id"):
            print(f"[{index}/{len(targets)}] {item.slug} 尚未导入，跳过")
            continue
        plugin = db.get(models.Plugin, record["plugin_id"])
        if plugin is None:
            print(f"[{index}/{len(targets)}] {item.slug} 插件行已不存在，跳过")
            continue
        category = item.category or skillhub_client.skill_category_from_tags(item.tags)
        changed: list[str] = []
        if category and not plugin.skill_category:
            plugin.skill_category = category
            changed.append(f"分类={category}")
        if skillhub_client.is_featured_tags(item.tags) and not plugin.featured:
            plugin.featured = True
            changed.append("推荐位=精选")
        if changed:
            updated += 1
        print(f"[{index}/{len(targets)}] {item.slug} {'、'.join(changed) if changed else '无需变更'}")
    db.commit()
    print(f"[skillhub-sync] 元数据回填完成：更新 {updated} 个")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
