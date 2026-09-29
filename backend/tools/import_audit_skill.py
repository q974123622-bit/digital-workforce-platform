"""把「机构开户材料交叉核对」技能包导入插件中心，并授权给 AI 审核员工。

平台里技能的唯一载体是 ``Plugin(plugin_type='skill')`` + 已发布的 ``PluginVersion``；
本脚本把本地技能包走完 **提交 → 审核通过 → 发布 → 绑定 → 投放** 这条链，使技能
出现在插件中心（管理端「插件治理」/ 员工端「技能市场」），并在目标 AI 员工的
``/workspace/skills/<slug>/`` 里就位。

可重复执行：同 slug 的插件走**续版本**而不是新建；目标版本已存在则跳过提交；
绑定已存在则补齐授权位；投放本身幂等。同 slug 的历史重复插件用 ``--prune-duplicates``
下架并停用其绑定。

用法（容器内，WORKDIR ``/app/backend``）::

    python -m tools.import_audit_skill --zip /data/audit-skill-0.3.0.zip
    python -m tools.import_audit_skill --package /repo/extensions/skills/institution-account-material-review
    python -m tools.import_audit_skill --prune-duplicates
    python -m tools.import_audit_skill --dry-run
"""

from __future__ import annotations

import argparse
import io
import sys
import zipfile
from datetime import datetime
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import yaml  # noqa: E402
from sqlalchemy import select  # noqa: E402

from app import models  # noqa: E402
from app.database import SessionLocal  # noqa: E402
from app.services import plugin_governance, runtime_manager  # noqa: E402

DEFAULT_SLUG = "institution-account-material-review"
DEFAULT_EMPLOYEE = "AI-AUDIT"
DEFAULT_SUBMITTER = "E10281"
REVIEWER = "admin"
# 打包范围：只含运行时需要的文件。顶层 tests/（样例与 fixtures）与 __pycache__ 不进包，
# 否则制品体积被样例图片撑大，且 .pyc 是无意义垃圾。
TOP_FILES = ("plugin.yaml", "SKILL.md")
TOP_DIRS = ("scripts", "rules", "references")


def build_zip(package_dir: Path) -> bytes:
    """把技能包目录打成平台认的 zip（与 validate_zip 的口径一致）。"""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in TOP_FILES:
            path = package_dir / name
            if not path.is_file():
                raise SystemExit("技能包缺少 %s：%s" % (name, package_dir))
            archive.write(path, name)
        for top in TOP_DIRS:
            base = package_dir / top
            if not base.is_dir():
                continue
            for path in sorted(base.rglob("*")):
                if not path.is_file() or "__pycache__" in path.parts:
                    continue
                if path.suffix in {".pyc", ".pyo"}:
                    continue
                archive.write(path, path.relative_to(package_dir).as_posix())
    return buffer.getvalue()


def locate_package(explicit: str | None) -> Path:
    if explicit:
        return Path(explicit).expanduser().resolve()
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for base in [here, *here.parents]:
        candidates.append(base / "extensions" / "skills" / "institution-account-material-review")
        candidates.append(base / "08_落地蓝图" / "审核技能包")
        candidates.append(base / "audit" / "operation" / "08_落地蓝图" / "审核技能包")
    for candidate in candidates:
        if (candidate / "plugin.yaml").is_file():
            return candidate
    raise SystemExit("找不到技能包目录，请用 --package 指定，或用 --zip 直接给包")


def plugins_with_slug(db, slug: str) -> list[models.Plugin]:
    """所有 manifest.slug 匹配的技能插件（按 id 排序，最早的排在最前）。"""
    found: list[models.Plugin] = []
    for plugin in db.scalars(select(models.Plugin).where(
            models.Plugin.plugin_type == "skill")).all():
        versions = db.scalars(select(models.PluginVersion).where(
            models.PluginVersion.plugin_id == plugin.id)).all()
        if any(str((version.manifest or {}).get("slug") or "") == slug for version in versions):
            found.append(plugin)
    return sorted(found, key=lambda item: item.id)


def ensure_binding(db, plugin_id: str, employee: str, actor: str):
    """确保「插件 × AI 员工」绑定存在且三个授权位都是开。返回 (binding, 是否新建)。"""
    binding = db.scalar(select(models.AgentPluginBinding).where(
        models.AgentPluginBinding.plugin_id == plugin_id,
        models.AgentPluginBinding.target_agent_id == employee))
    if binding is not None:
        changed = False
        if not binding.admin_enabled:
            binding.admin_enabled = True
            changed = True
        if not binding.employee_enabled:
            binding.employee_enabled = True
            changed = True
        if binding.decision_mode != "allow":
            binding.decision_mode = "allow"
            changed = True
        if changed:
            binding.authorized_by = actor
            db.commit()
        return binding, False
    binding = models.AgentPluginBinding(
        plugin_id=plugin_id, target_agent_id=employee, authorized_by=actor,
        admin_enabled=True, employee_enabled=True, decision_mode="allow",
    )
    db.add(binding)
    db.commit()
    db.refresh(binding)
    return binding, True


def main() -> int:
    parser = argparse.ArgumentParser(description="导入审核技能包并授权给 AI 审核员工")
    parser.add_argument("--package", help="技能包目录（默认自动探测）")
    parser.add_argument("--zip", dest="zip_path", help="直接给一个打好的 zip（容器里常用 /data/xxx.zip）")
    parser.add_argument("--employee", default=DEFAULT_EMPLOYEE, help="目标 AI 员工工号")
    parser.add_argument("--submitter", default=DEFAULT_SUBMITTER, help="提交者工号（续版本鉴权用）")
    parser.add_argument("--slug", default=None, help="覆盖 plugin.yaml 里的 slug")
    parser.add_argument("--prune-duplicates", action="store_true",
                        help="同 slug 的重复插件下架并停用其绑定")
    parser.add_argument("--dry-run", action="store_true", help="只看现状，不写库")
    args = parser.parse_args()

    if args.zip_path:
        data = Path(args.zip_path).read_bytes()
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            manifest = yaml.safe_load(archive.read("plugin.yaml").decode("utf-8")) or {}
        source = args.zip_path
    else:
        package_dir = locate_package(args.package)
        data = build_zip(package_dir)
        manifest = yaml.safe_load((package_dir / "plugin.yaml").read_text(encoding="utf-8")) or {}
        source = str(package_dir)

    name = str(manifest.get("name") or "").strip()
    slug = (args.slug or str(manifest.get("slug") or "")).strip() or DEFAULT_SLUG
    version = str(manifest.get("version") or "").strip()
    data_level = str(manifest.get("data_level") or "L3").strip() or "L3"
    if not name or not version:
        raise SystemExit("plugin.yaml 缺少 name 或 version")
    print("技能包：%s（slug=%s 版本=%s %d 字节）" % (name, slug, version, len(data)))
    print("来源：%s" % source)

    db = SessionLocal()
    try:
        existing = plugins_with_slug(db, slug)
        print("插件中心里同 slug 的插件：%s" % ([p.id for p in existing] or "无"))
        if args.dry_run:
            print("--dry-run：不做任何写库操作")
            return 0
        target = existing[0] if existing else None

        version_row = None
        if target is not None:
            version_row = db.scalar(select(models.PluginVersion).where(
                models.PluginVersion.plugin_id == target.id,
                models.PluginVersion.version == version))
        if version_row is not None:
            print("版本 %s 已存在，跳过提交" % version)
        else:
            version_row = plugin_governance.submit_zip(
                db, data=data, filename="%s-%s.zip" % (slug, version), name=name,
                plugin_type="skill", scope="shared", category=None,
                deployment_mode="instruction", data_level=data_level, version=version,
                submitter=args.submitter, target_agent_id=None,
                plugin_id=target.id if target is not None else None,
            )
            print("已提交：plugin=%s version=%s（%s）"
                  % (version_row.plugin_id, version_row.version, version_row.review_status))
        target = db.get(models.Plugin, version_row.plugin_id)

        if version_row.review_status != "approved":
            version_row.review_status = "approved"
            version_row.reviewed_by = REVIEWER
            version_row.reviewed_at = datetime.now()
            version_row.review_note = "本地技能包导入（离线脚本）"
            db.add(models.PluginReview(
                plugin_version_id=version_row.id, decision="approved",
                reviewer=REVIEWER, note=version_row.review_note))
            db.commit()
            print("已审核通过（reviewer=%s）" % REVIEWER)
        if version_row.publish_status != "published":
            plugin_governance.publish_version(db, version_row, REVIEWER)
            print("已发布（%s → %s）" % (version_row.version, version_row.publish_status))
        else:
            plugin_governance.expand_skill_artifact(target, version_row)
            print("版本已发布，已刷新展开目录")

        binding, created = ensure_binding(db, target.id, args.employee, REVIEWER)
        print("绑定%s：%s → %s" % ("已创建" if created else "已存在", target.id, args.employee))

        if args.prune_duplicates:
            for plugin in plugins_with_slug(db, slug):
                if plugin.id == target.id:
                    continue
                plugin_governance.withdraw_plugin(db, plugin)
                print("重复插件已下架：%s（绑定同步停用）" % plugin.id)

        result = runtime_manager.deploy_skills(db, args.employee)
        print("投放：deployed=%s skipped=%s removed=%s failed=%s"
              % (result.get("deployed"), result.get("skipped"),
                 result.get("removed"), result.get("failed")))

        print("\n完成：%s %s 已入库并授权给 %s" % (name, version, args.employee))
        print("  插件 ID：%s" % target.id)
        print("  当前版本：%s" % target.current_version)
        print("  插件状态：%s" % target.status)
        print("  绑定 ID：%s" % binding.id)
        return 0
    finally:
        db.close()


if __name__ == "__main__":
    raise SystemExit(main())
