#!/usr/bin/env python3
"""机构开户材料交叉核对 · 原子工具集（能力工具箱，不是流水线）。

设计口径：**判定由脚本给出，核对范围与顺序由你（模型）规划**。
每个子命令都无状态、可重复调用、支持只跑一部分、stdout 输出 JSON（report 除外），
你可以自由组合、局部重跑，不必按固定顺序走完。

统一用法：
  python3 scripts/review.py <子命令> [选项]

子命令：
  audit     一站式：清点 → 比对 → 证据回查 → 出表（推荐首选，4~6 次往返压成 1 次）
  inspect   清点材料 + 每份摘要（类型、载体、转写行数、已有字段数、指纹、前几行）
  rules     按需查规则：字段定义 / 材料登记 / 归一化与判定编号 / 同义词表
  extract   对指定材料的指定字段做确定性定位抽值（产出可命中的 evidence_quote）
  compare   比指定字段或字段组，输出 findings（可 --append 累积）
  verify    回查结论的证据是否在材料原文命中（防幻觉）
  report    渲染核对表；出表前强制跑覆盖率自检，未核字段必须显式列出

`audit` 只是把上面这些按固定顺序串起来，判定与闸门完全复用同一套实现；
需要细看某一步（比如只想比一组字段、只想回查一条结论）时再用分步命令。

材料目录约定（**平铺**：一个材料包一个目录，分笔由你判断）：
  <材料包>/
  ├── materials.json     可选；清单（含 hint_business_no 提示、每份材料的 code / source_file）
  ├── S2.json            单份材料：material_code / material_type / text_lines / fields
  ├── D1.json
  └── ...

materials.json 里的 `hint_business_no` 只是按文件名/目录名推测的**提示**，可能与材料内容不符。
哪些材料属于同一笔业务，由你按材料内容判断，再用 `--codes D1,D2,S3` 圈定本轮要核的材料
（逻辑分笔，不必搬动文件）。

单份材料文件格式：
  {
    "material_code": "D1",
    "material_type": "M-CUST-01",
    "source_file": "营业执照.png",
    "kind": "image",
    "text_lines": ["名称 | 机构开户申请", "..."],   // 平台转写落盘的事实层
    "fields": [{"key": "name", "value": "机构开户申请", "evidence_quote": "名称 | 机构开户申请",
                "readable": true, "confidence": 0.93, "position": "第 1 行"}],
    "unreadable_regions": [{"desc": "印章覆盖"}]
  }

铁律（与 SKILL.md 一致）：
1. 只提示不决策，所有结论标注「机器提示，以人工审核为准」；
2. 不猜：值只能来自 text_lines，evidence_quote 必须能原文命中；
3. 印章、签字类只出「有 / 无 / 看不清」三态；
4. 证件号、账号等敏感信息不进日志、不外发；
5. 判定由脚本完成，模型只负责规划与表达。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

import judge as jd  # noqa: E402
import normalize as nz  # noqa: E402
import report as rp  # noqa: E402

RULES_DIR = HERE.parent / "rules"
DISCLAIMER = ("全部结论为机器提示，最终以人工审核为准；"
              "印章与签字类只出「有/无/看不清」三态。")
LEVELS = ("L1", "L2", "L3", "L4", "L5")


# ==================================================================== 规则加载

def load_rules():
    with open(RULES_DIR / "fields.json", "r", encoding="utf-8") as fh:
        fields = json.load(fh)["fields"]
    with open(RULES_DIR / "materials.json", "r", encoding="utf-8") as fh:
        material_types = json.load(fh)
    with open(RULES_DIR / "rules.json", "r", encoding="utf-8") as fh:
        rules = json.load(fh)
    return fields, material_types, rules, nz.load_synonyms()


def material_entries(material_types):
    """把所有材料登记打平成 {code: entry}，两侧（系统侧 / 客户侧）合并。"""
    out = {}
    for side in ("system_side", "customer_side"):
        for item in material_types.get(side, []):
            entry = dict(item)
            entry["side"] = "system" if side == "system_side" else "customer"
            out[entry["code"]] = entry
    return out


def material_name(mtype, material_types):
    for side in ("system_side", "customer_side"):
        for item in material_types.get(side, []):
            if item["code"] == mtype:
                return item["name"]
    return mtype or "未知材料"


# ==================================================================== 材料类型判定
# 材料类型（M-CUST-xx / M-SYS-xx）本来完全靠模型逐份读材料后判断并写回，代价是一轮
# 执行要多读 N 份材料 JSON、多写回 N 次（真机实测这段占了 13 次往返 / 约 2 分钟）。
# 这里给一个**确定性**的文件名 / 文本猜测，只在材料自身没有 material_type 时兜底写入；
# 模型觉得猜错了仍可直接改 material_type —— 判定口径与结论口径都没有放宽。

_TYPE_ALIASES = {
    "营业执照": "M-CUST-01",
    "工商公示信息": "M-CUST-02",
    "企业公示信息": "M-CUST-02",
    "法人证件信息": "M-CUST-03",
    "法人身份证": "M-CUST-03",
    "自然人身份证件": "M-CUST-03",
    "三方存管协议": "M-CUST-04",
    "证券账户开户申请表": "M-CUST-05",
    "资金账户开户申请表": "M-CUST-06",
    "受益所有人分析排查记录表": "M-CUST-07",
    "开户合同签字页": "M-CUST-08",
    "印章图": "M-CUST-09",
}


def _bigrams(text):
    text = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", "", str(text or ""))
    if len(text) < 2:
        return {text} if text else set()
    return {text[i:i + 2] for i in range(len(text) - 1)}


def _dice(a, b):
    ba, bb = _bigrams(a), _bigrams(b)
    if not ba or not bb:
        return 0.0
    return 2.0 * len(ba & bb) / (len(ba) + len(bb))


def guess_material_type(material, entries):
    """按「文件名别名 > 文件名相似度 > 文本里出现材料名」给一个类型猜测。

    返回 ``(code, score, why)``；没有可靠线索时 ``code`` 为 None（宁可不猜，也不硬指）。
    """
    stem = Path(str(material.get("source_file") or "")).stem
    for alias, code in _TYPE_ALIASES.items():
        if alias in stem:
            return code, 1.0, "文件名命中别名「%s」" % alias
    best_code, best_score = None, 0.0
    for code, entry in entries.items():
        score = _dice(stem, entry.get("name"))
        if score > best_score:
            best_code, best_score = code, score
    if best_code and best_score >= 0.5:
        return best_code, best_score, "文件名与材料名相似度 %.2f" % best_score
    head = "\n".join(str(x) for x in (material.get("text_lines") or [])[:12])
    for code, entry in entries.items():
        name = entry.get("name") or ""
        if name and name in head:
            return code, 0.9, "转写文本里出现材料名「%s」" % name
    return None, best_score, "没有可靠线索，需你判断"


def apply_type_guesses(input_dir, codes=None, write=True):
    """给还没有 ``material_type`` 的材料补一个猜测类型。

    只在空值时写入，**不覆盖**已有判定；模型改过的类型不会被冲掉。
    """
    _fields, material_types, _rules, _syn = load_rules()
    entries = material_entries(material_types)
    materials = load_materials(input_dir, codes)
    out = []
    for code, mat in sorted(materials.items()):
        if mat.get("material_type"):
            out.append({"material": code, "material_type": mat["material_type"],
                        "name": material_name(mat["material_type"], material_types),
                        "score": 1.0, "why": "已有类型（未改动）", "written": False})
            continue
        guess, score, why = guess_material_type(mat, entries)
        written = False
        if guess and write:
            mat["material_type"] = guess
            target = Path(input_dir) / ("%s.json" % code)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(mat, fh, ensure_ascii=False, indent=2)
            written = True
        out.append({"material": code, "material_type": guess,
                    "name": material_name(guess, material_types) if guess else None,
                    "score": round(score, 3), "why": why, "written": written})
    return out


# ==================================================================== 材料装载

# 产物文件名黑名单：这些是我们自己写出来的东西，不是材料。
# 真机上踩过：模型把 findings.json 写回了材料包目录，下一轮 load_materials 把它当成
# 一份材料装进来（材料清单里出现一行「findings | - | 未知材料 | 0」），
# 于是它被当成「无字段的来源」参与比对，报告里多出一份不存在的材料。
PRODUCT_FILENAMES = {"materials.json", "findings.json", "manifest.json"}


def _looks_like_product(data):
    """按结构判断这个 JSON 是不是我们自己的产物（而不是材料）。"""
    keys = set(data.keys())
    # findings.json：带 findings / summary / selected_codes / converged_out
    if keys & {"findings", "summary", "selected_codes", "converged_out"}:
        return True
    # 材料文件至少要有其中之一；三者都没有就不是材料
    return not (keys & {"text_lines", "fields", "material_code"})


def load_materials(input_dir, codes=None):
    """读材料目录下的全部材料文件。返回 {code: material}。

    `codes` 给定时只装载这些材料代号（**逻辑分笔**：从一堆材料里圈出本轮要核的那些），
    大小写不敏感。目录是平铺的，所以这一步不需要搬动任何文件。

    `codes` 里出现不存在的代号时**直接报错**，不静默忽略：真机上模型传了
    `--codes findings`（一个并不存在的代号），静默忽略让报告里凭空多出一行材料，
    而且没人发现。宁可让它当场失败。
    """
    wanted = {str(code).strip().upper() for code in (codes or []) if str(code).strip()}
    out = {}
    for path in sorted(Path(input_dir).glob("*.json")):
        if path.name in PRODUCT_FILENAMES:
            continue
        try:
            with open(path, "r", encoding="utf-8") as fh:
                data = json.load(fh)
        except Exception as exc:  # noqa: BLE001
            print("[warn] 跳过无法解析的 %s：%s" % (path.name, exc), file=sys.stderr)
            continue
        if not isinstance(data, dict):
            continue
        if _looks_like_product(data):
            print("[warn] 跳过产物文件（不是材料）：%s" % path.name, file=sys.stderr)
            continue
        code = data.get("material_code") or path.stem
        if wanted and code.upper() not in wanted:
            continue
        data["material_code"] = code
        data.setdefault("text_lines", [])
        data.setdefault("fields", [])
        out[code] = data
    if wanted:
        found = {code.upper() for code in out}
        unknown = sorted(wanted - found)
        if unknown:
            raise SystemExit(
                "未知材料代号：%s。可用的代号：%s。"
                "（代号来自材料文件里的 material_code 或文件名；"
                "产物文件 findings.json 不会被当成材料）"
                % ("、".join(unknown), "、".join(sorted(out)) or "（无）"))
    return out


def load_manifest(input_dir):
    path = Path(input_dir) / "materials.json"
    if not path.exists():
        return {}
    try:
        with open(path, "r", encoding="utf-8") as fh:
            return json.load(fh)
    except Exception as exc:  # noqa: BLE001
        print("[warn] materials.json 无法解析：%s" % exc, file=sys.stderr)
        return {}


def fingerprint(material):
    text = "\n".join(str(x) for x in (material.get("text_lines") or []))
    return "sha1:" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


# ==================================================================== inspect

_ORG_HINT = re.compile(
    r"[\u4e00-\u9fff（）()A-Za-z0-9·]{2,40}?"
    r"(?:有限公司|股份有限公司|有限责任公司|合伙企业|事务所|银行|证券|基金|中心)")


def _first_org_hint(material):
    """从转写文本里抓第一个机构名，作为「疑似同一笔业务」的线索。"""
    for line in material.get("text_lines") or []:
        match = _ORG_HINT.search(str(line))
        if match:
            return match.group(0)
    return ""


def suggest_groups(materials, manifest):
    """给一组「疑似同笔业务」的提示。**只提示不决策**，分组错不影响结论。

    线索优先级：材料自带的 hint_business_no（平台按文件名/目录名推测）> 转写文本里的机构名。
    两条线索都没有时归到「未定」，不硬猜。
    """
    declared = {m.get("code") or m.get("material_code"): m
                for m in (manifest.get("materials") or [])}
    groups: dict[str, dict] = {}
    for code, mat in sorted(materials.items()):
        hint = ((declared.get(code) or {}).get("hint_business_no")
                or mat.get("hint_business_no") or "")
        if hint and hint != "UNGROUPED":
            why = "文件名/目录名带这个前缀（平台推测，可能不准）"
        else:
            org = _first_org_hint(mat)
            if org:
                hint, why = org, "转写文本里出现同一个机构名"
            else:
                hint, why = "未定", "没有可印证的线索（只有一份，或文本里没有机构名）"
        item = groups.setdefault(hint, {"hint": hint, "codes": [], "why": why})
        item["codes"].append(code)
    return [groups[key] for key in sorted(groups)]


def inspect_materials(input_dir, codes=None):
    fields, material_types, _rules, _syn = load_rules()
    entries = material_entries(material_types)
    materials = load_materials(input_dir, codes)
    manifest = load_manifest(input_dir)
    declared = {m.get("code") or m.get("material_code"): m
                for m in (manifest.get("materials") or [])}

    items = []
    for code, mat in sorted(materials.items()):
        mtype = mat.get("material_type") or ""
        entry = entries.get(mtype) or {}
        lines = [str(x) for x in (mat.get("text_lines") or [])]
        guess_code, guess_score, guess_why = (None, 0.0, "") if mtype else guess_material_type(mat, entries)
        items.append({
            "material_code": code,
            "material_type": mtype or None,
            "type_guess": guess_code,
            "type_guess_why": guess_why or None,
            "type_guess_score": round(guess_score, 3) if not mtype else None,
            "name": entry.get("name") or material_name(mtype, material_types),
            "side": entry.get("side"),
            "carrier": entry.get("carrier"),
            "required": bool(entry.get("required")),
            "declared": bool(code in declared),
            "source_file": mat.get("source_file") or mat.get("file"),
            "kind": mat.get("kind"),
            "hint_business_no": (mat.get("hint_business_no")
                                 or (declared.get(code) or {}).get("hint_business_no")),
            "transcript_lines": len(lines),
            "field_count": len(mat.get("fields") or []),
            "unreadable_regions": len(mat.get("unreadable_regions") or []),
            "head": lines[:5],
            "fingerprint": fingerprint(mat),
            "expected_extract": list(entry.get("extract") or []),
        })

    present_types = {m["material_type"] for m in items if m["material_type"]}
    missing_required = [
        {"code": item["code"], "name": item["name"]}
        for code, item in sorted(entries.items())
        if item.get("side") == "customer" and item.get("required") is True
        and code not in present_types
    ]
    # 传了 --codes 表示「本轮只看这些」，没被选中的材料不该被报成「声明但缺失」
    declared_missing = [code for code in declared if code not in materials] if not codes else []

    return {
        "business_no": (manifest.get("business_no") or manifest.get("hint_business_no")
                        or Path(input_dir).name),
        "layout": manifest.get("layout") or "flat",
        "selected_codes": sorted(materials),
        "material_count": len(items),
        "materials": items,
        "suggested_groups": suggest_groups(materials, manifest),
        "missing_required": missing_required,
        "declared_but_absent": declared_missing,
        "summary": ("材料 %d 份；必备材料%s；声明但缺失 %d 份"
                    % (len(items), "齐备" if not missing_required else "缺 %d 份" % len(missing_required),
                       len(declared_missing))),
        "next_hint": ("suggested_groups 只是「疑似同笔业务」的提示，不是结论。"
                      "用 `inspect --codes D1,D2` 或 `compare --codes D1,D2` 圈定本轮要核的材料；"
                      "`rules --kind fields` 看字段定义，`extract` 抽字段，`compare` 比字段，"
                      "最后 `report` 出表（出表前会强制跑覆盖率自检）。"),
    }


# ==================================================================== rules

def query_rules(kind, key=None, group=None, rule=None, material=None, name=None):
    fields, material_types, rules, syn = load_rules()
    if kind == "fields":
        items = fields
        if key:
            exact = [f for f in items if key in (f.get("key"), f.get("name"))]
            # 宽容匹配：给中文片段或材料内路径也能查到，避免模型猜不中规则表的 key
            items = exact or [f for f in items
                              if key in str(f.get("name") or "")
                              or any((s or {}).get("path") == key for s in f.get("sources") or [])]
        if group:
            items = [f for f in items if f.get("system_group") == group]
        if rule:
            items = [f for f in items
                     if rule in (f.get("normalize") or []) or f.get("judge") == rule]
        if material:
            items = [f for f in items
                     if any((s or {}).get("material") == material for s in f.get("sources") or [])]
        return {"kind": "fields", "total": len(fields), "returned": len(items), "fields": items}
    if kind == "materials":
        entries = material_entries(material_types)
        items = list(entries.values())
        if key or material:
            target = key or material
            items = [e for e in items if e["code"] == target or e["name"] == target]
        return {"kind": "materials", "note": material_types.get("note"),
                "returned": len(items), "materials": items}
    if kind == "rules":
        return {"kind": "rules", "note": rules.get("note"), "rules": rules,
                "levels": rules.get("levels")}
    if kind == "synonyms":
        return {"kind": "synonyms", "note": syn.get("note"), "synonyms": syn}
    if kind == "index":
        # 紧凑索引：只给「有哪些字段、是否必核、适用哪些材料」，供规划阶段扫一眼
        rows = []
        for f in fields:
            rows.append({
                "key": f.get("key"),
                "name": f.get("name"),
                "group": f.get("system_group"),
                "required": bool(f.get("required")),
                "normalize": f.get("normalize") or [],
                "judge": f.get("judge"),
                "materials": [s.get("material") for s in f.get("sources") or []],
            })
        if group:
            rows = [r for r in rows if r["group"] == group]
        if name:
            rows = [r for r in rows if name in (r["name"] or "")]
        return {"kind": "index", "total": len(rows), "fields": rows}
    raise SystemExit("未知的 --kind：%s（可选 fields/materials/rules/synonyms/index）" % kind)


# ==================================================================== extract
# 确定性定位抽值：模型负责判断「这个材料该抽哪些字段」，脚本负责机械地定位与摘录，
# 保证产出的 evidence_quote 一定能原文命中（不给幻觉留口子）。

_SEP = re.compile(r"[:：\s]+")

# 常见字段的标签别名：规则表里没有材料内标签，这里补一层通用写法
LABEL_ALIASES = {
    "name": ["名称", "机构名称", "单位名称", "客户名称", "公司名称"],
    "english_name": ["英文名称", "英文名"],
    "type": ["类型", "机构类型", "企业类型", "公司类型"],
    "credit_code": ["统一社会信用代码", "信用代码", "统一社会信用代码/注册号"],
    "address": ["地址", "住所", "注册地址", "经营地址"],
    "legal_rep": ["法定代表人", "法人", "法定代表人姓名"],
    "registered_capital": ["注册资本", "注册资本(万元)", "注册资本（万元）"],
    "currency": ["币种", "货币种类", "币别"],
    "establish_date": ["成立日期", "成立时间", "注册日期"],
    "business_term": ["营业期限", "经营期限"],
    "business_scope": ["经营范围", "主营业务"],
    "registrar": ["登记机关", "发证机关", "登记机关名称"],
    "registration_date": ["登记日期", "发照日期", "核准日期"],
    "seal_text": ["公章", "公章文字"],
    "cert_type": ["证件类别", "证件类型"],
    "cert_no": ["证件编号", "证件号码", "证件号"],
    "phone": ["手机", "手机号", "手机号码", "联系电话"],
    "fixed_phone": ["固定电话", "座机", "固话"],
    "contact_address": ["联系地址", "通讯地址"],
    "nationality": ["国籍", "国别"],
    "gender": ["性别"],
    "birth_date": ["出生日期", "出生年月"],
    "beneficiary_type": ["受益人类型", "受益所有人类型"],
    "ownership_start_date": ["受益所有权形成日期", "形成日期", "起始日期"],
    "ownership_end_date": ["受益所有权终止日期", "终止日期", "结束日期"],
    "control_form": ["控制形式", "控制方式"],
    "control_content": ["控制内容", "控制说明"],
    "customer_name": ["客户名称", "机构客户名称"],
    "customer_short_name": ["机构简称", "客户简称"],
    "account_type": ["账户类型", "账户类别"],
    "capital_source": ["资金来源"],
    # 下面这批来自真样例：系统录入侧截图里这些栏位的写法与规则表字段名不同，
    # 不补别名就一个字段都抽不出来（S5「法代/控股股东」整屏用的是 姓名 / 证件编号 / 证件有效期）。
    "special_institution_category": ["特殊机构类别"],
    "cert_start_date": ["证件起始日期", "起始日期"],
    "cert_end_date": ["证件截止日期", "截止日期"],
    "cert_validity": ["证件有效期", "有效期限", "有效期"],
    "legal_rep_name": ["法定代表人", "法人姓名", "姓名", "负责人姓名"],
    "legal_rep_cert_type": ["证件类别", "证件类型"],
    "legal_rep_cert_no": ["证件编号", "证件号码", "证件号"],
    "legal_rep_cert_validity": ["证件有效期", "有效期限", "有效期"],
    "legal_rep_phone": ["联系电话", "手机", "手机号"],
    "controller_name": ["控股股东", "控股股东姓名", "姓名"],
    "controller_cert_type": ["证件类别", "证件类型"],
    "controller_cert_no": ["证件编号", "证件号码"],
    "controller_cert_validity": ["证件有效期", "有效期限"],
    "operator_name": ["经办人", "经办人姓名", "姓名"],
    "operator_cert_type": ["证件类别", "证件类型"],
    "operator_cert_no": ["证件编号", "证件号码"],
    "contact_name": ["联系人", "联系人姓名", "姓名"],
    "contact_cert_type": ["证件类别", "证件类型"],
    "contact_cert_no": ["证件编号", "证件号码"],
    "customer_short_name": ["机构简称", "客户简称"],
    "email": ["EMAIL", "电子邮箱", "邮箱"],
    "website": ["网站地址", "网址", "网站"],
    "registered_capital_wan": ["注册资本(万)", "注册资本（万）"],
    "office_address": ["办公地址"],
    "registered_address": ["注册地址"],
    # ---- 下面这批来自真样例的表单写法 ----------------------------------------
    # 系统录入侧与客户侧对同一栏位的叫法不同（「证件有效期」vs「证件有效期截止日期」），
    # 两张申请表还会把「类型及号码」写在同一格。不补这些别名，真机上大片字段抽不出来，
    # 判定就只能落到「全部来源均未读出」。
    "mobile": ["手机", "移动电话", "手机号", "手机号码"],
    "fax": ["传真", "单位传真"],
    "office_phone": ["单位电话", "办公电话"],
    "postal_code": ["邮政编码", "邮编"],
    "institution_name": ["机构名称", "机构（产品）全称", "单位名称", "客户名称"],
    "enterprise_name": ["企业名称", "公司名称"],
    "full_name": ["机构（产品）全称", "机构全称", "客户全称", "机构名称"],
    "cert_type": ["证件类别", "证件类型", "主要身份证明文件类别", "机构有效身份证明文件类型",
                  "身份证明文件类型"],
    "cert_no": ["证件编号", "证件号码", "证件号", "主要身份证明文件号码", "机构有效身份证明文件号码",
                "税务登记证号码", "公民身份号码", "身份证明文件号码"],
    "cert_validity": ["证件有效期", "有效期限", "有效期", "证件有效期截止日期", "证明文件有效期限",
                      "证件有效期截止日"],
    "cert_valid_from": ["证件有效期", "证件有效期起始日期", "证明文件有效期限"],
    "reg_address": ["注册地址", "主要身份证明文件注册地址"],
    "nationality": ["国籍", "国别", "国籍或地区", "国籍/地区"],
    "birth_date": ["出生日期", "出生年月", "出生"],
    "legal_rep_name": ["法定代表人", "法人姓名", "姓名", "负责人姓名", "负责人", "法定代表人或负责人"],
    "business_term_end": ["营业期限至", "营业期限自至", "营业期限"],
    "business_scope": ["经营范围", "主营业务", "经营范围（主营）"],
    "controller_name": ["控股股东", "控股股东姓名", "控股股东或实际控制人", "实际控制人"],
    "integrity_record": ["诚信记录", "不良诚信记录", "是否有来源于以下机构的不良诚信记录"],
    "operator_name": ["经办人", "经办人姓名", "开户代理人姓名", "姓名"],
    "institution_category": ["机构类别", "机构类型", "企业类别"],
    "issuing_authority": ["发证机关", "签发机关", "证明文件发证单位"],
    "credit_code": ["统一社会信用代码", "信用代码", "统一社会信用代码/注册号", "统一社会信用代码：",
                    "统一社会信用代码："],
    "registration_date": ["登记日期", "发照日期", "核准日期", "登记机关"],
    "establish_date": ["成立日期", "成立时间", "注册日期", "开业日期"],
    "seal_text": ["公章", "公章文字", "印章文字"],
    # 角色前缀字段（联系人/经办人/法定代表人/控股股东/开户代理人）在两张申请表里
    # 统一写作「身份证明文件类型 / 号码」，不能借通用字段的别名 —— 通用别名里有
    # 「机构有效身份证明文件类型」这种机构级标签，会把机构的营业执照读成联系人的证件类别。
    **{key: ["证件类别", "证件类型", "身份证明文件类型", "身份证明文件类别"]
       for key in ("contact_cert_type", "operator_cert_type", "legal_rep_cert_type",
                   "controller_cert_type", "principal_cert_type", "agent_cert_type")},
    **{key: ["证件编号", "证件号码", "证件号", "身份证明文件号码", "证件号码及有效期"]
       for key in ("contact_cert_no", "operator_cert_no", "legal_rep_cert_no",
                   "controller_cert_no", "principal_cert_no", "agent_cert_no")},
    **{key: ["证件有效期", "有效期限", "证件号码及有效期"]
       for key in ("contact_cert_validity", "operator_cert_validity", "legal_rep_cert_validity",
                   "controller_cert_validity", "principal_cert_validity", "agent_cert_validity")},
    "operator_gender": ["性别"],
}


def label_hints(path, material_type, fields, extra=None):
    """给材料内的字段 path 找候选标签：显式指定 > 规则表字段名 > 内置别名表。

    两类来源都要看，因为规则表只声明**客户侧**来源：
    - 客户侧材料：规则表里 `sources` 声明了 (材料类型, 材料内路径) 的字段，取其字段中文名；
    - 系统录入侧材料（基准方）：规则表不写 `sources`，按 `system_group` + 字段 key 后缀对应，
      取字段中文名 —— 漏了这条，5 张系统截图就一个字段都抽不出来。

    返回按**标签长度降序**排列：同一格里可能同时命中长标签与它的子串
    （`证件有效期截止日期` 同时含 `证件有效期`），先试长的那条才不会把标签后半截当成值。
    """
    hints = []
    if extra:
        hints.extend([extra] if isinstance(extra, str) else list(extra))
    for f in fields:
        for src in f.get("sources") or []:
            if src.get("material") == material_type and src.get("path") == path:
                if f.get("name"):
                    hints.append(f["name"])
        if f.get("system_group") == material_type:
            suffix = f["key"].split(".", 1)[1] if "." in f["key"] else f["key"]
            if suffix == path and f.get("name"):
                hints.append(f["name"])
    hints.extend(LABEL_ALIASES.get(path, []))
    seen, out = set(), []
    for h in hints:
        h = str(h).strip()
        if not h or h in seen:
            continue
        seen.add(h)
        out.append(h)
        # 「证件有效期（起始日）」这类带括号说明的字段名，真实标签往往只写到括号前
        base = re.split(r"[（(]", h)[0].strip()
        if base and base not in seen:
            seen.add(base)
            out.append(base)
    out.sort(key=lambda x: len(nz.n_text(x)), reverse=True)
    return out


# --------------------------------------------------------------- 标签格 / 勾选项
# 真样例里的三种写法，靠「值一定长得不像标签」这一条判据统一处理：
#   1. 整格就是标签，值在右邻格（`国籍/地区 | 中国`）；
#   2. 一格塞了两个标签（`法定代表人或负责人`、`国籍或地区`），标签的后半截不是值；
#   3. 一格塞了「类型 + 号码」（`营业执照 91350100260200025B`），号码不属于枚举字段。
_LABEL_CONNECTORS = ("或", "和", "/", "／", "、", "及", "与", "兼")
_TRAILING_PUNCT = " \t　:：,，;；.。?？!！-—|"
_LABEL_BOUNDARY = set(" \t　|,，;；、/／-—")
_LONG_DIGITS = re.compile(r"\d{8,}")


def _label_set(labels, extra=None):
    """把标签集合归一化成可比较的集合，并补上「去括号说明」的主体写法。

    去括号后的主体要够长才收：`机构（产品）全称` 的主体是 `机构`，它太泛，
    收进来会把 `境内机构 | 一般法人机构 | …` 这种勾选行整个判成标签行。
    """
    out = set()
    for item in list(labels or []) + list(extra or []):
        if not item:
            continue
        text = str(item)
        t = nz.n_text(text)
        if t:
            out.add(t)
        base = nz.n_text(re.split(r"[（(]", text)[0])
        if len(base) >= 3:
            out.add(base)
    return out


def _is_label_text(text, labels):
    """整格是不是一个栏位标签（含「标签+连接词」的合并写法、被转写截断的标签）。

    被截断的标签也要认（`实际控制备案主体的统一社` 是 `…统一社会信用代码` 的前半截）——
    不认就会把下一个字段的标签当成上一个字段的值。长度下限 6 是为了不误伤短值：
    `机构` 是 `机构简称` 的前缀，但它本身就是一个正常的取值。
    """
    if not labels:
        return False
    t = nz.n_text(text).strip(_TRAILING_PUNCT)
    if not t:
        return True
    if t in labels:
        return True
    parts = [p for p in re.split("|".join(re.escape(c) for c in _LABEL_CONNECTORS), t) if p]
    if len(parts) > 1 and all(p in labels for p in parts):
        return True
    return len(t) >= 6 and any(lab.startswith(t) for lab in labels)


def _is_merged_label(text, labels):
    """一格塞了两个及以上标签（`法定代表人或负责人`）—— 值要越过下一个标签格。"""
    if not labels:
        return False
    t = nz.n_text(text).strip(_TRAILING_PUNCT)
    parts = [p for p in re.split("|".join(re.escape(c) for c in _LABEL_CONNECTORS), t) if p]
    return len(parts) > 1 and all(p in labels for p in parts)


def _is_label_tail(rest, labels):
    """同格取到的 rest 其实是「标签的后半截」，不是值。

    只认三种情形：以连接词开头（`或地区`、`及有效期`）、本身就是标签、是某个标签的尾部
    （`地区` ⊂ `国籍/地区`）。**不做「是某个标签的子串」判断** —— `身份证` 是
    `机构有效身份证明文件号码` 的子串，那条判据会把真值也误杀。
    """
    if not rest:
        return True
    t = nz.n_text(rest).strip(_TRAILING_PUNCT)
    if not t:
        return True
    if t[0] in _LABEL_CONNECTORS:
        return True
    if t in labels:
        return True
    return any(lab.endswith(t) for lab in labels)


def _tail_owns_next_cell(rest, labels):
    """标签尾本身是**另一个完整字段的标签**时，右邻格属于它、不属于本字段。

    `注册地址国籍/地区 | 中国`：本字段是 注册地址，右邻格是 `国籍/地区` 的值，不能取。
    `国籍或地区 | 中国`：`地区` 只是 `国籍/地区` 的碎片，右邻格就是本字段的值，要取。
    区别就在后半截以连接词开头（碎片）还是本身就是完整标签（另一个字段）。
    """
    t = nz.n_text(rest).strip(_TRAILING_PUNCT)
    if not t or t[0] in _LABEL_CONNECTORS:
        return False
    return t in labels


def _pick_checked(value):
    """勾选串里取被 ☑ 选中的那一项（`☑无 □有` → `无`）。

    整串全是 □ 时说明这一栏没勾选，返回空 —— 没勾选就是没值，不能把选项清单当取值。
    """
    if not value or "☑" not in value:
        if value and value.count("□") >= 2 and "☑" not in value:
            return ""
        return value
    tail = value[value.rindex("☑") + 1:]
    picked = re.split(r"[□☐☑]", tail)[0].strip(_TRAILING_PUNCT)
    return picked or value


def _cut_enum_number(value):
    """枚举值尾部粘连号码时只取枚举（`营业执照 91350100260200025B` → `营业执照`）。"""
    if not value:
        return value
    m = _LONG_DIGITS.search(value)
    if m and m.start() > 0:
        head = value[:m.start()].strip(_TRAILING_PUNCT)
        if head:
            return head
    return value


def _find_label(cell, label):
    """在单元格里找标签的位置。转写会把标签拆开（`姓 名 测试`），退化为容忍空格。"""
    pos = cell.find(label)
    if pos >= 0:
        return pos, len(label)
    pattern = r"\s*".join(re.escape(c) for c in label if not c.isspace())
    m = re.search(pattern, cell)
    if m:
        return m.start(), m.end() - m.start()
    return -1, 0


# 单值枚举字段：值里再出现别的词（`男 民族 汉`）时只取第一个词。
# 这些栏位的取值是枚举，后面跟的多半是相邻栏位的内容，不是同一个值。
_FIRST_TOKEN_PATHS = {
    "gender", "cert_type", "currency", "nationality", "listed", "org_flag",
    "risk_level", "mail_method", "capital_nature", "state_owned",
    "institution_category", "enterprise_category",
}


def _first_token(value):
    if not value:
        return value
    parts = re.split(r"\s+", value)
    return parts[0] if len(parts) > 1 else value


# 整份材料就是值本身的字段：印章图没有栏目标签，转写内容就是印文
_WHOLE_TEXT_PATHS = {"seal_text"}


def _enum_from_text(lines, syn, key):
    """整份材料里只出现了一个枚举写法时就用它当值。

    自然人身份证件的「证件类别」就是这么来的：证件正面只印「居民身份证」，
    并没有「证件类别」这个栏目；出现两个及以上枚举组（营业执照 + 身份证）时不猜。
    """
    groups = syn.get(key) or []
    if not groups:
        return None
    # 只认「短行」上的枚举写法：身份证正面就是孤零零一行「居民身份证」；
    # 排查记录表里那行佐证材料清单也含「营业执照」，但它不是证件类别。
    text = nz.n_text("\n".join(str(x) for x in lines if len(str(x).strip()) <= 20))
    hits = {i for i, group in enumerate(groups)
            for item in group if nz.n_text(item) and nz.n_text(item) in text}
    if len(hits) != 1:
        return None
    for item in groups[hits.pop()]:
        if nz.n_text(item) in text:
            return item
    return None


def _at_label_boundary(cell, pos):
    """标签必须「独立成词」：`证件起始日期` 里的 `起始日期` 是另一个字段名的一部分，
    不能当本字段的标签用（否则 受益所有权形成日期 会取到 证件起始日期 的值）。"""
    if pos <= 0:
        return True
    prev = cell[pos - 1]
    if prev in _LABEL_BOUNDARY or prev in _LABEL_CONNECTORS or prev.isspace():
        return True
    return not (prev.isalnum() or _CJK.match(prev))


# 标签之后允许出现的字符：值分隔符、连接词、标点、结尾
_LABEL_TAIL_CHARS = set(":：,，;；.。?？!！()（）[]【】")


def _label_ok(cell, pos, span):
    """标签匹配要同时满足两条：前面独立成词、后面不粘着别的词。

    后半条挡住 `营业期限信息`（栏目标题，不是 `营业期限` + 值）这类误判：
    标签后面紧跟的必须是一个分隔符、连接词、标点或行尾。
    """
    if not _at_label_boundary(cell, pos):
        return False
    end = pos + span
    if end >= len(cell):
        return True
    nxt = cell[end]
    return (nxt.isspace() or nxt in _LABEL_BOUNDARY or nxt in _LABEL_CONNECTORS
            or nxt in _LABEL_TAIL_CHARS)


def _first_value_cell(cells, start, labels, noise, skip_labels):
    """从 start 起找第一个「非标签格」。

    `skip_labels=False`：右邻格是标签就说明本字段这一行没有值（`机构类别 | 机构简称 | X`）；
    `skip_labels=True`：本格是合并标签，要越过后面那个标签格再取值（`法定代表人或负责人 | 姓名 | 测试`）。
    """
    for i in range(start, len(cells)):
        raw = cells[i]
        if not raw:
            continue
        if _is_label_text(raw, labels):
            if skip_labels:
                continue
            return None
        value = _pick_checked(strip_row_noise(raw, noise))
        if value:
            return value
        return None
    return None


def _below_line_value(lines, idx, labels, span=6):
    """标签行下面：① 单独的纯值行（营业执照的信用代码/登记日期）；② 勾选组（☑）。

    真样例里这两种写法很常见：标签一行、值另起一行。跨行取值只认「整行就是值」
    （无表格分隔符、不是标签、含数字）或「整行是勾选项」，避免把下一行的其它字段吃进来。
    """
    for j in range(idx, min(idx + span, len(lines))):
        text = str(lines[j]).strip()
        if not text:
            continue
        if _is_split_label_tail(lines, j, labels):
            continue
        if "☑" in text:
            # 勾选组本身带别的栏位标签时说明那是另一行的值（`资本属性 | ☑境内资本 …`），
            # 不能拿来当本字段的取值。
            if any(lab and lab in nz.n_text(text) for lab in labels):
                continue
            picked = _pick_checked(text)
            if picked and picked != text and not _is_label_tail(picked, labels):
                return picked, text, "第 %d 行" % (j + 1)
            continue
        if "|" in text or len(text) < 4 or not re.search(r"[0-9]", text):
            continue
        # 「整行就是值」的前提：这一行里没有任何已知标签 —— 否则它其实是
        # 「标签 + 值」（`证件编号 330381198905291621`），吃进来就成了假值。
        if _is_label_text(text, labels):
            continue
        if any(nz.n_text(lab) and nz.n_text(lab) in nz.n_text(text) for lab in labels):
            continue
        return text, text, "第 %d 行" % (j + 1)
    return None, None, None


def _joined_label_value(lines, idx, keys, raw_labels, lset, noise):
    """标签被版面换行拆开：`联系人证件有效` + 下一行 `期 20260902 ~ 20460902`。

    只在本行是某个标签的**严格前缀**、且下一行以该标签的剩余部分开头时才认，
    判据完全来自标签本身，不做模糊拼接。
    """
    if idx >= len(lines):
        return None
    head_line = str(lines[idx - 1])
    if "|" in head_line:
        return None
    head = nz.n_text(head_line)
    if len(head) < 4:
        return None
    tail = str(lines[idx])
    for key in keys:
        k = nz.n_text(key)
        if not k.startswith(head) or len(head) >= len(k):
            continue
        rest = k[len(head):]
        if not tail.startswith(rest):
            continue
        value = _SEP.sub(" ", tail[len(rest):]).strip()
        value = cut_at_next_label(value, raw_labels, key)
        value = _pick_checked(strip_row_noise(value, noise))
        if value and not _is_label_tail(value, lset):
            return (value, tail, "第 %d 行" % idx, key, "split_label", None)
    return None


def _is_split_label_tail(lines, j, labels):
    """第 j 行是不是上一行「被换行拆开的标签」的续行（`联系人证件有效` + `期 …`）。

    这种续行会被「标签一行、值一行」的兜底误当成值（真机上抽出来过 `期`）。
    """
    if j <= 0 or j >= len(lines):
        return False
    head = nz.n_text(str(lines[j - 1]))
    if len(head) < 4:
        return False
    line = str(lines[j])
    return any(lab.startswith(head) and len(lab) > len(head) and line.startswith(lab[len(head):])
               for lab in labels)


def locate_value(lines, hints, anchor=None, labels=None, noise=None, label_set=None):
    """在转写文本里定位字段值。

    返回 `(value, evidence_quote, position, label_used, method, hint)`。
    `hint` 是 `{"truncated": bool, "continuation": str}`：值落在行尾、且下一行看起来
    是版面换行切出来的续行时置位。**这里只提示、不自动拼接** —— 版面换行与「下一行是
    另一个字段」在纯文本里区分不了（`姓名 测试` 的下一行就是 `性别 男 民族 汉`），
    自动拼接会把两个字段连成一个假值。拼不拼由模型看着续行内容自己定。

    五条来自真样例的清洗（判据都来自规则表与材料自身，不新增硬编码）：
    - `labels`：系统侧一行里塞了多个字段（`客户名称 X AI 机构简称 Y`），值止于下一个已知标签；
    - `noise`：系统侧行尾带界面控件文字（样例里是 `AI` 补录按钮），它不是字段值；
    - 标签格与标签尾：`国籍/地区`、`法定代表人或负责人`、`证件有效期截止日期` 这类整格就是
      标签，值在右邻格；把标签后半截当值会造出「国籍=/地区」「法定代表人姓名=或负责人」；
    - 勾选组与「类型+号码」粘连格：只取 ☑ 项、只取枚举部分；
    - 换行拆开的标签与「标签一行、值一行」：`联系人证件有效` + `期 20260902 ~ …`。
    """
    keys = ([anchor] if anchor else []) + list(hints)
    lset = label_set if label_set is not None else _label_set(labels)
    for idx, line in enumerate(lines, start=1):
        # 0) 标签被换行拆开
        joined = _joined_label_value(lines, idx, keys, labels, lset, noise)
        if joined:
            return joined
        cells = [c.strip() for c in str(line).split("|")]
        for cell_idx, cell in enumerate(cells):
            for label in keys:
                if not label or nz.n_text(label) not in nz.n_text(cell):
                    continue
                # 1) 标签与值在同一单元格：取标签之后的内容
                pos, span = _find_label(cell, label)
                if pos < 0 or not _label_ok(cell, pos, span):
                    continue
                rest = _SEP.sub(" ", cell[pos + span:]).strip()
                rest = strip_row_noise(cut_at_next_label(rest, labels, label), noise)
                rest = _pick_checked(rest)
                if rest and not _is_label_tail(rest, lset):
                    return (rest, str(line), "第 %d 行" % idx, label, "same_cell",
                            _tail_hint(lines, idx, rest, keys))
                if rest and _tail_owns_next_cell(rest, lset):
                    # 这一行是另一个字段的（`注册地址国籍/地区 | 中国`），本字段在这里没有值
                    continue
                # 2) 右邻单元格（合并标签时越过下一个标签格）
                value = _first_value_cell(cells, cell_idx + 1, lset, noise,
                                          skip_labels=_is_merged_label(cell, lset))
                if value:
                    return (value, str(line), "第 %d 行" % idx, label, "next_cell",
                            _tail_hint(lines, idx, value, keys))
                # 3) 标签行下面：单独的纯值行 / 勾选组
                value, quote, position = _below_line_value(lines, idx, lset)
                if value:
                    return (value, quote, position, label, "next_line",
                            _tail_hint(lines, idx, value, keys))
    return None, None, None, None, "not_found", None


# 行尾噪声判据：短、纯 ASCII、非纯数字的 token 在同一材料的多行里反复出现 —— 那是界面
# 控件文字（样例里的系统侧截图每行都带 `AI` 补录按钮），不是字段值。不剔掉会污染几乎
# 每个取值。三条限制都是为了不误伤真值：中文值不算（排除），纯数字如日期/证件号不算
# （排除），长 token 不算（证件号、统一社会信用代码都在 8 位以上）。
_NOISE_MIN_LINES = 3
_NOISE_MIN_RATIO = 0.4
_NOISE_MAX_CHARS = 8
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _is_noise_candidate(token):
    if not token or len(token) > _NOISE_MAX_CHARS:
        return False
    if _CJK.search(token):
        return False
    if token.isdigit():
        return False
    return any(ch.isalnum() or ch.isalpha() for ch in token)


def detect_row_noise(lines):
    """从材料自身的转写文本里识别界面控件文字。返回要去掉的 token 集合。"""
    counts, total = {}, 0
    for line in lines:
        text = nz.clean(line)
        if not text:
            continue
        total += 1
        for token in set(re.split(r"\s+", text)):
            if _is_noise_candidate(token):
                counts[token] = counts.get(token, 0) + 1
    if total < _NOISE_MIN_LINES:
        return set()
    return {tok for tok, count in counts.items()
            if count >= _NOISE_MIN_LINES or count / total >= _NOISE_MIN_RATIO}


def strip_row_noise(value, noise):
    if not value or not noise:
        return value
    kept = [part for part in re.split(r"\s+", value) if part not in noise]
    return " ".join(kept).strip()


def cut_at_next_label(rest, labels, current=None):
    """值止于下一个已知标签之前（系统侧一行可能塞了多个字段）。

    只在标签「独立成词」时才切断：`证件地址 X AI 联系地址 Y` 里 `联系地址` 前面是空格，
    是真标签；而 `简化识别-特定机构的法定代表人、实际控制人…` 里 `法定代表人` 前面是
    「的」，只是值的一部分，按子串切断会把真值截短（真机上截成过 `简化识别-特定机构的`）。
    """
    if not rest or not labels:
        return rest
    best = None
    for label in labels:
        if not label or label == current:
            continue
        start = 0
        while True:
            index = rest.find(label, start)
            # index == 0 是当前标签自身的子串（`法人` vs `法定代表人`），不能当边界
            if index <= 0:
                break
            if rest[index - 1] in _LABEL_BOUNDARY or rest[index - 1].isspace():
                if best is None or index < best:
                    best = index
                break
            start = index + 1
    return rest[:best].strip() if best else rest


# 续行判据：短、不含表格分隔符、不自带任何候选标签。
# 只用来给模型一个「这里可能被换行切断了」的提示，不参与取值。
_CONTINUATION_MAX_CHARS = 40


def _tail_hint(lines, idx, value, keys):
    """值是否可能被版面换行截断；是则附上候选续行。"""
    if idx >= len(lines):
        return None
    tail = nz.clean(lines[idx])
    if not tail or "|" in tail or len(tail) > _CONTINUATION_MAX_CHARS:
        return None
    if any(k and nz.n_text(k) in nz.n_text(tail) for k in keys if k):
        return None
    return {"truncated": True, "continuation": tail,
            "joined": "%s%s" % (value, tail),
            "note": "值落在行尾、下一行疑似版面换行切出的续行；拼不拼由你判断"}


def _extract_paths(lines, mtype, paths, fields, label_map=None, noise=None,
                   all_labels=None, label_set=None, syn=None):
    """对一份材料的一组字段做确定性定位。返回 `(produced, missing)`。

    `labels` 用标签全集（原始写法），用于把「一行塞了多个字段」的值截到下一个标签之前；
    `label_set` 是同一批标签的归一化集合，用于判断「整格是不是标签」；
    `noise` 是行尾界面控件文字。判据都来自规则表与材料自身。
    """
    label_map = label_map or {}
    names = [f.get("name") for f in fields if f.get("name")]
    aliases = [a for group in LABEL_ALIASES.values() for a in group]
    if all_labels is None:
        all_labels = set(names) | set(aliases)
    if label_set is None:
        label_set = _label_set(names, aliases)
    if noise is None:
        noise = detect_row_noise(lines)
    produced, missing = [], []
    for path in paths:
        hints = label_hints(path, mtype, fields, label_map.get(path))
        value, quote, position, used, method, hint = locate_value(
            lines, hints, labels=all_labels, noise=noise, label_set=label_set)
        if value is None and path in _WHOLE_TEXT_PATHS and len(
                [x for x in lines if str(x).strip()]) <= 3:
            # 印章图的转写内容就是印章文字本身，没有「印章文字：」这样的栏目标签；
            # 取不到时用整份材料里最长的一行当印文（也是 P-01 判非本户的名称依据）。
            value = max((str(x).strip() for x in lines if str(x).strip()), key=len)
            quote, position, used, method, hint = value, "", "text", "whole_text", None
        if value is None and path == "cert_type":
            # 证件类别兜底：整份材料只出现一种证件枚举时用它（身份证正面没有「证件类别」栏）
            guess = _enum_from_text(lines, syn or {}, "cert_type")
            if guess:
                value, quote, position, used, method, hint = (
                    guess, next((str(x) for x in lines if nz.n_text(guess) in nz.n_text(x)), ""),
                    "", "text_enum", "text_enum", None)
        if value is None:
            missing.append({"key": path, "reason": "未在转写文本里定位到该字段（可能该栏为空或标签写法不同）",
                            "hints": hints})
            continue
        if path in _FIRST_TOKEN_PATHS:
            value = _first_token(value)
        if path.endswith("cert_type") or path in _FIRST_TOKEN_PATHS:
            # 枚举类字段：表单把「类型及号码」写在同一格时只取枚举部分
            value = _cut_enum_number(value)
        if path.endswith("cert_no") or path in ("credit_code", "org_code", "tax_no"):
            # 号码类字段：一格塞了「号码/有效期」时只取号码
            value = re.split(r"[/／]", value)[0].strip()
        masked = bool(re.search(r"[*＊?？]", value))
        item = {
            "key": path,
            "value": value,
            "evidence_quote": quote,
            "readable": not masked,
            "confidence": 0.99 if method == "next_cell" else 0.9,
            "position": position,
            "label_used": used,
            "method": method,
        }
        if hint:
            item["truncated"] = True
            item["continuation"] = hint["continuation"]
            item["joined_candidate"] = hint["joined"]
            item["confidence"] = 0.75      # 可能被换行截断，置信度下调
        produced.append(item)
    return produced, missing


def extract_for_material(input_dir, material_code, only_paths=None, label_map=None,
                         write=False, entries=None):
    fields, material_types, _rules, _syn = load_rules()
    entries = entries or material_entries(material_types)
    materials = load_materials(input_dir)
    if material_code not in materials:
        raise SystemExit("材料目录里没有 %s（现有：%s）"
                         % (material_code, "、".join(sorted(materials)) or "空"))
    mat = materials[material_code]
    mtype = mat.get("material_type") or ""
    lines = [str(x) for x in (mat.get("text_lines") or [])]

    wanted = list(only_paths) if only_paths else list((entries.get(mtype) or {}).get("extract") or [])
    if not wanted:
        wanted = [f.get("key") for f in mat.get("fields") or [] if f.get("key")]

    produced, missing = _extract_paths(lines, mtype, wanted, fields, label_map, syn=_syn)

    if write and produced:
        by_key = {f.get("key"): f for f in mat.get("fields") or []}
        for item in produced:
            by_key[item["key"]] = item
        mat["fields"] = list(by_key.values())
        target = Path(input_dir) / ("%s.json" % material_code)
        with open(target, "w", encoding="utf-8") as fh:
            json.dump(mat, fh, ensure_ascii=False, indent=2)

    return {
        "material": material_code,
        "material_type": mtype,
        "requested": len(wanted),
        "extracted": len(produced),
        "fields": produced,
        "missing": missing,
        "written": bool(write and produced),
        "note": ("值来自转写文本的确定性定位，evidence_quote 为整行原文，可直接过证据闸门；"
                 "readable=false 表示值里含掩码符号（* / ?），需人工核对。"),
    }


def extract_materials(input_dir, codes=None, entries=None, only_missing=True, write=True):
    """对材料包里每份材料批量跑确定性抽值（`audit` 的前置步骤）。

    为什么必须批量抽：**系统录入侧（M-SYS-*）截图同样是核对对象**，它的值不抽出来，
    `run_compare` 就取不到基准方取值，比对会静默退化成「客户材料互相印证」——
    真机上模型只对客户侧材料跑了 extract，5 张系统截图 fields 全空，24 个系统侧
    字段全部落空，而且报告还印着「基准方，提供核对字段全集」，看不出问题在哪。
    这是判定准确率的最大单点，且是纯本地毫秒级操作。

    `only_missing=True` 时只补材料自身还没有的字段，不覆盖已有值（模型手工修正过的优先）。
    `write=False` 只算不落盘（评测用）。
    """
    fields, material_types, _rules, _syn = load_rules()
    entries = entries or material_entries(material_types)
    materials = load_materials(input_dir, codes)
    report = {}
    for code, mat in sorted(materials.items()):
        mtype = mat.get("material_type") or ""
        lines = [str(x) for x in (mat.get("text_lines") or [])]
        all_paths = list((entries.get(mtype) or {}).get("extract") or [])
        if not all_paths:
            all_paths = [f.get("key") for f in mat.get("fields") or [] if f.get("key")]
        have = {f.get("key") for f in mat.get("fields") or []
                if f.get("key") and f.get("value") not in (None, "")}
        wanted = [p for p in all_paths if p and (p not in have if only_missing else True)]
        produced, missing = _extract_paths(lines, mtype, wanted, fields, syn=_syn)
        written = False
        if write and produced:
            by_key = {f.get("key"): f for f in mat.get("fields") or [] if f.get("key")}
            for item in produced:
                by_key[item["key"]] = item
            mat["fields"] = list(by_key.values())
            target = Path(input_dir) / ("%s.json" % code)
            with open(target, "w", encoding="utf-8") as fh:
                json.dump(mat, fh, ensure_ascii=False, indent=2)
            written = True
        # 产率 = 本次请求里真正取到值的比例。系统侧截图本来就有值，产率低说明
        # 标签没对齐或噪声没剔干净 —— 必须显式告警，不能静默留空。
        item = {
            "material_type": mtype,
            "side": "system" if str(mtype).startswith("M-SYS-") else "customer",
            "requested": len(wanted),
            "extracted": len(produced),
            "yield": round(len(produced) / len(wanted), 3) if wanted else None,
            "written": written,
            "missing_keys": [m["key"] for m in missing],
        }
        if wanted and len(produced) / len(wanted) < 0.5:
            item["warning"] = ("抽值产率偏低（%d/%d）：该材料的栏位标签与规则表可能没对齐，"
                               "或这一栏确实多为空，请人工看一眼" % (len(produced), len(wanted)))
            print("[warn] %s（%s）%s" % (code, mtype, item["warning"]), file=sys.stderr)
        report[code] = item
    return report


# ==================================================================== compare

def keep_codes(materials, seal_keys=None):
    """P-04 多版本收敛：同一材料类型有多份时，只保留一份参与比对。

    不收敛就会出错：`build_index` 的键是 (material_type, path)，同类型多版本会互相
    静默覆盖，最后取到哪一份全看字典遍历顺序 —— 报出来的「只取已签章版本」与实际
    参与比对的材料可能不是同一份。收敛规则：有签章的取签章件，都无签章取代号最小的一份。

    `seal_keys` 由规则表补充（判定编号 J-07 引用的材料内路径），业务新增签章字段时
    不用改代码。
    """
    by_type = {}
    for code, mat in materials.items():
        by_type.setdefault(mat.get("material_type"), []).append(code)
    keep = set()
    for _mtype, codes in by_type.items():
        if len(codes) < 2:
            keep.update(codes)
            continue
        signed = [c for c in codes if jd.is_signed(materials[c], seal_keys)]
        keep.add(signed[0] if signed else sorted(codes)[0])
    return keep


def build_index(materials):
    """{(material_type, path): {...}}，side 区分系统录入侧（基准方）与客户提交侧（对照方）。"""
    index = {}
    for code, mat in materials.items():
        mtype = mat.get("material_type") or ""
        side = "system" if mtype.startswith("M-SYS-") else "customer"
        for item in mat.get("fields") or []:
            key = item.get("key")
            if not key:
                continue
            index[(mtype, key)] = {
                "material": code,
                "material_name": mtype,
                "side": side,
                "path": key,
                "value": item.get("value"),
                "readable": item.get("readable"),
                "confidence": item.get("confidence"),
                "position": item.get("position"),
                "evidence_quote": item.get("evidence_quote"),
            }
    return index


def select_fields(fields, keys=None, groups=None):
    """按字段 key / 字段名 / 名称片段 / 材料内路径（`M-CUST-01:cert_no`）挑字段。

    宽容匹配是刻意的：模型说「核一下证件编号」时可能给中文名、给片段、给材料内路径，
    不该因为它没背下规则表里的 key 就挑不出字段。
    """
    out = list(fields)
    if groups:
        want = {g.strip() for g in groups if g.strip()}
        out = [f for f in out if f.get("system_group") in want]
    if not keys:
        return out
    matched = []
    for raw in keys:
        k = raw.strip()
        if not k:
            continue
        if ":" in k and not k.startswith("sys"):
            mtype, path = k.split(":", 1)
            for f in fields:
                if any((s or {}).get("material") == mtype and (s or {}).get("path") == path
                       for s in f.get("sources") or []) and f not in matched:
                    matched.append(f)
            continue
        # 先精确命中：说「证件编号」就该只挑证件编号，不能把「联系人证件编号」也带上。
        # 精确命中为空才退回片段匹配（与 query_rules 的宽容口径一致）。
        exact = [f for f in fields if k in (f.get("key"), f.get("name"))]
        hits = exact or [f for f in fields if k in str(f.get("name") or "")]
        for f in hits:
            if f not in matched:
                matched.append(f)
    return matched


def missing_required_materials(materials, material_types):
    """本轮缺了哪些**必备**客户侧材料。返回 [{code, name}]，按规则表顺序。

    必备性由规则表 `required is True` 决定（`conditional` / `reference` / `false`
    不算缺失）。判定与 `jd.precheck_completeness` 同一口径，只是把清单单独取出来 ——
    核对表第一节要按缺口归并「因缺哪份材料而无法比对」，光有一条 P-03 说明不够用。
    """
    present_types = {m.get("material_type") for m in materials.values()}
    return [{"code": item["code"], "name": item.get("name") or item["code"]}
            for item in material_types.get("customer_side", [])
            if item.get("required") is True and item["code"] not in present_types]


def prechecks(materials, material_types):
    issues = []
    issues += jd.precheck_entity(materials)
    issues += jd.precheck_internal(materials)
    issues += jd.precheck_versions(materials, material_types)
    issues += jd.precheck_completeness(materials, material_types)
    # 注意：这里**不再**补一条笼统的「必备材料未齐备」P-03。
    # jd.precheck_completeness 已经逐份列出了「必备材料缺失：X」，再补一条
    # material="-" 的汇总会把同一次缺失报两遍（真机产物里就是两条重复的 P-03）。
    # 缺口的完整清单走 payload["missing_required"]，由核对表第一节呈现。
    for note in jd.precheck_same_person([], materials):
        issues.append({"type": "P-05", "level": "L3", "material": "-", "detail": note})
    return issues


# 印章 / 签字字段分两类，用于核对表的「材料清单及盖章情况」一节。
_STAMP_KEYS = ("company_stamp_text", "party_a_seal", "party_a_stamp", "business_stamp",
               "agency_seal", "institution_seal", "seal", "seal_text")
_SIGN_KEYS = ("applicant_signature", "operator_signature", "reviewer_signature",
              "customer_signature")
_VISUAL_UNCLEAR = ("看不清", "模糊", "无法辨认")


def _visual_state(values):
    """印章/签字三态：有 / 无 / 看不清。值来自材料自己的 fields。"""
    if not values:
        return "-"
    for value in values:
        if any(token in str(value) for token in _VISUAL_UNCLEAR):
            return "看不清"
    if any(nz.empty_state(value) == "value" for value in values):
        return "有"
    return "无"


def seal_overview(materials):
    """每份材料的盖章 / 签字情况（只出三态，不给一致性结论）。

    取的是材料自身 fields 里的签章类字段（判据复用 `judge.is_seal_key` 的清单 +
    命名 hint），所以业务新增签章字段时不用改代码。
    """
    out = []
    for code, mat in sorted(materials.items()):
        stamp_values, sign_values = [], []
        for item in mat.get("fields") or []:
            key = item.get("key")
            if not jd.is_seal_key(key):
                continue
            value = item.get("value")
            if key in _SIGN_KEYS or "signature" in str(key).lower():
                sign_values.append(value)
            else:
                stamp_values.append(value)
        out.append({
            "material": code,
            "material_type": mat.get("material_type"),
            "stamp": _visual_state(stamp_values),
            "signature": _visual_state(sign_values),
            "unreadable_regions": len(mat.get("unreadable_regions") or []),
        })
    return out


def run_compare(input_dir, keys=None, groups=None, business_no=None, codes=None):
    fields, material_types, _rules, syn = load_rules()
    materials = load_materials(input_dir, codes)
    material_issues = prechecks(materials, material_types)
    # P-04：先把多版本收敛掉再建索引，否则同类型多版本会互相覆盖
    kept = keep_codes(materials, jd.seal_paths_from_rules(fields))
    dropped = sorted(set(materials) - kept)
    index = build_index({c: m for c, m in materials.items() if c in kept})
    excluded = {issue["material"] for issue in material_issues
                if issue["type"] == "P-01" and issue.get("material")}

    findings = []
    for field in select_fields(fields, keys, groups):
        sources = []
        sys_key = field["key"].split(".", 1)[1] if "." in field["key"] else field["key"]
        sys_src = index.get((field.get("system_group"), sys_key))
        if sys_src:
            sources.append(sys_src)
        for src in field.get("sources") or []:
            item = index.get((src["material"], src["path"]))
            if item is None or item["material"] in excluded:
                continue
            sources.append(item)
        findings.append(jd.judge_field(field, sources, syn))

    manifest = load_manifest(input_dir)
    entries = material_entries(material_types)
    return {
        "business_no": (business_no or manifest.get("business_no")
                        or manifest.get("hint_business_no") or Path(input_dir).name),
        "selected_codes": sorted(materials),
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "disclaimer": DISCLAIMER,
        "materials": material_summary(materials, material_types),
        # 材料类型 → 名称（含本轮**没有**提供的类型，核对表要给缺失材料写名字）
        "material_names": {code: item.get("name") or code for code, item in entries.items()},
        # 材料代号 → 名称 / 材料类型。结论里的 `sources[].material` 是**代号**
        # （可能是 D1、也可能是文件名），只有类型名不对，表格列头会印成「D1」。
        "material_labels": {code: material_name(mat.get("material_type"), material_types)
                            for code, mat in sorted(materials.items())},
        "material_types": {code: mat.get("material_type")
                           for code, mat in sorted(materials.items())},
        # 本轮缺了哪些必备材料 —— 核对表第一节「本轮材料完整性」的数据源
        "missing_required": missing_required_materials(materials, material_types),
        # 每份材料的盖章 / 签字三态
        "seal_summary": seal_overview(materials),
        # 每份材料已抽到的字段名。覆盖率自检要靠它区分「材料没提供」与
        # 「材料提供了、但里面没有这个栏目（抽取的字段名没跟规则表对齐）」——
        # 这两种缺口要人做的事完全不同：前者补件，后者重跑 extract。
        "material_field_keys": {code: [item.get("key") for item in (mat.get("fields") or [])
                                      if item.get("key")]
                                for code, mat in sorted(materials.items())},
        "converged_out": dropped,
        "material_issues": material_issues,
        "findings": findings,
    }


def material_summary(materials, material_types):
    entries = material_entries(material_types)
    out = []
    for code, mat in sorted(materials.items()):
        mtype = mat.get("material_type")
        entry = entries.get(mtype) or {}
        out.append({
            "material_code": code,
            "material_type": mtype,
            "name": material_name(mtype, material_types),
            "field_count": len(mat.get("fields") or []),
            "unreadable_regions": mat.get("unreadable_regions") or [],
            # 以下四项来自材料登记表，核对表「比对材料清单」的用途说明要用
            "side": entry.get("side") or ("system" if str(mtype or "").startswith("M-SYS-") else "customer"),
            "carrier": entry.get("carrier"),
            "required": entry.get("required"),
            "compare_only": entry.get("compare_only"),
            "source_file": mat.get("source_file") or mat.get("file"),
        })
    return out


def summarise(payload):
    findings = payload["findings"]
    counts = {level: 0 for level in LEVELS}
    for finding in findings:
        counts[finding["level"]] = counts.get(finding["level"], 0) + 1
    required = [f for f in findings if f["required"] and f["level"] != "L5"]
    consistent = [f for f in required if f["level"] == "L4"]
    return {
        "total_fields": len(findings),
        "by_level": counts,
        "required_fields": len(required),
        "consistent_required": len(consistent),
        "coverage": round(len(consistent) / len(required), 4) if required else None,
        "coverage_note": "一致率 = 一致字段数 / 必核字段数，系统独有（L5）不计入分母",
    }


def merge_findings(old, new):
    """把新算出的 findings 并入旧结果（按 key 覆盖），便于分批比、局部重跑。"""
    by_key = {f["key"]: f for f in old}
    for f in new:
        by_key[f["key"]] = f
    merged = list(by_key.values())
    order = {f["key"]: i for i, f in enumerate(load_rules()[0])}
    merged.sort(key=lambda f: order.get(f["key"], 10 ** 6))
    return merged


# ==================================================================== verify

def verify_findings(input_dir, payload, key=None, codes=None):
    """回查证据：结论引用的 evidence_quote 是否真的能在来源材料原文里命中。

    两种命中口径都算命中，`hit_scope` 记下是哪一种：
    - `line`：引文完整落在某一行里（最常见）；
    - `text`：引文跨了相邻两行。版面换行会把一个字段值切成两行（身份证住址
      「住址 福建省福州市鼓楼区 / 测试路2号」就是典型），只按单行比对会把
      真命中误判成幻觉 —— 这是拿真样例跑出来的，不是设想。
    """
    # 回查必须看到全部材料：只装 --codes 那几份会把别的材料上的真命中误判成幻觉。
    # 所以 codes 在这里的语义是「只回查引用到这些材料的结论」，不是「只装载这些材料」。
    materials = load_materials(input_dir)
    findings = payload.get("findings") or []
    if codes:
        wanted = {str(code).strip().upper() for code in codes if str(code).strip()}
        findings = [f for f in findings if any(
            str(src.get("material") or "").upper() in wanted
            for src in f.get("sources") or [])]
    if key:
        findings = [f for f in findings if f["key"] == key or f.get("name") == key]
        if not findings:
            raise SystemExit("findings 里没有字段 %s" % key)
    results, miss = [], 0
    for finding in findings:
        for src in finding.get("sources") or []:
            quote = src.get("evidence_quote")
            mat = materials.get(src.get("material")) or {}
            lines = [nz.clean(x) for x in (mat.get("text_lines") or [])]
            hit, scope = False, None
            if quote:
                nq = nz.n_text(quote)
                if nq:
                    if any(nq in nz.n_text(l) for l in lines):
                        hit, scope = True, "line"
                    elif nq in nz.n_text("\n".join(lines)):
                        hit, scope = True, "text"
            if not hit:
                miss += 1
            results.append({
                "field": finding["key"],
                "material": src.get("material"),
                "value": src.get("value"),
                "evidence_quote": quote,
                "hit": hit,
                "hit_scope": scope,
                "position": src.get("position"),
            })
    cross_line = sum(1 for r in results if r.get("hit_scope") == "text")
    return {"checked": len(results), "miss": miss, "cross_line": cross_line,
            "results": results}


# ==================================================================== coverage

def coverage_check(payload, fields=None, material_types=None):
    """覆盖率自检：必核字段是否都被核到。

    两种「未核」都算不过闸门：
    - 本轮压根没比这个字段（不在已比范围内）；
    - 比了，但没有任何来源材料提供它（材料缺失 / 材料里没有该栏）。
    「有来源、但来源都读不出值」不算未核 —— 那是已核出的结论（J-06 待确认），
    单独计数提醒人工看图，不阻塞出表。

    每条未核项额外带 `missing_materials`（规则表里声明了、但本轮没提供的材料类型），
    核对表据此把「44 行无来源」归并成「因缺哪几份材料而无法比对」几行 ——
    平铺几十行未核字段，人看不出问题出在哪。
    """
    if fields is None:
        fields, material_types, _rules, _syn = load_rules()
    present = {m.get("material_type") for m in (payload.get("materials") or [])
               if m.get("material_type")}
    names = dict(payload.get("material_names") or {})
    if material_types:
        for item in material_types.get("customer_side", []) + material_types.get("system_side", []):
            names.setdefault(item["code"], item.get("name") or item["code"])
    findings = {f["key"]: f for f in payload.get("findings") or []}
    required = [f for f in fields if f.get("required") and f.get("key")]
    unchecked, no_value, checked = [], [], []
    # 材料类型 → 本轮实际提供的材料代号；材料代号 → 已抽到的字段名
    codes_by_type = {}
    for material in payload.get("materials") or []:
        if material.get("material_type"):
            codes_by_type.setdefault(material["material_type"], []).append(material.get("material_code"))
    keys_by_code = payload.get("material_field_keys") or {}

    def _absent_materials(field):
        """规则表声明了、但本轮材料里没有的材料类型（去重且稳定排序）。"""
        declared = [str((src or {}).get("material") or "") for src in (field.get("sources") or [])]
        return sorted({m for m in declared if m and m not in present})

    def _unmatched_materials(field):
        """材料本轮提供了，但里面没有这个栏目（抽取的字段名没跟规则表对齐）。

        与「材料没提供」是两种完全不同的缺口：前者要补件，后者要重跑 extract
        或对齐字段名。混在一起报，人不知道该做什么。
        """
        out = set()
        for src in field.get("sources") or []:
            mtype = str((src or {}).get("material") or "")
            path = (src or {}).get("path")
            if not mtype or mtype not in present:
                continue
            codes = codes_by_type.get(mtype) or []
            if codes and not any(path in (keys_by_code.get(code) or []) for code in codes):
                out.add(mtype)
        return sorted(out)

    for field in required:
        finding = findings.get(field["key"])
        group = field.get("system_group")
        if finding is None:
            unchecked.append({"key": field["key"], "name": field.get("name"),
                              "system_group": group,
                              "reason": "本轮未比对（不在已比字段范围内）",
                              "missing_materials": _absent_materials(field),
                              "unmatched_materials": _unmatched_materials(field)})
            continue
        if not finding.get("sources"):
            absent = _absent_materials(field)
            unmatched = _unmatched_materials(field)
            reason = "无任何来源材料提供该字段"
            if not absent and unmatched:
                reason = "来源材料已提供，但材料里没有该栏目（抽取的字段名与规则表未对齐）"
            unchecked.append({"key": field["key"], "name": field.get("name"),
                              "system_group": group,
                              "reason": reason,
                              "missing_materials": absent,
                              "unmatched_materials": unmatched})
            continue
        if all(s.get("empty_state") != "value" for s in finding["sources"]):
            no_value.append({"key": field["key"], "name": field.get("name"),
                             "system_group": group,
                             "reason": "来源均未读出取值（材料空白或未识别）"})
            checked.append(field["key"])
            continue
        checked.append(field["key"])
    for item in unchecked:
        absent = item.get("missing_materials") or []
        item["missing_names"] = [names.get(code, code) for code in absent]
    total = len(required)

    # ---- 系统侧（基准方）取值覆盖 ----------------------------------------
    # 系统录入侧是比对的**基准方**：它全空时 run_compare 取不到基准取值，
    # 只剩客户侧材料互相比，单个来源会被判成「归一化后一致」（假一致）。
    # 真机上 5 张系统截图 fields 全空，覆盖率自检照样 pass —— 这是静默退化的源头，
    # 必须单列成一个硬闸门。
    sys_required = [f for f in required if f.get("system_group")]
    sys_types = sorted({f["system_group"] for f in sys_required})
    sys_present = [t for t in sys_types if t in present]
    keys_by_systype = {}
    for material in payload.get("materials") or []:
        mtype = material.get("material_type")
        if mtype:
            keys_by_systype.setdefault(mtype, set()).update(
                keys_by_code.get(material.get("material_code")) or [])
    empty_types = [t for t in sys_present if not keys_by_systype.get(t)]
    missing_sys_values = []
    for field in sys_required:
        mtype = field["system_group"]
        if mtype not in present:
            continue
        suffix = field["key"].split(".", 1)[1] if "." in field["key"] else field["key"]
        if suffix not in (keys_by_systype.get(mtype) or set()):
            missing_sys_values.append({"key": field["key"], "name": field.get("name"),
                                       "system_group": mtype})
    system_side = {
        "types_declared": sys_types,
        "types_present": sys_present,
        "types_missing": [t for t in sys_types if t not in present],
        "empty_types": empty_types,
        "empty_type_names": [names.get(t, t) for t in empty_types],
        "fields_without_system_value": missing_sys_values,
        "fields_without_system_value_count": len(missing_sys_values),
        "pass": not empty_types,
    }

    return {
        "required_total": total,
        "checked": len(checked),
        "coverage": round(len(checked) / total, 4) if total else None,
        "unchecked": unchecked,
        "no_value": no_value,
        # 系统侧全空 → 比对已退化，闸门不通过（补齐后可重跑）
        "pass": (not unchecked) and system_side["pass"],
        "system_side": system_side,
        # 按缺口归并：{缺失材料类型: {name, fields: [...]}}，只含真的由该缺口造成的未核
        "by_missing_material": _group_unchecked_by_gap(unchecked, names),
    }


def _group_unchecked_by_gap(unchecked, names):
    """把未核字段按**主因**归并：缺哪份材料 > 字段名未对齐 > 其他。

    主因取「缺的材料」这一档：一条未核同时有「缺 M-CUST-05」和「M-CUST-01 里没
    这个栏目」时，根因是缺件（补了件通常就核上了）。按完整组合归并会炸出十几行
    差别细微的分组（真机实测 10 行、每行 1~4 个字段），反而看不出问题在哪。
    字段名未对齐的明细由报告里单独一张表给出。
    """
    groups = {}
    for item in unchecked:
        absent = tuple(item.get("missing_materials") or [])
        unmatched = tuple(item.get("unmatched_materials") or [])
        if absent:
            key = absent
        elif unmatched:
            key = ("__unmatched__",)
        else:
            key = ("__other__",)
        bucket = groups.setdefault(key, {"materials": [], "unmatched": [],
                                        "names": [], "unmatched_names": [], "fields": []})
        for code in absent:
            if code not in bucket["materials"]:
                bucket["materials"].append(code)
        for code in unmatched:
            if code not in bucket["unmatched"]:
                bucket["unmatched"].append(code)
        bucket["fields"].append({"key": item.get("key"), "name": item.get("name")})
    out = []
    order = {"__unmatched__": 1, "__other__": 2}
    for key in sorted(groups, key=lambda k: (order.get(k[0], 0), tuple(map(str, k)))):
        bucket = groups[key]
        bucket["names"] = [names.get(code, code) for code in bucket["materials"]]
        bucket["unmatched_names"] = [names.get(code, code) for code in bucket["unmatched"]]
        bucket["count"] = len(bucket["fields"])
        out.append(bucket)
    return out


# ==================================================================== CLI

def _parse_csv(value):
    return [x.strip() for x in (value or "").split(",") if x.strip()]


_LAST_PAYLOAD = {}


def _remember(obj):
    if isinstance(obj, dict):
        _LAST_PAYLOAD.clear()
        _LAST_PAYLOAD.update(obj)


def _emit(obj, out=None):
    _remember(obj)
    text = json.dumps(obj, ensure_ascii=False, indent=2)
    if out:
        Path(out).write_text(text, encoding="utf-8")
        print("已写出 %s" % out)
    else:
        print(text)


def _summarize(command, args):
    """把子命令结果压成一条轨迹标题+细节。"""
    data = _LAST_PAYLOAD
    if command == "inspect":
        missing = len(data.get("missing_required") or [])
        groups = len(data.get("suggested_groups") or [])
        return ("清点材料：%d 份" % (data.get("material_count") or 0),
                "必备材料%s；疑似同笔业务 %d 组（仅提示）；声明但缺失 %d 份"
                % ("齐备" if not missing else "缺 %d 份" % missing, groups,
                   len(data.get("declared_but_absent") or [])))
    if command == "rules":
        return ("查规则：%s" % (data.get("kind") or getattr(args, "kind", "")),
                "返回 %d 条" % (data.get("returned") or data.get("total") or 0))
    if command == "extract":
        return ("抽取 %s：%d/%d 个字段" % (data.get("material"),
                                          data.get("extracted") or 0,
                                          data.get("requested") or 0),
                "未定位 %d 个" % len(data.get("missing") or []))
    if command == "compare":
        summary = data.get("summary") or {}
        levels = summary.get("by_level") or {}
        return ("比对 %d 个字段" % (summary.get("total_fields") or 0),
                "一致 %d / 不一致 %d / 待确认 %d；材料级问题 %d 条"
                % (levels.get("L4", 0), levels.get("L2", 0), levels.get("L3", 0),
                   len(data.get("material_issues") or [])))
    if command == "verify":
        return ("证据回查 %d 条" % (data.get("checked") or 0),
                "未命中 %d 条（跨行命中 %d 条）" % (data.get("miss") or 0,
                                                    data.get("cross_line") or 0))
    if command == "report":
        coverage = data.get("coverage") or {}
        return ("渲染核对表", "覆盖率 %s；未核 %d 项"
                % (coverage.get("coverage"), len(coverage.get("unchecked") or [])))
    if command == "audit":
        summary = data.get("summary") or {}
        levels = summary.get("by_level") or {}
        coverage = data.get("coverage") or {}
        verify = data.get("verify") or {}
        typed = len([g for g in (data.get("type_guesses") or []) if g.get("material_type")])
        return ("一站式核对：%d 个字段" % (summary.get("total_fields") or 0),
                "一致 %d / 不一致 %d / 待确认 %d；覆盖率 %s；证据未命中 %d；材料类型 %d 份；%s"
                % (levels.get("L4", 0), levels.get("L2", 0), levels.get("L3", 0),
                   coverage.get("coverage"), verify.get("miss") or 0, typed,
                   "已出表" if data.get("report_written") else "被覆盖率闸门拦住"))
    return None, ""


def _autoreport(command, args):
    """子命令跑完自动上报一条执行轨迹。

    为什么做进 CLI 而不是让模型自己调 `report_channel.py`：真机实测模型直接跑 compare
    出了表、一条轨迹都没上报（dsh 只外露最终答复，容器内动作平台看不见）。平台注入了
    上报地址（`DWP_EXECUTION_EVENT_URL`）时每条子命令自动留痕；没注入（本机自测、评测层）
    时静默跳过，行为完全不变。
    """
    if not os.environ.get("DWP_EXECUTION_EVENT_URL"):
        return
    title, detail = _summarize(command, args)
    if not title:
        return
    try:
        import report_channel as rc

        rc.report(title, detail, status="completed")
    except Exception as exc:  # noqa: BLE001 - 轨迹是观测，不阻断主流程
        sys.stderr.write("[review] 轨迹上报失败（不影响结果）：%s\n" % exc)


def cmd_inspect(args):
    result = inspect_materials(args.materials, codes=_parse_csv(getattr(args, "codes", None)))
    _emit(result, args.out)
    return 0


def cmd_rules(args):
    result = query_rules(args.kind, key=args.key, group=args.group, rule=args.rule,
                         material=args.material, name=args.name)
    _emit(result, args.out)
    return 0


def cmd_extract(args):
    label_map = json.loads(args.labels) if args.labels else {}
    result = extract_for_material(args.materials, args.material,
                                  only_paths=_parse_csv(args.fields),
                                  label_map=label_map, write=args.write)
    _emit(result, args.out)
    return 0


def cmd_extract_all(args):
    """材料包级批量抽值：平台在上传物化后调用它，做到「材料就绪即可比对」。

    与 `audit` 内置的抽值前置同一实现、同一口径（只补缺失字段，不覆盖已有值）。
    """
    report = extract_materials(args.materials, codes=_parse_csv(args.codes),
                               only_missing=not args.force, write=True)
    total_req = sum(item["requested"] for item in report.values())
    total_got = sum(item["extracted"] for item in report.values())
    result = {
        "materials": len(report),
        "requested": total_req,
        "extracted": total_got,
        "yield": round(total_got / total_req, 3) if total_req else None,
        "system_side": {code: item for code, item in sorted(report.items())
                        if item["side"] == "system"},
        "warnings": {code: item["warning"] for code, item in sorted(report.items())
                     if item.get("warning")},
        "by_material": report,
    }
    _emit(result, args.out)
    return 0


def cmd_compare(args):
    # 抽值前置：与 audit 同一口径。只补材料自身缺失的字段（不覆盖已有值），
    # 保证系统侧（基准方）有取值可比 —— 否则单个来源会被判成「归一化后一致」。
    if not getattr(args, "no_extract", False):
        extract_materials(args.materials, codes=_parse_csv(getattr(args, "codes", None)),
                          only_missing=True, write=True)
    payload = run_compare(args.materials, keys=_parse_csv(args.fields),
                          groups=_parse_csv(args.groups), business_no=args.business_no,
                          codes=_parse_csv(getattr(args, "codes", None)))
    out_path = Path(args.out)
    if args.append and out_path.exists():
        with open(out_path, "r", encoding="utf-8") as fh:
            old = json.load(fh)
        payload["findings"] = merge_findings(old.get("findings") or [], payload["findings"])
    payload["summary"] = summarise(payload)
    payload["coverage"] = coverage_check(payload)
    payload["disclaimer"] = DISCLAIMER
    _remember(payload)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    s = payload["summary"]
    print(json.dumps({
        "compared": len(payload["findings"]),
        "by_level": s["by_level"],
        "required_fields": s["required_fields"],
        "consistent_required": s["consistent_required"],
        "consistency_rate": s["coverage"],
        "coverage": payload["coverage"]["coverage"],
        "unchecked": len(payload["coverage"]["unchecked"]),
        "material_issues": len(payload["material_issues"]),
        "out": str(out_path),
    }, ensure_ascii=False, indent=2))
    return 0


def cmd_verify(args):
    with open(args.findings, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    result = verify_findings(args.materials, payload, key=args.key,
                             codes=_parse_csv(getattr(args, "codes", None)))
    _emit(result, args.out)
    return 1 if result["miss"] else 0


def _resolve_html_path(args, report_path):
    """HTML 输出去向：**默认就出**，与核对表同名的 `.html`。

    - 默认（既没给 `--html` 也没给 `--no-html`）→ `<out>` 同名 `.html`；
    - `--html <路径>` 指定路径；`--html` 不带值同样取同名；
    - `--no-html` 显式关掉（只在明确不要 HTML 时才用）。

    为什么默认出：平台会把工作区里的 `.html` 一并快照成可预览产物，
    业务同事要的是能直接打开、能打印的成品；只给 `.md` 等于把「怎么看」
    这件事丢回给用户。两份同源（同一份 blocks），不存在口径漂移。
    """
    if getattr(args, "no_html", False):
        return None
    value = getattr(args, "html", None)
    if value:
        return str(value)
    return str(Path(report_path).with_suffix(".html"))


def cmd_report(args):
    with open(args.findings, "r", encoding="utf-8") as fh:
        payload = json.load(fh)
    payload.setdefault("summary", summarise(payload))
    coverage = coverage_check(payload)
    payload["coverage"] = coverage
    _remember(payload)
    if not coverage["pass"] and not args.force:
        # 出表前的强制闸门：未核字段必须显式列出，否则不得出表
        print(json.dumps({
            "blocked": True,
            "reason": "覆盖率自检未通过：必核字段存在未核项，先补齐或显式列出未核与原因",
            "coverage": coverage["coverage"],
            "required_total": coverage["required_total"],
            "checked": coverage["checked"],
            "unchecked": coverage["unchecked"],
            "hint": "补齐后可重跑；确实无法核的，用 --force 出表并在核对表里保留「未核」章节。",
        }, ensure_ascii=False, indent=2))
        return 2
    text = rp.build(payload)
    Path(args.out).write_text(text, encoding="utf-8")
    written = [str(args.out)]
    html_path = _resolve_html_path(args, Path(args.out))
    if html_path is not None:
        rp.write_html(payload, html_path)
        written.append(html_path)
    print("已写出 %s（%d 字符，覆盖率 %s，未核 %d 项）"
          % ("、".join(written), len(text), coverage["coverage"], len(coverage["unchecked"])))
    return 0


def _report_skill_step(result):
    """把一站式核对的结果上报进执行轨迹（``skill_step``）。

    轨迹是观测、不是业务前置条件：容器里没注入上报环境变量（离线自测）时静默跳过。
    由脚本自己上报，而不是要求模型再手动调一次 `report_channel.py` ——
    模型经常漏掉这一步，而它本身是完全确定性的。
    """
    try:
        import report_channel as rc
    except Exception:  # noqa: BLE001 - 上报通道缺失不影响主流程
        return
    summary = result.get("summary") or {}
    levels = summary.get("by_level") or {}
    coverage = result.get("coverage") or {}
    typed = len([g for g in (result.get("type_guesses") or []) if g.get("material_type")])
    rc.report(
        "一站式核对完成：%d 个字段" % (summary.get("total_fields") or 0),
        "一致 %d / 不一致 %d / 待确认 %d；覆盖率 %s；证据未命中 %d；材料类型 %d 份；%s"
        % (levels.get("L4", 0), levels.get("L2", 0), levels.get("L3", 0),
           coverage.get("coverage"), (result.get("verify") or {}).get("miss") or 0, typed,
           ("已出表（Markdown + HTML）" if result.get("report_html")
            else ("已出表" if result.get("report_written") else "被覆盖率闸门拦住"))),
        status="completed",
    )


def cmd_audit(args):
    """一站式：补材料类型 → 抽值 → inspect → compare → verify → report。

    只是把现有函数按固定顺序串起来，**不新增判定逻辑、不放宽覆盖率**：
    比对、证据回查、覆盖率闸门全部复用分步命令用的同一套实现，产出的
    findings.json 与核对表（`.md` 与 `.html` 两份，同源渲染）与分步跑出来的等价。
    需要细看某一步时，再用 inspect / rules / extract / compare / verify / report 单独跑。

    为什么提供这条捷径：每多一次子命令就是一次 bash 往返 + 一次模型调用，
    真机上一轮 23 步里有相当一部分花在「按顺序调六个命令」上。
    """
    materials = args.materials
    codes = _parse_csv(getattr(args, "codes", None))
    out_dir = Path(args.out_dir or ".")
    findings_path = Path(args.findings) if args.findings else out_dir / "findings.json"
    report_path = Path(args.out) if args.out else out_dir / "核对表.md"

    stage_timing = {}
    t_stage = time.time()

    # 0) 材料类型兜底：只在材料自身没有 material_type 时按文件名/文本猜一个并写回，
    #    省掉「逐份读材料 + 逐个写回类型」那一整段往返（真机实测约 13 次调用）。
    type_guesses = apply_type_guesses(materials, codes=codes)

    # 0.5) 抽值前置：**系统录入侧截图同样是核对对象**，必须先抽出基准方取值，
    #      否则 run_compare 取不到系统侧值，比对退化成客户材料互相印证（假一致）。
    #      只补缺失字段，不覆盖已有值；纯本地确定性定位，毫秒级。
    extraction = None
    if not getattr(args, "no_extract", False):
        extraction = extract_materials(materials, codes=codes, only_missing=True, write=True)
    stage_timing["type_guess_and_extract"] = round(time.time() - t_stage, 3)
    t_stage = time.time()

    # 1) 清点：必备材料齐备性 + 疑似同笔业务提示（只提示不决策）
    info = inspect_materials(materials, codes=codes)
    stage_timing["inspect"] = round(time.time() - t_stage, 3)
    t_stage = time.time()

    # 2) 比对（判定由脚本给出）
    payload = run_compare(materials, keys=_parse_csv(args.fields),
                          groups=_parse_csv(args.groups),
                          business_no=args.business_no, codes=codes)
    stage_timing["compare"] = round(time.time() - t_stage, 3)
    t_stage = time.time()
    payload["summary"] = summarise(payload)
    payload["coverage"] = coverage_check(payload)
    payload["disclaimer"] = DISCLAIMER
    findings_path.parent.mkdir(parents=True, exist_ok=True)
    findings_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                             encoding="utf-8")

    # 3) 证据回查（防幻觉）；必须看到全部材料，--codes 只筛结论不筛材料
    verify = verify_findings(materials, payload, codes=codes)
    verify_summary = {
        "checked": verify["checked"],
        "miss": verify["miss"],
        "cross_line": verify["cross_line"],
        "missed_fields": sorted({r["field"] for r in verify["results"] if not r["hit"]}),
    }
    stage_timing["verify"] = round(time.time() - t_stage, 3)

    # 4) 出表：覆盖率闸门与 report 子命令完全同一口径
    coverage = dict(payload["coverage"])
    # 只回前 20 条未核，避免把 stdout 撑大 —— 完整清单在 findings.json 里。
    coverage["unchecked"] = coverage["unchecked"][:20]
    coverage["no_value"] = coverage["no_value"][:20]
    sys_side = coverage.get("system_side") or {}
    # 先定「这一轮到底出不出表」：被覆盖率闸门拦住时根本没有产物文件，
    # report_html 就不能回报一个不存在的路径（模型会照着去读）。
    will_write = bool(payload["coverage"]["pass"] or args.force)
    html_target = _resolve_html_path(args, report_path) if will_write else None
    if html_target:
        html_note = (
            "HTML 版（%s）默认一并产出，同样属于本轮交付物，不要删、不要另存到别处；"
            "答复里可以告诉用户「核对表已生成 Markdown 与 HTML 两版，可直接打开或打印」。"
            % html_target)
    elif will_write:
        html_note = "本轮用 --no-html 关掉了 HTML 版，只交付 Markdown。"
    else:
        html_note = "本轮被覆盖率闸门拦住、尚未出表，补齐缺口后重跑才会产出核对表与 HTML。"
    result = {
        "material_count": info["material_count"],
        "selected_codes": info["selected_codes"],
        "missing_required": info["missing_required"],
        "suggested_groups": info["suggested_groups"],
        "type_guesses": type_guesses,
        "type_guess_note": ("材料类型由脚本按文件名/文本猜出并写回；"
                            "哪份判错了直接改它的 material_type 再重跑即可。"),
        "extraction": extraction,
        "system_side_coverage": sys_side,
        "stage_timing": stage_timing,
        "summary": payload["summary"],
        "coverage": coverage,
        "verify": verify_summary,
        "findings": str(findings_path),
        "report": str(report_path),
        "report_html": html_target,
        "report_written": False,
        "disclaimer": DISCLAIMER,
        "next_hint": ("结论已全部落在核对表里：读 %s 写答复即可。%s"
                      "不要逐份读材料 JSON，也不要 cat findings.json（几十 KB，只吃上下文）。"
                      "结论摘要、不一致明细与待人工确认项必须写进答复正文 —— "
                      "用户只看得到答复，看不到工作区文件。"
                      "**不要自己写比对脚本或自己另写一份报告**：判定、宽表与结论段都已由"
                      "核对表给出，自写一份只会与它不一致、并让这一轮多花几分钟。"
                      % (report_path, html_note)),
    }
    if will_write:
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rp.build(payload), encoding="utf-8")
        result["report_written"] = True
        if html_target is not None:
            Path(html_target).parent.mkdir(parents=True, exist_ok=True)
            rp.write_html(payload, html_target)
            result["report_html"] = html_target
    else:
        result["blocked"] = True
        if sys_side.get("empty_types"):
            result["block_reason"] = (
                "覆盖率自检未通过：系统录入侧（基准方）以下材料一份字段都没抽出来 -> %s。"
                "比对会退化成客户材料互相印证，必须先把基准方取值补齐。"
                "（或 --force 出表并保留「未核」章节）" % "、".join(sys_side["empty_type_names"]))
        else:
            result["block_reason"] = ("覆盖率自检未通过：必核字段存在未核项，先补齐"
                                     "（或 --force 出表并保留「未核」章节）")
    _remember(result)
    _report_skill_step(result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["report_written"] else 2


def build_parser():
    ap = argparse.ArgumentParser(description="机构开户材料交叉核对原子工具集")
    sub = ap.add_subparsers(dest="command", required=True)

    p = sub.add_parser("inspect", help="清点材料 + 每份摘要")
    p.add_argument("--materials", required=True, help="材料包目录")
    p.add_argument("--codes", help="只核这些材料代号（逗号分隔，逻辑分笔，如 D1,D2,S1）")
    p.add_argument("--out")
    p.set_defaults(func=cmd_inspect)

    p = sub.add_parser("rules", help="按需查规则")
    p.add_argument("--kind", default="index",
                   choices=["index", "fields", "materials", "rules", "synonyms"])
    p.add_argument("--key", help="字段 key 或名称")
    p.add_argument("--group", help="系统侧分组，如 M-SYS-01")
    p.add_argument("--rule", help="归一化或判定编号，如 N-01 / J-07")
    p.add_argument("--material", help="材料类型，如 M-CUST-01")
    p.add_argument("--name", help="字段名模糊匹配")
    p.add_argument("--out")
    p.set_defaults(func=cmd_rules)

    p = sub.add_parser("extract", help="确定性定位抽值")
    p.add_argument("--materials", required=True)
    p.add_argument("--material", required=True, help="材料代号，如 D1")
    p.add_argument("--fields", help="只抽这些字段（逗号分隔，材料内字段名）")
    p.add_argument("--labels", help="标签映射 JSON，如 '{\"name\":[\"名称\",\"单位名称\"]}'")
    p.add_argument("--write", action="store_true", help="把结果写回材料文件")
    p.add_argument("--out")
    p.set_defaults(func=cmd_extract)

    p = sub.add_parser("extract-all", help="对材料包内所有材料批量抽值（平台物化后自动调用）")
    p.add_argument("--materials", required=True)
    p.add_argument("--codes", help="只处理这些材料代号（逗号分隔）")
    p.add_argument("--force", action="store_true", help="已抽过的字段也重抽（默认只补缺失）")
    p.add_argument("--out")
    p.set_defaults(func=cmd_extract_all)

    p = sub.add_parser("compare", help="比指定字段或字段组")
    p.add_argument("--materials", required=True)
    p.add_argument("--fields", help="只比这些字段（逗号分隔，字段 key 或名称）")
    p.add_argument("--groups", help="只比这些系统侧分组（逗号分隔）")
    p.add_argument("--out", default="findings.json")
    p.add_argument("--append", action="store_true", help="并入已有 findings（局部重跑）")
    p.add_argument("--business-no")
    p.add_argument("--codes", help="只核这些材料代号（逗号分隔，逻辑分笔）")
    p.add_argument("--no-extract", action="store_true",
                   help="跳过内置抽值前置（默认会补抽各材料缺失字段，含系统侧）")
    p.set_defaults(func=cmd_compare)

    p = sub.add_parser("verify", help="回查结论证据是否命中原文")
    p.add_argument("--materials", required=True)
    p.add_argument("--findings", default="findings.json")
    p.add_argument("--key", help="只查某一条结论")
    p.add_argument("--codes", help="只回查引用到这些材料的结论（逗号分隔）")
    p.add_argument("--out")
    p.set_defaults(func=cmd_verify)

    p = sub.add_parser("report", help="渲染核对表（含覆盖率闸门）")
    p.add_argument("--findings", default="findings.json")
    p.add_argument("--out", default="核对表.md")
    p.add_argument("--force", action="store_true", help="未核项存在时仍出表（会保留未核章节）")
    p.add_argument("--html", nargs="?", const="",
                   help="HTML 输出路径（默认就出，取 <out> 同名 .html）")
    p.add_argument("--no-html", action="store_true", help="不要 HTML 版（只出 Markdown）")
    p.set_defaults(func=cmd_report)

    p = sub.add_parser("audit", help="一站式：抽值→清点→比对→回查→出表（推荐首选）")
    p.add_argument("--materials", required=True, help="材料包目录")
    p.add_argument("--codes", help="只核这些材料代号（逗号分隔，逻辑分笔，如 D1,D2,S1）")
    p.add_argument("--fields", help="只比这些字段（逗号分隔，字段 key 或名称）")
    p.add_argument("--groups", help="只比这些系统侧分组（逗号分隔）")
    p.add_argument("--business-no")
    p.add_argument("--out-dir", help="产物目录（默认当前目录）")
    p.add_argument("--findings", help="findings 输出路径（默认 <out-dir>/findings.json）")
    p.add_argument("--out", help="核对表输出路径（默认 <out-dir>/核对表.md）")
    p.add_argument("--no-extract", action="store_true",
                   help="跳过内置抽值前置（材料已由上游抽好时用；默认会补抽系统侧与客户侧缺失字段）")
    p.add_argument("--force", action="store_true", help="未核项存在时仍出表（会保留未核章节）")
    p.add_argument("--html", nargs="?", const="",
                   help="HTML 输出路径（默认就出，取 <out> 同名 .html）")
    p.add_argument("--no-html", action="store_true", help="不要 HTML 版（只出 Markdown）")
    p.set_defaults(func=cmd_audit)
    return ap


def main(argv=None):
    args = build_parser().parse_args(argv)
    code = args.func(args)
    _autoreport(args.command, args)
    return code


# 供 compare.py（参考路径）复用，避免两处各写一份
emit = _emit
parse_csv = _parse_csv


if __name__ == "__main__":
    raise SystemExit(main())
