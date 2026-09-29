#!/usr/bin/env python3
"""把真样例材料打成一个「材料包」zip，供平台上传测试用。

用法：
    python3 make-audit-pack.py                      # 默认 6 份材料、2 个业务号
    python3 make-audit-pack.py --all                # 全部 18 份、2 个业务号
    python3 make-audit-pack.py --size 3 --business 3
    python3 make-audit-pack.py --flat               # 完全平铺、文件名不带业务号
    python3 make-audit-pack.py --out /tmp/my-pack.zip

默认包内结构是「按业务号分目录」（业务号只是**提示**，平台不再据此分笔）：

    B2026001/营业执照.png
    B2026001/工商公示信息.png
    B2026002/开户合同签字页.png

`--flat` 打成完全平铺、文件名也不带业务号的形式，用来正面验收「不靠命名也能跑」：
平台会把它们全部归到同一个提示下，由 AI 员工按材料内容自行分笔。

业务号是编出来的（样例本身没有业务号）。同一个业务号下的材料会被当作同一笔业务做交叉核对，
所以想测「跨业务号不串味」就把两份不同业务的材料放进同一个包。
"""

from __future__ import annotations

import argparse
import zipfile
from pathlib import Path

SAMPLES = Path("/root/audit/operation/03_样本材料/智能审核样本")

# 挑材料时优先选这些：能覆盖「系统录入侧截图 / 客户提交侧扫描件 / 签章页 / 文本直读」四种形态
PREFERRED = [
    "营业执照.png",             # 客户侧扫描件，有印章
    "工商公示信息.png",         # 另一份同源材料，可以交叉核对
    "开户合同签字页.png",       # 签章页，测三态
    "三方存管协议.png",
    "资金账户开户申请表_已盖章.png",
    "法人证件信息.png",
    "开户截图 (1).png",         # 系统录入侧截图：带界面控件文字、一行多字段
    "开户截图 (2).png",
    "证券账户开户申请表.png",
    "公章3.jpeg",
    "100012798_业务详情.txt",   # 文本直读通路
    "20260915-机构受益所有人分析排查记录表（2026年4月版）模板.docx",  # docx 混排
]


def pick(size: int) -> list[str]:
    available = [name for name in PREFERRED if (SAMPLES / name).is_file()]
    rest = sorted(
        path.name for path in SAMPLES.iterdir()
        if path.is_file() and path.name not in available
        and path.suffix.lower() in {".png", ".jpg", ".jpeg", ".txt", ".docx", ".pdf"}
    )
    ordered = available + rest
    return ordered[:size] if size > 0 else ordered


def main() -> int:
    ap = argparse.ArgumentParser(description="打一个审核材料包 zip")
    ap.add_argument("--size", type=int, default=6, help="材料份数，0 表示全部")
    ap.add_argument("--business", type=int, default=2, help="拆成几个业务号")
    ap.add_argument("--out", default="/tmp/audit-pack.zip")
    args = ap.parse_args()

    if not SAMPLES.is_dir():
        raise SystemExit("样例目录不存在：%s" % SAMPLES)
    names = pick(args.size)
    if not names:
        raise SystemExit("样例目录里没有可用材料：%s" % SAMPLES)

    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as zf:
        for index, name in enumerate(names):
            business = "B202600%d" % (index % max(1, args.business) + 1)
            zf.write(SAMPLES / name, "%s/%s" % (business, name))

    print("材料包：%s（%.1f KB）" % (target, target.stat().st_size / 1024))
    for index, name in enumerate(names):
        business = "B202600%d" % (index % max(1, args.business) + 1)
        print("  %s/%s" % (business, name))
    print("\n上传方式：在会话输入框点「材料包」图标选这个 zip（或拖进输入区）。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
