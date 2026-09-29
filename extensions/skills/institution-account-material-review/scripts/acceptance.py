#!/usr/bin/env python3
"""验收脚本：一次跑完「覆盖率 + 幻觉 + 三态 + 口径」四类硬检查，给一个过/不过的结论。

验收口径（与计划一致）：
- **覆盖**：必核字段必须全部被核到，未核项要显式列出原因；
- **幻觉**：每条非空取值的 evidence_quote 都必须能在来源材料的转写文本里原文命中；
- **三态**：印章、签字类字段（判定编号 J-07）只允许出 L3 三态，不得给一致性结论；
- **口径**：结论带免责声明，L2 不一致必须给差异性质（detail 非空）。

用法：
  python3 scripts/acceptance.py --materials /workspace/materials/B2026001 \
      --findings findings.json [--json] [--min-coverage 1.0]

退出码：0 = 全部通过；1 = 有检查不通过（明细打印出来，可直接贴进复盘）。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import review as rv  # noqa: E402


def run_checks(materials_dir, payload, min_coverage=1.0, baseline=None):
    """baseline：样例集覆盖基线。给了它，覆盖率就按「基线之外不得出现新增未核」判 ——
    样例材料集天然只覆盖整笔业务的一部分（必核 53 个字段里有 20 个的来源材料不在这批
    材料里），要求 100% 等于永远不通过，反而会让闸门失去意义。基线必须显式列出哪些
    字段是「这批材料本来就没有来源」，新增未核仍然算失败。"""
    checks = []

    def add(name, passed, detail):
        checks.append({"name": name, "pass": bool(passed), "detail": detail})

    # 1) 覆盖率
    coverage = rv.coverage_check(payload)
    unchecked_keys = [u["key"] for u in coverage["unchecked"]]
    if baseline is None:
        # 默认门槛 1.0 = 闸门：必核字段必须全核到。显式调低门槛（<1.0）是调用方主动说
        # 「这个比例我接受」，此时按比例判、但未核清单照样打出来。
        strict = min_coverage >= 1.0
        passed = (coverage["pass"] and (coverage["coverage"] or 0) >= min_coverage) if strict \
            else (coverage["coverage"] or 0) >= min_coverage
        add("覆盖率：必核字段全部核到" if strict else "覆盖率：达到指定门槛 %.2f" % min_coverage,
            passed,
            {"coverage": coverage["coverage"], "required_total": coverage["required_total"],
             "checked": coverage["checked"], "unchecked": unchecked_keys})
    else:
        known = set(baseline.get("uncovered_keys") or [])
        new_unchecked = [k for k in unchecked_keys if k not in known]
        add("覆盖率：基线之外无新增未核",
            not new_unchecked,
            {"coverage": coverage["coverage"], "required_total": coverage["required_total"],
             "checked": coverage["checked"],
             "baseline_uncovered": len(known), "new_unchecked": new_unchecked,
             "unchecked": unchecked_keys,
             "baseline_source": baseline.get("sample_set") or baseline.get("note")})

    # 2) 幻觉：证据必须命中原文
    verify = rv.verify_findings(materials_dir, payload)
    misses = [r for r in verify["results"] if not r["hit"]]
    add("幻觉：全部结论的证据都能在原文命中",
        not misses,
        {"checked": verify["checked"], "miss": verify["miss"],
         "miss_detail": misses[:10]})

    # 3) 印章 / 签字类只出三态
    findings = payload.get("findings") or []
    visual = [f for f in findings if f.get("judge") == "J-07"]
    bad_visual = [f["key"] for f in visual if f.get("level") != "L3"]
    add("三态：印章/签字类字段只出三态（L3）",
        not bad_visual,
        {"visual_fields": len(visual), "violations": bad_visual})

    # 4) 免责声明
    add("口径：结论带「以人工审核为准」免责声明",
        "人工审核" in str(payload.get("disclaimer") or ""),
        payload.get("disclaimer"))

    # 5) L2 必须给差异性质
    l2 = [f for f in findings if f.get("level") == "L2"]
    no_detail = [f["key"] for f in l2 if not str(f.get("detail") or "").strip()]
    add("口径：不一致（L2）都给了差异性质",
        not no_detail,
        {"l2": len(l2), "missing_detail": no_detail})

    # 6) 材料级问题单独成章（结构存在，不与字段级混排）
    add("口径：材料级问题单独成章",
        "material_issues" in payload and isinstance(payload["material_issues"], list),
        len(payload.get("material_issues") or []))

    return checks, coverage, verify


def main(argv=None):
    ap = argparse.ArgumentParser(description="核对结论验收（覆盖率 + 幻觉 + 三态 + 口径）")
    ap.add_argument("--materials", required=True, help="业务目录")
    ap.add_argument("--findings", default="findings.json")
    ap.add_argument("--min-coverage", type=float, default=1.0)
    ap.add_argument("--baseline", help="样例集覆盖基线 JSON（声明这批材料本来就没有来源的必核字段）")
    ap.add_argument("--json", action="store_true", help="只输出 JSON")
    args = ap.parse_args(argv)

    with open(args.findings, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    baseline = None
    if args.baseline:
        with open(args.baseline, "r", encoding="utf-8") as fh:
            baseline = json.load(fh)
    checks, coverage, verify = run_checks(args.materials, payload, args.min_coverage, baseline)
    passed = all(c["pass"] for c in checks)
    result = {
        "pass": passed,
        "findings": args.findings,
        "materials": args.materials,
        "coverage": coverage["coverage"],
        "required_total": coverage["required_total"],
        "coverage_baseline": (baseline or {}).get("sample_set"),
        "evidence_checked": verify["checked"],
        "evidence_miss": verify["miss"],
        "evidence_cross_line": verify.get("cross_line", 0),
        "checks": checks,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if passed else 1

    print("验收结果：%s" % ("通过" if passed else "不通过"))
    print("覆盖率 %s（必核 %d 个%s）；证据回查 %d 条，未命中 %d 条（跨行命中 %d 条）"
          % (coverage["coverage"], coverage["required_total"],
             "，按样例集基线判" if baseline else "",
             verify["checked"], verify["miss"], verify.get("cross_line", 0)))
    for item in checks:
        print("  %s %s%s" % ("PASS" if item["pass"] else "FAIL", item["name"],
                             "" if item["pass"] else "  ← %s" % json.dumps(
                                 item["detail"], ensure_ascii=False)[:300]))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
