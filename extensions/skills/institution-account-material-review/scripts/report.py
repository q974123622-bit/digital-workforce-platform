#!/usr/bin/env python3
"""findings.json → 人工可读的核对表（Markdown / HTML）。纯 stdlib。

用法：
  python3 report.py --findings findings.json --out 核对表.md
  # 默认同时产出 核对表.md 与 核对表.html（同一份 blocks，两种渲染，不会漂移）
  python3 report.py --findings findings.json --out 核对表.md --html 指定路径.html
  python3 report.py --findings findings.json --out 核对表.md --no-html   # 只要 Markdown

这里是渲染器本身（无闸门）。**出表闸门在 `review.py report`**：它会先跑覆盖率自检，
必核字段存在未核项时拒绝出表，除非显式 `--force`。

设计：先把 findings 变成**结构化文档块**（heading / para / table / list / quote），
再由两个渲染器分别输出 Markdown 与 HTML。不先写 Markdown 再解析成 HTML ——
那样两种形态必然漂移，而且 Markdown 表格一旦错列，HTML 也跟着错。

三条必须守住的口径（都是拿真样例踩出来的）：

1. **材料缺口置顶**。材料不齐时（比如只有客户侧材料、没有系统侧截图），
   老实铺几十行「无来源材料提供该字段」等于什么都没说。第一节先讲清
   「缺哪几份必备材料 → 因此哪几个字段无法比对」，未核字段按缺口归并成几行。
2. **来源并列成列，列数由数据决定**。某一侧有多个来源时，固定两列会串列
   （真机产物里 6 列表塞了 7 个单元格，整节在 Markdown 里错位）。列头与每行
   单元格数由同一份 `columns` 生成，不可能对不上。
3. **一致率与覆盖率是两个数，各带分子分母**。上一版把它们显示成同一个小数
   （都是 0.1698），容易被读成「一致率 17%」。
"""

from __future__ import annotations

import argparse
import html as html_lib
import json
from pathlib import Path

LEVEL_BADGE = {
    "L1": "🚨 严重风险",
    "L2": "❌ 不一致",
    "L3": "🟡 待确认",
    "L4": "✅ 一致",
    "L5": "🟣 系统独有",
}
UNCHECKED_BADGE = "⬜ 未核"
_CN = ["零", "一", "二", "三", "四", "五", "六", "七", "八", "九", "十",
       "十一", "十二", "十三", "十四", "十五", "十六", "十七", "十八", "十九", "二十"]
_CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩⑪⑫⑬⑭⑮⑯⑰⑱⑲⑳"


# ==================================================================== 基础工具

def cell(value):
    """单元格文本：空值统一成 `-`，换行压平（Markdown 表格不能有裸换行）。

    **这里不做竖线转义** —— 转义统一在 `Doc.table` 里做一次。否则「先转义、
    再被 `cell` 转义一次」会变成 `\\\\|`，而漏掉一次就是表格串列（真机产物里
    6 列表塞了 7 个单元格，整节错位）。取值里带竖线不是罕见情况：
    归一化的同义词展开就是 `SYN::甲|乙|丙`。
    """
    text = "-" if value in (None, "") else str(value)
    return text.replace("\n", " ").replace("\r", " ").strip()


def escape_cell(text):
    """Markdown 表格单元格里的竖线必须转义，否则整行错列。"""
    return str(text).replace("|", "\\|")


def _short(value, limit=180):
    text = cell(value)
    return text if len(text) <= limit else text[:limit - 1] + "…"


def pct(value):
    return "-" if value is None else "%.1f%%" % (float(value) * 100)


def _ratio(hit, total):
    if not total:
        return "-"
    return "%s（%d/%d）" % (pct(float(hit) / total), hit, total)


def _cn(index):
    return _CN[index] if 0 <= index < len(_CN) else str(index)


def _circle(index):
    return _CIRCLED[index] if index < len(_CIRCLED) else "(%d)" % (index + 1)


def _sorted_types(types):
    """系统侧在前、客户侧在后，各自按代号排序（用于材料**类型**）。"""
    return sorted(set(t for t in types if t),
                  key=lambda t: (0 if str(t).startswith("M-SYS-") else 1, str(t)))


def _type_of(data, code):
    return (data.get("material_types") or {}).get(code)


def _sort_keys(data, keys):
    """把材料**代号**排成稳定的列序。

    排序要按材料类型（M-CUST-01 在 M-CUST-02 前），不能按代号字符串 ——
    代号是 D1 / 文件名，中文按 Unicode 排出来是「工商公示信息」在「营业执照」前面，
    与规则表的顺序无关，列头看起来就是乱的。
    """
    def order(code):
        mtype = _type_of(data, code) or code
        return (0 if str(mtype).startswith("M-SYS-") else 1, str(mtype), str(code))
    return sorted(set(code for code in keys if code), key=order)


def difference_nature(finding):
    """L2 的差异性质：错填 / 漏填 / 口径不同 / 疑似识别错误。"""
    sources = finding.get("sources") or []
    values = [s for s in sources if s.get("empty_state") == "value"]
    if any(s.get("empty_state") != "value" for s in sources):
        return "漏填"
    if len(values) == 2:
        a, b = str(values[0].get("value") or ""), str(values[1].get("value") or "")
        if a and b and (a in b or b in a):
            return "口径不同"
        if len(a) == len(b):
            return "疑似识别错误或错填"
    return "错填"


# ==================================================================== 文档块

class Doc:
    """结构化文档：块序列 + 自动章节编号。"""

    def __init__(self):
        self.blocks = []
        self._section = -1

    def h1(self, text):
        self.blocks.append({"kind": "heading", "level": 1, "text": text})

    def h2(self, text, numbered=True):
        if numbered:
            self._section += 1
            text = "%s、%s" % (_cn(self._section), text)
        self.blocks.append({"kind": "heading", "level": 2, "text": text})

    def h3(self, text):
        self.blocks.append({"kind": "heading", "level": 3, "text": text})

    def para(self, text):
        self.blocks.append({"kind": "para", "text": text})

    def quote(self, text):
        self.blocks.append({"kind": "quote", "text": text})

    def bullets(self, items):
        self.blocks.append({"kind": "list", "items": [str(i) for i in items]})

    def table(self, head, rows, empty="无。"):
        if not rows:
            self.blocks.append({"kind": "para", "text": empty})
            return
        # 竖线转义的唯一入口：保证每个单元格恰好转义一次
        self.blocks.append({"kind": "table",
                            "head": [escape_cell(cell(h)) for h in head],
                            "rows": [[escape_cell(cell(c)) for c in row] for row in rows]})


def render_markdown(blocks):
    out = []
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            out.append("%s %s" % ("#" * block["level"], block["text"]))
        elif kind == "para":
            out.append(block["text"])
        elif kind == "quote":
            out.append("> %s" % block["text"])
        elif kind == "list":
            out.append("\n".join("- %s" % item for item in block["items"]))
        elif kind == "table":
            lines = ["| %s |" % " | ".join(block["head"]),
                     "|%s|" % "|".join("---" for _ in block["head"])]
            for row in block["rows"]:
                lines.append("| %s |" % " | ".join(row))
            out.append("\n".join(lines))
    return "\n\n".join(out) + "\n"


HTML_STYLE = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; padding: 28px 20px 64px; background: #f7f8fa; color: #1f2329;
  font-family: -apple-system, "PingFang SC", "Microsoft YaHei", "Noto Sans CJK SC", sans-serif;
  font-size: 13px; line-height: 1.7; }
.wrap { max-width: 1440px; margin: 0 auto; background: #fff; border: 1px solid #e5e7eb;
  border-radius: 10px; padding: 28px 32px 40px; box-shadow: 0 1px 3px rgba(16,24,40,.06); }
h1 { font-size: 22px; margin: 0 0 4px; }
h2 { font-size: 17px; margin: 32px 0 12px; padding-bottom: 7px; border-bottom: 2px solid #e5e7eb; }
h3 { font-size: 14px; margin: 22px 0 8px; color: #344054; }
p { margin: 10px 0; }
blockquote { margin: 12px 0; padding: 9px 14px; background: #fffbeb;
  border-left: 4px solid #f59e0b; border-radius: 0 6px 6px 0; color: #7c4a03; }
table { width: 100%; border-collapse: collapse; margin: 10px 0 18px; font-size: 12.5px; }
th, td { border: 1px solid #e5e7eb; padding: 7px 9px; text-align: left; vertical-align: top;
  word-break: break-word; }
th { background: #f2f4f7; font-weight: 600; white-space: nowrap; }
tbody tr:nth-child(even) { background: #fbfcfd; }
tbody tr:hover { background: #f6f9ff; }
ul { margin: 8px 0; padding-left: 22px; }
li { margin: 3px 0; }
strong { color: #b42318; }
code { background: #f2f4f7; padding: 1px 5px; border-radius: 4px; font-size: 12px; }
@media print { body { background: #fff; padding: 0; } .wrap { border: 0; box-shadow: none; } }
""".strip()


def _inline_html(text):
    # Markdown 里为防串列而转义的竖线，在 HTML 里要还原成普通竖线
    escaped = html_lib.escape(str(text).replace("\\|", "|"), quote=False)
    # 极小的行内标记：**粗体** 与 `代码`。只做这两条，够用且不会误伤。
    while "**" in escaped:
        if escaped.count("**") < 2:
            break
        escaped = escaped.replace("**", "<strong>", 1).replace("**", "</strong>", 1)
    if "`" in escaped:
        parts = escaped.split("`")
        if len(parts) >= 3:
            rebuilt = parts[0]
            for index in range(1, len(parts)):
                rebuilt += ("<code>%s</code>" % parts[index] if index % 2 else parts[index])
            escaped = rebuilt
    return escaped


def render_html(blocks, title):
    body = []
    for block in blocks:
        kind = block["kind"]
        if kind == "heading":
            body.append("<h%d>%s</h%d>" % (block["level"], _inline_html(block["text"]),
                                           block["level"]))
        elif kind == "para":
            body.append("<p>%s</p>" % _inline_html(block["text"]))
        elif kind == "quote":
            body.append("<blockquote>%s</blockquote>" % _inline_html(block["text"]))
        elif kind == "list":
            body.append("<ul>%s</ul>" % "".join("<li>%s</li>" % _inline_html(i)
                                                for i in block["items"]))
        elif kind == "table":
            head = "".join("<th>%s</th>" % _inline_html(h) for h in block["head"])
            rows = "".join(
                "<tr>%s</tr>" % "".join("<td>%s</td>" % _inline_html(c) for c in row)
                for row in block["rows"])
            body.append("<table><thead><tr>%s</tr></thead><tbody>%s</tbody></table>" % (head, rows))
    return ("<!DOCTYPE html>\n<html lang=\"zh-CN\">\n<head>\n<meta charset=\"utf-8\">\n"
            "<meta name=\"viewport\" content=\"width=device-width, initial-scale=1\">\n"
            "<title>%s</title>\n<style>\n%s\n</style>\n</head>\n<body>\n<div class=\"wrap\">\n%s\n"
            "</div>\n</body>\n</html>\n"
            % (html_lib.escape(str(title)), HTML_STYLE, "\n".join(body)))


# ==================================================================== 取材

def _materials(data):
    return list(data.get("materials") or [])


def _name_of(data, key):
    """材料**代号**或**类型** → 显示名。

    先查代号表（结论里的 `sources[].material` 是代号），再查类型表
    （缺失材料、系统侧分组用的是类型）。两处都查不到时原样返回。
    """
    if not key:
        return "-"
    labels = data.get("material_labels") or {}
    if key in labels:
        return labels[key]
    return (data.get("material_names") or {}).get(key) or key


def _material_by_code(data):
    return {m.get("material_code"): m for m in _materials(data)}


def _side_label(side):
    return {"system": "系统录入（基准方）", "customer": "客户提交（对照方）"}.get(side, "-")


def _purpose(material, data):
    """材料清单的「用途说明」：类型名 + 抽取字段数 + 是否参与逐字段比对。"""
    parts = []
    mtype = material.get("material_type")
    if mtype:
        parts.append(_name_of(data, mtype))
    count = material.get("field_count") or 0
    parts.append("抽取 %d 字段" % count)
    if material.get("side") == "system":
        # 系统录入侧是**被核对对象**，不是「不用抽的字段全集来源」。这里写清楚，
        # 否则容易被读成「系统侧不用抽字段」，进而漏掉基准方取值（真机踩过）。
        parts.append("基准方（被核对对象，需与客户材料一样抽字段）")
        if not count:
            parts.append("**本轮未取到任何取值，比对已退化**")
    elif material.get("compare_only") is False:
        parts.append("仅登记与签章检查，不参与逐字段比对")
    else:
        parts.append("参与逐字段比对")
    required = material.get("required")
    if required is True:
        parts.append("必备")
    elif required == "conditional":
        parts.append("条件必备")
    elif required == "reference":
        parts.append("参考件")
    return "；".join(parts)


def _columns_for(data, findings):
    """一组结论的来源列（系统侧在前）。

    列来自各结论**实际**的来源材料，而不是规则表里声明的全集 —— 声明了但本轮
    没提供的材料如果也占一列，整张表会全是 `-`，反而看不清到底比了什么。
    未成列的声明来源会在表上方用一句话交代。
    """
    codes = set()
    for finding in findings:
        for src in finding.get("sources") or []:
            if src.get("material"):
                codes.add(src["material"])
    return _sort_keys(data, codes)


def _declared_but_not_column(data, findings, columns):
    """声明了却没成列的材料（本轮未提供，或材料里没有对应栏目）。"""
    declared = set()
    for finding in findings:
        for src in finding.get("declared_sources") or []:
            if src.get("material"):
                declared.add(src["material"])
    return _sort_keys(data, declared - set(columns))


def _group_by_group(findings):
    """按系统侧分组（= 一张截图）聚合。汇总表最多 5 张，不会碎成一堆 1 行小表。"""
    groups = {}
    for finding in findings:
        groups.setdefault(finding.get("system_group"), []).append(finding)
    return groups


def _group_title(doc, group, columns):
    """标题里的材料顺序与表头列序**完全一致**，否则读者按标题对不上列。"""
    others = [code for code in columns if _type_of(doc.data, code) != group]
    return " vs ".join([_name_of(doc.data, group)] + [_name_of(doc.data, c) for c in others])


VALUE_LIMIT = 120


def _value_cell(finding, mtype):
    """某材料在这一行的取值；材料本轮没提供该栏目时给 `-`。

    超长取值（经营范围这类）截断到 120 字符：几百字的单元格会把整张宽表撑烂，
    完整取值在 `findings.json` 里。
    """
    for src in finding.get("sources") or []:
        if src.get("material") != mtype:
            continue
        state = src.get("empty_state")
        if state == "value":
            raw = cell(src.get("value"))
            norm = src.get("normalized")
            if norm and str(norm) != str(src.get("value")):
                return _short("%s → %s" % (raw, cell(norm)), VALUE_LIMIT)
            return _short(raw, VALUE_LIMIT)
        return "（空）" if state else "-"
    return "-"


def _finding_note(finding):
    if finding.get("level") == "L2":
        return "[%s] %s" % (difference_nature(finding), _short(finding.get("detail"), 160))
    return _short(finding.get("detail"), 160)


def _result_cell(finding):
    return LEVEL_BADGE.get(finding.get("level"), cell(finding.get("level")))


def _matrix_table(doc, findings, columns, index_column=True):
    """字段 × 来源材料 的宽表。列头与每行单元格由同一个 columns 生成，不会错列。"""
    head = (["序号"] if index_column else []) + ["字段名称"]
    head += [_circle(i) + _name_of(doc.data, mtype) for i, mtype in enumerate(columns)]
    head += ["核对结果", "说明"]
    rows = []
    for index, finding in enumerate(findings, start=1):
        row = ([str(index)] if index_column else []) + [cell(finding.get("name"))]
        row += [_value_cell(finding, mtype) for mtype in columns]
        row += [_result_cell(finding), _finding_note(finding)]
        rows.append(row)
    doc.table(head, rows)


def _gap_rows(data):
    """未核字段按缺口归并。

    返回 `(缺材料计数, 字段名未对齐计数, 无缺口线索数, 未核清单)`。
    两种缺口分开计：材料缺失要补件，字段名未对齐要重跑 extract。
    """
    coverage = data.get("coverage") or {}
    unchecked = coverage.get("unchecked") or []
    absent_counts, unmatched_counts, no_cause = {}, {}, 0
    for item in unchecked:
        absent = item.get("missing_materials") or []
        unmatched = item.get("unmatched_materials") or []
        if not absent and not unmatched:
            no_cause += 1
        for code in absent:
            absent_counts[code] = absent_counts.get(code, 0) + 1
        for code in unmatched:
            unmatched_counts[code] = unmatched_counts.get(code, 0) + 1
    return absent_counts, unmatched_counts, no_cause, unchecked


# ==================================================================== 各章节

def _section_integrity(doc, data):
    """零、本轮材料完整性 —— 缺口置顶。"""
    doc.h2("本轮材料完整性（先看这里）")
    missing = data.get("missing_required") or []
    absent_counts, unmatched_counts, no_cause, unchecked = _gap_rows(data)
    coverage = data.get("coverage") or {}
    total_required = coverage.get("required_total") or 0
    checked = coverage.get("checked") or 0

    if missing:
        names = "、".join("%s（%s）" % (m.get("name"), m.get("code")) for m in missing)
        doc.para("**本轮缺 %d 份必备材料**：%s。因此下列必核字段本轮**无法比对**，"
                 "相关结论不成立；建议补齐材料后重跑。"
                 % (len(missing), names))
    else:
        doc.para("必备材料已齐备。")

    if unchecked:
        doc.para("必核字段 %d 个，本轮核到 %d 个，**%d 个未核**（占比 %s）。"
                 % (total_required, checked, len(unchecked),
                    pct(1 - float(checked) / total_required) if total_required else "-"))
    else:
        doc.para("必核字段 %d 个已全部核到。" % total_required)

    if missing:
        doc.table(["缺失的必备材料", "代号", "因此无法比对的必核字段数"],
                  [[material.get("name"), material.get("code"),
                    absent_counts.get(material.get("code"), 0)] for material in missing])

    if unmatched_counts:
        doc.para("**另一种缺口：材料已提供，但材料里没有对应栏目** —— 抽取出来的字段名"
                 "跟规则表没对齐（多半是材料没跑过 `extract`，或字段名写法不同）。"
                 "这批要的是重跑抽取，不是补件：")
        doc.table(["已提供、但缺该栏目的材料", "影响的必核字段数"],
                  [[_name_of(data, code), count]
                   for code, count in sorted(unmatched_counts.items())])
        doc.para("（一个字段可能同时受多份材料影响，故各行合计大于未核字段数。）")

    if no_cause:
        doc.para("另有 %d 个必核字段本轮未比对（不在已比字段范围内）。" % no_cause)

    grouped = [g for g in (coverage.get("by_missing_material") or [])
               if g.get("materials") or g.get("unmatched")]
    if grouped:
        doc.para("**未核字段与原因**（按缺口归并；完整清单见 `findings.json` 的 "
                 "`coverage.unchecked`）：")
        rows = []
        for group in grouped:
            if group.get("names"):
                cause = "缺 " + "、".join(group["names"])
            elif group.get("unmatched_names"):
                cause = "材料已提供但无该栏目"
            else:
                cause = "其他（材料齐备，字段未比对）"
            examples = "、".join(f.get("name") or f.get("key") for f in group["fields"][:12])
            if group["count"] > 12:
                examples += " 等"
            rows.append([cause, group["count"], examples])
        doc.table(["缺口（主因）", "字段数", "字段（前 12 个）"], rows)
    elif no_cause:
        doc.para("另有 %d 个必核字段本轮未比对（不在已比字段范围内），详见 `coverage.unchecked`。"
                 % no_cause)

    excluded = [i for i in (data.get("material_issues") or []) if i.get("type") == "P-01"]
    if excluded:
        doc.para("**已排除在逐字段比对之外的材料**（主体不一致，见材料级问题）：")
        doc.table(["材料代号", "原因"],
                  [[i.get("material"), _short(i.get("detail"))] for i in excluded])

    converged = data.get("converged_out") or []
    if converged:
        doc.para("**未纳入比对的材料**（同一材料类型多版本，按 P-04 只取一份）：%s"
                 % "、".join(converged))


def _section_materials(doc, data):
    doc.h2("比对材料清单")
    rows = []
    for material in _materials(data):
        rows.append([material.get("material_code"),
                     material.get("name") or _name_of(data, material.get("material_type")),
                     _side_label(material.get("side")),
                     _purpose(material, data)])
    for material in data.get("missing_required") or []:
        rows.append([material.get("code"), material.get("name"), _side_label("customer"),
                     "**本轮未提供（必备材料缺失）**"])
    doc.table(["代号", "材料名称", "类型", "用途说明"], rows)


def _section_summary(doc, data):
    doc.h2("结论摘要")
    summary = data.get("summary") or {}
    by_level = summary.get("by_level") or {}
    coverage = data.get("coverage") or {}
    required = summary.get("required_fields") or 0
    consistent = summary.get("consistent_required") or 0
    # 严重风险是**材料级**问题（P-01），不在 findings 里，不能从 by_level 取
    critical = len([i for i in (data.get("material_issues") or []) if i.get("level") == "L1"])
    # 系统侧（基准方）取值覆盖：基准方全空时逐字段比对已经退化，必须在摘要里显式告警
    sys_side = coverage.get("system_side") or {}
    sys_present = sys_side.get("types_present") or []
    sys_empty = sys_side.get("empty_type_names") or []
    sys_cell = ("%d 组已取值" % len(sys_present)) if sys_present else "本轮无系统录入侧材料"
    if sys_empty:
        sys_cell += "；**%s 未取到任何取值，比对已退化**" % "、".join(sys_empty)
    rows = [
        ["规则表字段总数", summary.get("total_fields") or 0],
        ["本轮实际比对字段数", len(data.get("findings") or [])],
        ["必核字段数（不含系统独有）", required],
        # L4 总数含非必核字段，与「一致率」的分子不是一回事，两个数一起写出来免得被读成打架
        ["一致（L4，其中必核 %d）" % consistent, by_level.get("L4", 0)],
        ["不一致（L2）", by_level.get("L2", 0)],
        ["待确认（L3，含未读出与视觉三态）", by_level.get("L3", 0)],
        ["严重风险（L1，材料级）", critical],
        ["系统独有（L5，不计入一致率）", by_level.get("L5", 0)],
        ["**一致率（一致 / 必核）**", _ratio(consistent, required)],
        ["**必核字段覆盖率**", _ratio(coverage.get("checked") or 0,
                                    coverage.get("required_total") or 0)],
        ["系统录入侧取值覆盖", sys_cell],
        ["未核字段数", len(coverage.get("unchecked") or [])],
        ["有来源但未读出取值", len(coverage.get("no_value") or [])],
        ["材料级问题", len(data.get("material_issues") or [])],
    ]
    doc.table(["指标", "值"], rows)
    doc.para("口径：一致率 = 必核且判一致的字段数 / 必核字段数（「一致（L4）」那一行是全部字段的 L4 计数，"
             "含非必核字段，所以通常大于一致率的分子）；覆盖率 = 必核字段中被真正核到的比例。"
             "系统独有（L5）与严重风险（L1）不计入分母。两者含义不同，不可互相替代。")
    if sys_empty:
        doc.para("**系统录入侧（基准方）取值缺失**：%s 一份字段都没抽出来。此时逐字段比对只剩客户材料"
                 "互相比对，单个来源会被判成「一致」，结论不可采信 —— 先补齐基准方取值再重跑。"
                 % "、".join(sys_empty))


def _section_highlights(doc, data):
    doc.h2("不一致 / 待确认事项汇总（需优先处理）")
    issues = data.get("material_issues") or []
    findings = data.get("findings") or []

    doc.h3("A. 严重风险（材料级，L1）")
    critical = [i for i in issues if i.get("level") == "L1"]
    doc.table(["序号", "材料", "问题", "建议"],
              [[index, i.get("material"), _short(i.get("detail"), 200), "人工核实是否属本户材料"]
               for index, i in enumerate(critical, start=1)],
              empty="无。")

    for level, title in (("L2", "B. 字段级不一致（L2）"), ("L3", "C. 待确认（L3）")):
        at_level = [f for f in findings if f.get("level") == level]
        # 没有任何来源材料的字段不算「待确认」而是「未核」—— 它们是覆盖率闸门拦下的
        # 那一批，已经在第一节按缺口归并过了。混进汇总表会把 44 行「无来源」铺成表，
        # 正是「报告看起来什么都没核」的来源。
        picked = [f for f in at_level if f.get("sources")]
        skipped = [f for f in at_level if not f.get("sources")]
        doc.h3("%s，共 %d 项" % (title, len(picked)))
        if skipped:
            required_skipped = [f for f in skipped if f.get("required")]
            extra = ""
            if len(skipped) > len(required_skipped):
                extra = "，其余 %d 个是非必核字段（仅登记）" % (len(skipped) - len(required_skipped))
            doc.para("另有 %d 个字段没有任何来源材料：其中 **%d 个是必核字段**，已按缺口归并"
                     "见第一节%s。" % (len(skipped), len(required_skipped), extra))
        if not picked:
            doc.para("无。")
            continue
        for group, items in sorted(_group_by_group(picked).items(),
                                   key=lambda kv: -len(kv[1])):
            columns = _columns_for(data, items)
            doc.para("**%s**（%d 项）" % (_group_title(doc, group, columns), len(items)))
            _matrix_table(doc, items, columns)

    doc.h3("D. 印章 / 签章问题")
    seal = [s for s in (data.get("seal_summary") or [])
            if s.get("stamp") not in ("-", "有") or s.get("signature") not in ("-", "有")]
    doc.table(["序号", "材料", "公章 / 印章", "签字", "核对结果", "建议"],
              [[index, _name_of(data, s.get("material_type")) or s.get("material"),
                s.get("stamp"), s.get("signature"),
                "🟡 需人工看图" if "看不清" in (s.get("stamp"), s.get("signature"))
                else "❌ 未见签章",
                "核实原件" if "看不清" in (s.get("stamp"), s.get("signature"))
                else "确认是否漏签 / 漏盖章"]
               for index, s in enumerate(seal, start=1)],
              empty="无。印章与签字类只出「有 / 无 / 看不清」三态。")


def _section_groups(doc, data):
    """逐份系统侧材料的逐字段明细。每份材料一节，来源并列成列。"""
    findings = data.get("findings") or []
    unchecked = (data.get("coverage") or {}).get("unchecked") or []
    groups = {}
    for finding in findings:
        groups.setdefault(finding.get("system_group"), []).append(finding)
    # 未核字段也要出现在它所属的截图节里，否则那一节的字段数对不上规则表
    uncompared = {}
    for item in unchecked:
        group = item.get("system_group") or ""
        uncompared.setdefault(group, []).append(item)
    for group in uncompared:
        groups.setdefault(group, [])

    if not groups:
        return
    # 注意：这里不能复用 _sorted_types —— 它会滤掉空串，而空 system_group
    # （规则表没登记分组的字段）恰恰是必须显式呈现的。
    for group in sorted(groups, key=lambda g: (0 if str(g).startswith("M-SYS-") else 1, str(g))):
        items = groups.get(group) or []
        # 未核字段同时也是 findings（run_compare 会给每个字段出一条结论，只是 sources 为空），
        # 所以必须按 key 去重，否则同一字段在表里出现两次、节标题的字段数也对不上规则表。
        compared_keys = {f.get("key") for f in items}
        unchecked_items = [i for i in (uncompared.get(group) or [])
                           if i.get("key") not in compared_keys]
        name = _name_of(data, group)
        doc.h2("%s（%s，共 %d 字段）" % (name, group, len(items) + len(unchecked_items)),
               numbered=True)
        columns = _columns_for(data, items)
        absent = _declared_but_not_column(data, items, columns)
        if absent:
            doc.para("本组声明来源中，本轮未参与比对：%s（材料未提供，或材料内没有对应栏目）。"
                     % "、".join(_name_of(data, t) for t in absent))
        head = ["字段名称"] + [_circle(i) + _name_of(data, t) for i, t in enumerate(columns)]
        head += ["核对结果", "说明"]
        rows = []
        for finding in items:
            row = [cell(finding.get("name"))]
            row += [_value_cell(finding, t) for t in columns]
            row += [_result_cell(finding), _finding_note(finding)]
            rows.append(row)
        for item in unchecked_items:
            row = [cell(item.get("name") or item.get("key"))]
            row += ["-"] * len(columns)
            row += [UNCHECKED_BADGE, _short(item.get("reason"), 120)]
            rows.append(row)
        doc.table(head, rows)


def _section_cross_material(doc, data):
    """客户侧材料之间的专项比对：系统侧没有取值时，实际是材料之间在互相印证。"""
    findings = data.get("findings") or []
    picked = []
    for finding in findings:
        system_group = finding.get("system_group")
        sources = finding.get("sources") or []
        system_has_value = any(s.get("material") == system_group
                               and s.get("empty_state") == "value" for s in sources)
        customer = {s.get("material") for s in (finding.get("declared_sources") or [])
                    if s.get("material") and s.get("material") != system_group}
        if not system_has_value and len(customer) >= 2:
            picked.append(finding)
    doc.h2("客户侧材料之间的专项比对", numbered=True)
    if not picked:
        doc.para("无。本轮所有比对的字段都能在系统录入信息里取到取值。")
        return
    # 一张表而不是按「材料组合」拆成十几个小节：系统侧本来就取不到值，
    # 按组合拆只会得到一堆 1~2 行的小表，反而看不清。
    doc.para("下列 %d 个字段的系统录入侧没有取值，实际是客户提交材料之间在互相印证"
             "（上表已按系统侧分组呈现过，这里单列便于交叉核对）：" % len(picked))
    _matrix_table(doc, picked, _columns_for(data, picked), index_column=False)


def _section_seal_checklist(doc, data):
    doc.h2("客户提交材料清单及盖章情况", numbered=True)
    seal_by_code = {s.get("material"): s for s in (data.get("seal_summary") or [])}
    issues = data.get("material_issues") or []
    notes_by_code = {}
    for issue in issues:
        code = issue.get("material")
        if code and code != "-":
            notes_by_code.setdefault(code, []).append("%s：%s" % (issue.get("type"), issue.get("detail")))
    converged = set(data.get("converged_out") or [])
    rows = []
    index = 0
    for material in _materials(data):
        if material.get("side") != "customer":
            continue
        index += 1
        code = material.get("material_code")
        seal = seal_by_code.get(code) or {}
        notes = list(notes_by_code.get(code) or [])
        if (material.get("unreadable_regions") or []):
            notes.append("有 %d 处未识别区域" % len(material["unreadable_regions"]))
        if code in converged:
            notes.append("多版本，未纳入本轮比对")
        rows.append([index, material.get("name") or _name_of(data, material.get("material_type")),
                     "✅ 已提供",
                     seal.get("stamp") or "-", seal.get("signature") or "-",
                     _short("；".join(notes), 160) if notes else "—"])
    for material in data.get("missing_required") or []:
        index += 1
        rows.append([index, material.get("name"), "❌ 未提供", "-", "-", "必备材料缺失，建议补件"])
    doc.table(["序号", "材料名称", "是否提供", "公章情况", "签字情况", "备注"], rows)
    doc.para("印章与签字类只出「有 / 无 / 看不清」三态，不给一致性结论。")


def _section_conclusion(doc, data):
    doc.h2("核对结论", numbered=True)
    summary = data.get("summary") or {}
    by_level = summary.get("by_level") or {}
    coverage = data.get("coverage") or {}
    required = summary.get("required_fields") or 0
    consistent = summary.get("consistent_required") or 0
    findings = data.get("findings") or []
    missing = data.get("missing_required") or []
    unchecked = coverage.get("unchecked") or []

    doc.para("本轮比对 %d 个字段（其中必核 %d 个）：一致 %d、不一致 %d、待确认 %d、系统独有 %d；"
             "一致率 %s，必核字段覆盖率 %s。材料级问题 %d 条。"
             % (len(findings), required, by_level.get("L4", 0), by_level.get("L2", 0),
                by_level.get("L3", 0), by_level.get("L5", 0),
                _ratio(consistent, required), _ratio(coverage.get("checked") or 0,
                                                    coverage.get("required_total") or 0),
                len(data.get("material_issues") or [])))

    doc.h3("需优先处理")
    urgent = []
    for issue in data.get("material_issues") or []:
        if issue.get("level") == "L1":
            urgent.append("**严重风险**：%s —— %s" % (issue.get("material"), _short(issue.get("detail"), 160)))
    if missing:
        urgent.append("**补齐必备材料**：%s"
                      % "、".join("%s（%s）" % (m.get("name"), m.get("code")) for m in missing))
    for finding in findings:
        if finding.get("level") == "L2":
            urgent.append("**不一致**：%s —— %s" % (finding.get("name"), _short(finding.get("detail"), 160)))
    doc.bullets(urgent or ["无。"])

    doc.h3("其他需处理")
    others = []
    for finding in findings:
        # 只列真的比过的字段：无来源的字段是「未核」，下面单独一句交代
        if finding.get("level") == "L3" and finding.get("sources"):
            others.append("待确认：%s —— %s" % (finding.get("name"), _short(finding.get("detail"), 140)))
    if unchecked:
        others.append("未核字段 %d 个（见第一节的缺口归并）：%s"
                      % (len(unchecked),
                         "、".join(cell(i.get("name")) for i in unchecked[:15])
                         + (" 等" if len(unchecked) > 15 else "")))
    doc.bullets(others or ["无。"])

    doc.para("以上全部结论为机器提示，最终以人工审核为准；印章与签字类只出三态。")


def _section_rules(doc, data):
    doc.h2("判定规则说明", numbered=True)
    doc.para("规则编号定义见技能包 `rules/rules.json`；每个字段适用的归一化与判定编号见 "
             "`rules/fields.json`；材料登记与必备性见 `rules/materials.json`。")
    doc.para("结论分级：L1 严重风险（不计入一致率）｜L2 不一致｜L3 待确认｜L4 一致｜"
             "L5 系统独有（不计入一致率）。")
    doc.para("材料级问题编号：P-01 疑似非本户材料｜P-02 材料内部矛盾｜P-03 必备材料或签章缺失｜"
             "P-04 多版本收敛｜P-05 同人多角色。")
    doc.para("表格里的长取值截断到 %d 字符（完整取值见 `findings.json` 的 `findings[].sources`）；"
             "宽表的列由本轮**实际**提供取值的来源材料决定，某份材料本轮没有该栏目时该格为 `-`。"
             % VALUE_LIMIT)


# ==================================================================== 组装

def build_blocks(data):
    doc = Doc()
    doc.data = data  # 供 _name_of 等工具使用
    doc.h1("机构开户材料交叉核对表（机器提示）")
    doc.para("业务号：%s　　生成时间：%s　　本轮材料 %d 份：%s"
             % (cell(data.get("business_no")), cell(data.get("generated_at")),
                len(_materials(data)),
                "、".join(cell(m.get("material_code")) for m in _materials(data)) or "-"))
    doc.quote(data.get("disclaimer") or "全部结论为机器提示，最终以人工审核为准。")
    _section_integrity(doc, data)
    _section_materials(doc, data)
    _section_summary(doc, data)
    _section_highlights(doc, data)
    _section_groups(doc, data)
    _section_cross_material(doc, data)
    _section_seal_checklist(doc, data)
    _section_conclusion(doc, data)
    _section_rules(doc, data)
    return doc.blocks


def build(data):
    """Markdown 核对表（`review.py` 与 `compare.py` 都调这个）。"""
    return render_markdown(build_blocks(data))


def build_html(data):
    return render_html(build_blocks(data),
                       "机构开户材料交叉核对表 · %s" % cell(data.get("business_no")))


def write_html(data, out_path):
    Path(out_path).write_text(build_html(data), encoding="utf-8")
    return out_path


def main(argv=None):
    ap = argparse.ArgumentParser()
    ap.add_argument("--findings", required=True)
    ap.add_argument("--out", default="核对表.md")
    ap.add_argument("--html", nargs="?", const="",
                    help="HTML 输出路径（默认就出，取 <out> 同名 .html）")
    ap.add_argument("--no-html", action="store_true", help="不要 HTML 版（只出 Markdown）")
    args = ap.parse_args(argv)
    with open(args.findings, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    text = build(data)
    Path(args.out).write_text(text, encoding="utf-8")
    written = [args.out]
    if not args.no_html:
        target = args.html or str(Path(args.out).with_suffix(".html"))
        write_html(data, target)
        written.append(target)
    print("已写出 %s（%d 字符）" % ("、".join(written), len(text)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
