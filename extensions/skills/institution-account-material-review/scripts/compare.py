#!/usr/bin/env python3
"""参考路径（完整流程）：清点 → 前置校验 → 逐字段判定 → findings.json。纯 stdlib。

**这条路径仅供参考，不是必须走的流程。** 核对范围、顺序、深挖方式由你（模型）按实际
材料情况自行决定：你可以只用 `review.py` 的原子子命令拼自己的流程，也可以直接调这个
脚本一次跑完当基线。原子能力都在 `review.py`，这里只负责按固定顺序串起来，避免两份逻辑漂移。

用法：
  python3 compare.py --list  /workspace/materials/<业务号>
  python3 compare.py --input /workspace/materials/<业务号> --out findings.json
  python3 compare.py --input <业务号> --fields sys01.cert_no,sys01.cert_type --out findings.json --append
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


def main(argv=None):
    ap = argparse.ArgumentParser(description="机构开户材料交叉核对 · 参考路径")
    ap.add_argument("--input", help="业务目录（含每份材料的抽取结果 JSON）")
    ap.add_argument("--out", default="findings.json")
    ap.add_argument("--list", action="store_true", help="只清点材料，不跑比对")
    ap.add_argument("--fields", help="只比这些字段（逗号分隔）")
    ap.add_argument("--groups", help="只比这些系统侧分组（逗号分隔）")
    ap.add_argument("--append", action="store_true", help="并入已有 findings（局部重跑）")
    ap.add_argument("--business-no", default=None)
    args = ap.parse_args(argv)

    if not args.input:
        ap.error("必须指定 --input")

    if args.list:
        rv.emit(rv.inspect_materials(args.input))
        return 0

    payload = rv.run_compare(args.input, keys=rv.parse_csv(args.fields),
                             groups=rv.parse_csv(args.groups),
                             business_no=args.business_no)
    out_path = Path(args.out)
    if args.append and out_path.exists():
        with open(out_path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        payload["findings"] = rv.merge_findings(old.get("findings") or [], payload["findings"])
    payload["summary"] = rv.summarise(payload)
    payload["coverage"] = rv.coverage_check(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)

    s = payload["summary"]
    cov = payload["coverage"]
    print("结论：不一致 L2 %d｜待确认 L3 %d｜一致 L4 %d｜系统独有 L5 %d｜严重风险 L1 %d"
          % (s["by_level"].get("L2", 0), s["by_level"].get("L3", 0), s["by_level"].get("L4", 0),
             s["by_level"].get("L5", 0), s["by_level"].get("L1", 0)))
    print("必核字段 %d 个，一致 %d 个，一致率 %s" % (s["required_fields"],
                                                s["consistent_required"], s["coverage"]))
    print("覆盖率 %s（未核 %d 项）；材料级问题 %d 条"
          % (cov["coverage"], len(cov["unchecked"]), len(payload["material_issues"])))
    print("已写出 %s" % out_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
