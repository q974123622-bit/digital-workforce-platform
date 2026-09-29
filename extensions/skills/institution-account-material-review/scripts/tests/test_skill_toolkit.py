#!/usr/bin/env python3
"""技能包原子工具箱的离线自测：不联网、不调模型、纯 stdlib。

覆盖：
1. inspect 清点（含必备材料缺失、声明但缺失）；
2. rules 五类查询（index / fields / materials / rules / synonyms）；
3. extract 确定性定位（同单元格、右邻单元格、找不到）；
4. compare 逐字段判定与 --append 局部重跑；
5. verify 证据回查（命中 / 未命中）；
6. report 覆盖率闸门（未核字段存在时必须拦住，--force 才出表且保留未核章节）；
7. report_channel 敏感信息打码、无地址/连不上时不抛异常；
8. 参考路径 compare.py 作为独立进程跑通。

材料包解包的用例在平台侧 `backend/tests/test_material_pack.py`（解包发生在上传侧，不在容器里）。

用法：
  python3 scripts/tests/test_skill_toolkit.py
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import report_channel  # noqa: E402
import review as rv  # noqa: E402

PASSED, FAILED = [], []


def check(name, cond, detail=""):
    (PASSED if cond else FAILED).append(name)
    print("%s %s%s" % ("PASS" if cond else "FAIL", name,
                       ("  ← " + str(detail)) if (not cond and detail != "") else ""))


def build_case(root):
    """造一笔最小业务：系统录入侧 1 份 + 客户侧 2 份，带转写文本。"""
    case = Path(root) / "TEST-001"
    case.mkdir(parents=True, exist_ok=True)
    (case / "materials.json").write_text(json.dumps({
        "business_no": "TEST-001",
        "materials": [
            {"code": "S1", "material_type": "M-SYS-01", "source_file": "开户录入.png"},
            {"code": "D7", "material_type": "M-CUST-07", "source_file": "证件.png"},
            {"code": "D3", "material_type": "M-CUST-03", "source_file": "身份证.png"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "S1.json").write_text(json.dumps({
        "material_code": "S1", "material_type": "M-SYS-01", "kind": "image",
        "text_lines": ["证件类型 | 身份证", "证件编号 | 110101199001011234"],
        "fields": [{"key": "cert_type", "value": "身份证", "evidence_quote": "证件类型 | 身份证",
                    "readable": True, "confidence": 0.95, "position": "第 1 行"},
                   {"key": "cert_no", "value": "110101199001011234",
                    "evidence_quote": "证件编号 | 110101199001011234",
                    "readable": True, "confidence": 0.95, "position": "第 2 行"}],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "D7.json").write_text(json.dumps({
        "material_code": "D7", "material_type": "M-CUST-07", "kind": "image",
        "text_lines": ["证件类型 | 居民身份证", "证件编号 | 110101199001011234",
                       "单位名称：机构开户申请"],
        "fields": [{"key": "cert_type", "value": "居民身份证", "evidence_quote": "证件类型 | 居民身份证",
                    "readable": True, "confidence": 0.9, "position": "第 1 行"},
                   {"key": "cert_no", "value": "110101199001011234",
                    "evidence_quote": "证件编号 | 110101199001011234",
                    "readable": True, "confidence": 0.9, "position": "第 2 行"}],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "D3.json").write_text(json.dumps({
        "material_code": "D3", "material_type": "M-CUST-03", "kind": "image",
        "text_lines": ["证件编号 | 110101199001011999"],
        "fields": [{"key": "cert_no", "value": "110101199001011999",
                    "evidence_quote": "证件编号 | 110101199001011999",
                    "readable": True, "confidence": 0.9, "position": "第 1 行"}],
    }, ensure_ascii=False), encoding="utf-8")
    return case


def test_inspect(case):
    data = rv.inspect_materials(case)
    check("inspect：材料数正确", data["material_count"] == 3, data["material_count"])
    codes = {m["material_code"] for m in data["materials"]}
    check("inspect：代号齐全", codes == {"S1", "D7", "D3"}, codes)
    d7 = [m for m in data["materials"] if m["material_code"] == "D7"][0]
    check("inspect：带材料名称与转写行数",
          d7["name"] and d7["transcript_lines"] == 3, d7)
    check("inspect：指纹稳定（同内容同指纹）",
          d7["fingerprint"] == rv.inspect_materials(case)["materials"][1]["fingerprint"],
          d7["fingerprint"])
    check("inspect：必备材料缺失被列出", isinstance(data["missing_required"], list),
          data["missing_required"])


def test_rules():
    idx = rv.query_rules("index")
    check("rules：索引返回全部字段", idx["total"] == len(rv.load_rules()[0]), idx["total"])
    one = rv.query_rules("fields", key="sys01.cert_no")
    check("rules：按 key 查字段", one["returned"] == 1 and one["fields"][0]["key"] == "sys01.cert_no",
          one["returned"])
    grp = rv.query_rules("index", group="M-SYS-01")
    check("rules：按分组过滤", grp["total"] > 0 and all(
        r["group"] == "M-SYS-01" for r in grp["fields"]), grp["total"])
    by_rule = rv.query_rules("fields", rule="J-07")
    check("rules：按判定编号查字段", isinstance(by_rule["fields"], list), by_rule["returned"])
    mats = rv.query_rules("materials")
    check("rules：材料登记可取", mats["returned"] > 0, mats["returned"])
    syn = rv.query_rules("synonyms")
    check("rules：同义词表可取", "cert_type" in syn["synonyms"], list(syn["synonyms"])[:5])


def test_extract(case):
    res = rv.extract_for_material(case, "D7", only_paths=["cert_type", "cert_no"])
    by_key = {f["key"]: f for f in res["fields"]}
    check("extract：右邻单元格取值", by_key["cert_type"]["value"] == "居民身份证", res)
    check("extract：证据引文是整行原文",
          by_key["cert_type"]["evidence_quote"] == "证件类型 | 居民身份证", res)
    check("extract：定位方法被记录", by_key["cert_type"]["method"] == "next_cell",
          by_key["cert_type"]["method"])

    same = rv.extract_for_material(case, "D7", only_paths=["name"])
    check("extract：同单元格标签后取值",
          same["fields"] and same["fields"][0]["value"] == "机构开户申请"
          and same["fields"][0]["method"] == "same_cell", same["fields"])

    miss = rv.extract_for_material(case, "D7", only_paths=["nonexistent_field"])
    check("extract：定位不到时进 missing 且不猜值",
          not miss["fields"] and len(miss["missing"]) == 1, miss)

    written = rv.extract_for_material(case, "D7", only_paths=["cert_no"], write=True)
    check("extract：--write 写回材料文件", written["written"], written)
    again = rv.load_materials(case)["D7"]
    check("extract：写回后字段仍在",
          any(f["key"] == "cert_no" for f in again["fields"]), again["fields"])


def _mini_pack(tmp, name, materials):
    """造一个最小材料包：materials 是 [(代号, 材料类型, text_lines), ...]，fields 一律为空。"""
    root = Path(tmp) / name
    root.mkdir(parents=True, exist_ok=True)
    entries = []
    for code, mtype, lines in materials:
        (root / ("%s.json" % code)).write_text(json.dumps({
            "material_code": code, "material_type": mtype, "kind": "image",
            "text_lines": lines, "fields": [],
        }, ensure_ascii=False), encoding="utf-8")
        entries.append({"code": code, "material_type": mtype, "source_file": "%s.png" % code})
    (root / "materials.json").write_text(json.dumps(
        {"business_no": name, "materials": entries}, ensure_ascii=False), encoding="utf-8")
    return root


def test_extract_quality(tmp):
    """抽取质量：真样例里抽错过的那几类写法，必须稳定地不产出假值。"""
    pack = _mini_pack(tmp, "EXTRACT-001", [
        ("S1", "M-SYS-01", ["受益人名称 | 测试 | 证件类别 | 身份证",
                            "国籍/地区 | 中国",
                            "实际控制形式 | 实施实际控制的内容/方式"]),
        ("S3", "M-SYS-03", ["不良诚信记录 | 无"]),
    ])
    got = {f["key"]: f["value"] for f in rv.extract_for_material(pack, "S1")["fields"]}
    check("抽取：整格就是标签时取右邻格（国籍/地区 → 中国）",
          got.get("nationality") == "中国", got)
    check("抽取：标签后半截不当值（证件类别 → 身份证）", got.get("cert_type") == "身份证", got)
    check("抽取：右邻格是别的字段标签时不取值（实际控制形式）",
          "control_form" not in got, got)
    got3 = {f["key"]: f["value"] for f in rv.extract_for_material(pack, "S3")["fields"]}
    check("抽取：「无」是取值不是空值（不良诚信记录）",
          got3.get("integrity_record") == "无", got3)

    checked = _mini_pack(tmp, "EXTRACT-002", [
        ("D5", "M-CUST-05", ["资本属性 | ☑境内资本 □三资(合资、合作、外资) □境外资本",
                             "国有属性 | □国有股东(SS) □国有实际控制股东(CS) □暂未分类的国有股东 "
                             "□非国有或非国有有实际控制股东"]),
    ])
    got2 = {f["key"]: f["value"] for f in rv.extract_for_material(checked, "D5")["fields"]}
    check("抽取：勾选组只取 ☑ 那一项", got2.get("capital_nature") == "境内资本", got2)
    check("抽取：整串都是 □（未勾选）时不取值", "state_owned" not in got2, got2)

    split = _mini_pack(tmp, "EXTRACT-003", [
        ("S4", "M-SYS-04", ["联系人证件有效", "期 20260902 ~ 20460902"]),
    ])
    got3 = {f["key"]: f["value"] for f in rv.extract_for_material(split, "S4")["fields"]}
    check("抽取：标签被换行拆开时仍能取值",
          got3.get("contact_cert_validity") == "20260902 ~ 20460902", got3)


def test_system_side_is_baseline(tmp):
    """系统侧是基准方：只有它有值时判待确认，不能拿单个客户来源凑成「一致」。"""
    pack = _mini_pack(tmp, "EXTRACT-004", [("S2", "M-SYS-02", ["证件编号 | 91350100260200025B"])])
    report = rv.extract_materials(pack, write=True)
    check("抽取：系统侧材料同样参与抽值", report["S2"]["extracted"] >= 1, report["S2"])
    payload = rv.run_compare(pack, business_no="EXTRACT-004")
    by_key = {f["key"]: f for f in payload["findings"]}
    check("判定：只有系统侧有值 → 待确认（不判一致）",
          by_key["sys02.cert_no"]["level"] == "L3", by_key["sys02.cert_no"])
    check("覆盖率：系统侧有取值时闸门不因系统侧拦下",
          rv.coverage_check(payload)["system_side"]["pass"] is True,
          rv.coverage_check(payload)["system_side"])

    blank = _mini_pack(tmp, "EXTRACT-005", [("S3", "M-SYS-03", ["（无可读内容）"])])
    cov = rv.coverage_check(rv.run_compare(blank, business_no="EXTRACT-005"))
    check("覆盖率：系统侧整份材料 0 字段时闸门不通过",
          cov["system_side"]["pass"] is False
          and cov["system_side"]["empty_types"] == ["M-SYS-03"], cov["system_side"])


def test_precheck_entity_keys():
    """P-01 主体校验：主键必须先过格式校验，且只差一两个字符不判非本户。"""
    import judge as jd

    def mat(code, mtype, fields):
        return {"material_code": code, "material_type": mtype, "fields": fields}

    single = {"A": mat("A", "M-CUST-01",
                       [{"key": "credit_code", "value": "91350100260200025B"}])}
    check("P-01：有效主键不足 2 份时跳过校验",
          jd.precheck_entity(single) == [], jd.precheck_entity(single))

    noisy = {
        "A": mat("A", "M-CUST-01", [{"key": "credit_code", "value": "91350100260200025B"}]),
        "B": mat("B", "M-CUST-05", [{"key": "cert_type", "value": "营业执照"},
                                    {"key": "cert_no", "value": "913501002602000258"}]),
        "C": mat("C", "M-CUST-04", [{"key": "cert_type", "value": "营业执照"},
                                    {"key": "cert_no", "value": "(工商注册号) 123456789012345678"}]),
    }
    flagged = {i["material"] for i in jd.precheck_entity(noisy)}
    check("P-01：只差 1 个字符视为同一主体的录入误差", "B" not in flagged, flagged)
    check("P-01：主体键完全不同才判非本户", flagged == {"C"}, flagged)

    junk = {
        "A": mat("A", "M-CUST-01", [{"key": "credit_code", "value": "或注册号"}]),
        "B": mat("B", "M-CUST-05", [{"key": "cert_type", "value": "营业执照"},
                                    {"key": "cert_no", "value": "91350100260200025B"}]),
        "C": mat("C", "M-CUST-06", [{"key": "cert_type", "value": "营业执照"},
                                    {"key": "cert_no", "value": "91350100260200025B"}]),
    }
    check("P-01：标签残渣不能当主体键投票（或注册号）",
          jd.precheck_entity(junk) == [], jd.precheck_entity(junk))


def test_compare_and_verify(case):
    payload = rv.run_compare(case, keys=["sys01.cert_no", "sys01.cert_type"])
    payload["summary"] = rv.summarise(payload)
    payload["coverage"] = rv.coverage_check(payload)
    check("compare：只比指定字段", len(payload["findings"]) == 2, len(payload["findings"]))
    by_key = {f["key"]: f for f in payload["findings"]}
    check("compare：三方号码分歧判 L2（不因多数一致而放过）",
          by_key["sys01.cert_no"]["level"] == "L2", by_key["sys01.cert_no"])
    check("compare：证件类别命中同义词表判 L4",
          by_key["sys01.cert_type"]["level"] == "L4", by_key["sys01.cert_type"])
    check("compare：覆盖率自检识别出未比字段",
          payload["coverage"]["pass"] is False and payload["coverage"]["unchecked"],
          payload["coverage"]["coverage"])

    merged = rv.merge_findings(payload["findings"],
                               rv.run_compare(case, keys=["sys01.cert_type"])["findings"])
    check("compare：--append 合并后按规则表排序且不重复",
          len(merged) == 2 and {f["key"] for f in merged} == {"sys01.cert_no", "sys01.cert_type"},
          [f["key"] for f in merged])

    ver = rv.verify_findings(case, payload)
    check("verify：全部证据命中原文", ver["miss"] == 0 and ver["checked"] > 0, ver)

    broken = {"findings": [{"key": "x", "sources": [
        {"material": "D7", "value": "编造值", "evidence_quote": "这句话原文里没有"}]}]}
    check("verify：编造证据被查出来",
          rv.verify_findings(case, broken)["miss"] == 1, rv.verify_findings(case, broken))


def test_report_gate(case, tmp):
    findings = Path(tmp) / "findings.json"
    rv.emit(rv.run_compare(case, keys=["sys01.cert_no"]), str(findings))
    payload = json.loads(findings.read_text(encoding="utf-8"))
    payload["summary"] = rv.summarise(payload)
    payload["coverage"] = rv.coverage_check(payload)
    findings.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    out = Path(tmp) / "核对表.md"
    code = rv.main(["report", "--findings", str(findings), "--out", str(out)])
    check("report：未核字段存在时拦住出表（退出码 2）", code == 2 and not out.exists(), code)

    code = rv.main(["report", "--findings", str(findings), "--out", str(out), "--force"])
    text = out.read_text(encoding="utf-8") if out.exists() else ""
    check("report：--force 可出表", code == 0 and out.exists(), code)
    check("report：未核章节被保留", "未核字段与原因" in text, text[:200])
    check("report：带覆盖率行", "必核字段覆盖率" in text, text[:400])
    check("report：带免责声明", "以人工审核为准" in text, "免责声明缺失")


def test_report_channel():
    check("上报：长数字串被打码",
          "110101199001011234" not in report_channel.scrub("证件号 110101199001011234"),
          report_channel.scrub("证件号 110101199001011234"))
    check("上报：密钥被打码", "sk-abc" not in report_channel.scrub("key=sk-abcdefghijklmn"),
          report_channel.scrub("key=sk-abcdefghijklmn"))
    ok, info = report_channel.report("标题", "细节", url="")
    check("上报：没配地址时不抛异常、返回失败", ok is False and info == "no_url", (ok, info))
    ok, info = report_channel.report("标题", "细节", url="http://127.0.0.1:9/x",
                                     status="bogus")
    check("上报：非法状态被拒绝", ok is False and info == "bad_status", (ok, info))
    ok, info = report_channel.report("标题", "细节", url="http://127.0.0.1:9/x")
    check("上报：连不上时静默失败（不阻断主流程）", ok is False, (ok, info))


def build_dup_case(root):
    """同类型多版本：D7a 未签章、D7b 已签章，取值还不一样，用来验 P-04 收敛。"""
    case = Path(root) / "DUP-001"
    case.mkdir(parents=True, exist_ok=True)
    (case / "materials.json").write_text(json.dumps({
        "business_no": "DUP-001",
        "materials": [{"code": "S1", "material_type": "M-SYS-01"},
                      {"code": "D7a", "material_type": "M-CUST-07"},
                      {"code": "D7b", "material_type": "M-CUST-07"}],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "S1.json").write_text(json.dumps({
        "material_code": "S1", "material_type": "M-SYS-01",
        "text_lines": ["证件编号 | 110101199001011234"],
        "fields": [{"key": "cert_no", "value": "110101199001011234",
                    "evidence_quote": "证件编号 | 110101199001011234",
                    "readable": True, "confidence": 0.9}],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "D7a.json").write_text(json.dumps({
        "material_code": "D7a", "material_type": "M-CUST-07",
        "text_lines": ["证件编号 | 110101199001019999"],
        "fields": [{"key": "cert_no", "value": "110101199001019999",
                    "evidence_quote": "证件编号 | 110101199001019999",
                    "readable": True, "confidence": 0.9}],
    }, ensure_ascii=False), encoding="utf-8")
    (case / "D7b.json").write_text(json.dumps({
        "material_code": "D7b", "material_type": "M-CUST-07",
        "text_lines": ["证件编号 | 110101199001011234", "公章 | 某某测试有限公司"],
        "fields": [{"key": "cert_no", "value": "110101199001011234",
                    "evidence_quote": "证件编号 | 110101199001011234",
                    "readable": True, "confidence": 0.9},
                   {"key": "seal_text", "value": "某某测试有限公司",
                    "evidence_quote": "公章 | 某某测试有限公司",
                    "readable": True, "confidence": 0.9}],
    }, ensure_ascii=False), encoding="utf-8")
    return case


def test_version_convergence(tmp):
    case = build_dup_case(tmp)
    payload = rv.run_compare(case, keys=["sys01.cert_no"])
    check("收敛：未签章的多版本被淘汰",
          payload["converged_out"] == ["D7a"], payload["converged_out"])
    finding = payload["findings"][0]
    materials = {s["material"] for s in finding["sources"]}
    check("收敛：参与比对的是已签章版本",
          "D7b" in materials and "D7a" not in materials, sorted(materials))
    check("收敛：收敛后判一致（不再被旧版本污染）",
          finding["level"] == "L4", finding)

    fields, _mt, _r, _syn = rv.load_rules()
    # 规则表里「证件编号」这个名字有两个字段（受益所有人 / 客户），精确命中就该只出这两个，
    # 不能把「联系人证件编号」「经办人证件编号」这些片段命中也带进来 —— 那是 --fields 的
    # 精确优先口径；模型要指定来源时用材料内路径 `M-CUST-07:cert_no`。
    by_name = [f["key"] for f in rv.select_fields(fields, keys=["证件编号"])]
    check("字段映射：中文名精确命中且不带入片段命中",
          by_name == ["sys01.cert_no", "sys02.cert_no"], by_name)
    check("字段映射：名称片段可选中字段",
          any(f["key"] == "sys01.cert_no" for f in rv.select_fields(fields, keys=["证件"])),
          [f["key"] for f in rv.select_fields(fields, keys=["证件"])][:5])
    check("字段映射：材料内路径可选中字段",
          [f["key"] for f in rv.select_fields(fields, keys=["M-CUST-07:cert_no"])]
          == ["sys01.cert_no"],
          [f["key"] for f in rv.select_fields(fields, keys=["M-CUST-07:cert_no"])])


def test_acceptance(case, tmp):
    import copy

    import acceptance as acc

    payload = rv.run_compare(case, keys=["sys01.cert_no"])
    payload["summary"] = rv.summarise(payload)
    payload["coverage"] = rv.coverage_check(payload)
    path = Path(tmp) / "acc-findings.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")

    checks, _cov, ver = acc.run_checks(case, payload)
    by_name = {c["name"]: c for c in checks}
    check("验收：只比了部分字段时覆盖率检查判不通过",
          by_name["覆盖率：必核字段全部核到"]["pass"] is False,
          by_name["覆盖率：必核字段全部核到"])
    check("验收：证据回查通过", by_name["幻觉：全部结论的证据都能在原文命中"]["pass"] is True,
          ver)
    check("验收：免责声明检查通过",
          by_name["口径：结论带「以人工审核为准」免责声明"]["pass"] is True, checks)

    bad = copy.deepcopy(payload)
    bad["findings"][0]["sources"][0]["evidence_quote"] = "这句话原文里根本不存在"
    checks2, _c2, ver2 = acc.run_checks(case, bad)
    by2 = {c["name"]: c for c in checks2}
    check("验收：编造证据被幻觉检查抓出",
          by2["幻觉：全部结论的证据都能在原文命中"]["pass"] is False and ver2["miss"] >= 1,
          ver2["miss"])

    code = acc.main(["--materials", str(case), "--findings", str(path), "--json"])
    check("验收：命令行入口在不通过时返回 1", code == 1, code)
    code = acc.main(["--materials", str(case), "--findings", str(path),
                     "--min-coverage", "0.0", "--json"])
    check("验收：放宽覆盖率门槛后通过", code == 0, code)


def test_compare_reference_path(case, tmp):
    """参考路径 compare.py 作为独立进程跑通（--list 与 --input 两种用法）。"""
    out = Path(tmp) / "ref-findings.json"
    for args, expect in (
        ([str(SCRIPTS / "compare.py"), "--list", "--input", str(case)], "material_count"),
        ([str(SCRIPTS / "compare.py"), "--input", str(case), "--out", str(out),
          "--fields", "sys01.cert_no"], "已写出"),
    ):
        proc = subprocess.run([sys.executable, *args], capture_output=True, text=True,
                              env=os.environ.copy())
        check("参考路径：%s 正常退出" % args[1], proc.returncode == 0,
              proc.stderr[-300:])
        check("参考路径：%s 有输出" % args[1], expect in proc.stdout, proc.stdout[:200])
    check("参考路径：findings 落盘", out.exists() and out.stat().st_size > 100, out)


# ==================================================================== 真样例回归

FIXTURE_DIR = HERE.parents[1] / "tests" / "fixtures"
SAMPLE_PACK = FIXTURE_DIR / "sample-pack"
SAMPLE_CASE = "100012798-full"


def _unlink(path):
    try:
        Path(path).unlink()
    except OSError:
        pass


def _sample_case_dir():
    path = SAMPLE_PACK / SAMPLE_CASE
    return path if (path / "materials.json").exists() else None


def test_sample_pack():
    """拿 13 份真样例跑工具链。

    样例数据由 `07_抽取评测/runner/build_sample_fixture.py` 从 golden + 评测产物固化
    （转写文本、抽取结果、期望值），所以这里既验工具链，也验「评测产物 → 核对结论」
    这一段的一致性。缺 fixture 时跳过，不把没数据的检查算成失败。
    """
    case = _sample_case_dir()
    if case is None:
        print("SKIP 真样例：找不到 %s（先跑 runner/build_sample_fixture.py）" % SAMPLE_PACK)
        return
    expected = json.loads((SAMPLE_PACK / "expected.json").read_text(encoding="utf-8"))
    case_expected = expected["cases"][SAMPLE_CASE]

    # 1) 清点
    info = rv.inspect_materials(case)
    check("样例：清点出 13 份材料", info["material_count"] == 13, info["material_count"])
    check("样例：必备材料齐备", not info["missing_required"], info["missing_required"])
    typed = [m for m in info["materials"] if m["material_type"]]
    check("样例：材料类型全部识别", len(typed) == 13, len(typed))
    again = rv.inspect_materials(case)
    check("样例：指纹稳定（同输入两次一致）",
          [m["fingerprint"] for m in info["materials"]] == [m["fingerprint"] for m in again["materials"]])
    check("样例：转写文本非空（每份都有行）",
          all(m["transcript_lines"] > 0 for m in info["materials"]),
          [(m["material_code"], m["transcript_lines"]) for m in info["materials"]])

    # 2) 全量比对 + 证据回查：这一批材料的结论必须零幻觉
    payload = rv.run_compare(case, business_no="100012798")
    payload["summary"] = rv.summarise(payload)
    verify = rv.verify_findings(case, payload)
    check("样例：证据回查零未命中（无幻觉）", verify["miss"] == 0,
          [r for r in verify["results"] if not r["hit"]][:3])
    check("样例：跨行引文被正确判为命中（不再误报幻觉）",
          verify.get("cross_line", 0) >= 1, verify.get("cross_line"))
    check("样例：结论都带免责声明", "人工审核" in payload["disclaimer"])

    # 3) golden 作为裁判：评测判对的字段就是「样例答案」，固化成回归基线
    evaluated = correct = fabricated = 0
    wrong = []
    for material_code, item in case_expected["materials"].items():
        for rec in item["extracted"]:
            if not rec.get("eval_key"):
                continue
            golden = next((g for g in item["fields"] if g["eval_key"] == rec["eval_key"]), None)
            if golden is None:
                continue
            evaluated += 1
            if rec.get("correct") is True:
                correct += 1
            else:
                wrong.append((material_code, rec["eval_key"], golden["golden"], rec.get("value")))
            if rec.get("gated_out") and rec.get("value") not in (None, ""):
                fabricated += 1
    # 评测层口径是 70 字段 / 69 正确；这里数的是「有技能包字段映射、能进核对表」的那部分
    # （golden 里有 2 个字段没有映射，导不出来），所以是 69 / 68。
    check("样例：固化的评测基线是 69 条可导出字段 / 68 正确",
          evaluated == 69 and correct == 68,
          {"evaluated": evaluated, "correct": correct, "wrong": wrong[:5]})
    check("样例：唯一判错的是 F4 业务专用章（已知结构限制：椭圆章被电子签章水印打成马赛克）",
          [w[1] for w in wrong] == ["business_stamp"], wrong)
    check("样例：证据闸门拦下的字段没有留下取值（无实质幻觉）", fabricated == 0, fabricated)

    # 4) 确定性抽取器在真样例上的行为：清洗规则必须挡住界面控件文字与一行多字段
    s2 = rv.extract_for_material(case, "S2")
    s2_values = {f["key"]: f["value"] for f in s2["fields"]}
    check("样例抽取：行尾界面控件文字被剔除（S2 的 `AI`）",
          s2_values.get("customer_name") == "机构开户审核", s2_values.get("customer_name"))
    check("样例抽取：一行多字段时值止于下一个标签（S2 证件地址）",
          s2_values.get("contact_address") == "福建省福州市鼓楼区湖东路路100号",
          s2_values.get("contact_address"))
    check("样例抽取：长证件号不被噪声规则误伤",
          s2_values.get("cert_no") == "91350100260200025B", s2_values.get("cert_no"))
    s1 = rv.extract_for_material(case, "S1")
    s1_values = {f["key"]: f["value"] for f in s1["fields"]}
    check("样例抽取：S1 抽到 10 个字段",
          s1["extracted"] == 10, (s1["extracted"], s1["requested"]))
    check("样例抽取：证件起始/截止日期按标签区分",
          (s1_values.get("cert_start_date"), s1_values.get("cert_end_date")) == ("20260902", "20460902"),
          (s1_values.get("cert_start_date"), s1_values.get("cert_end_date")))
    s5 = rv.extract_for_material(case, "S5")
    check("样例抽取：别名表覆盖截图写法（S5 用「姓名」而不是「法定代表人」）",
          s5["extracted"] >= 9 and s5["fields"][0]["value"] == "测试",
          (s5["extracted"], [f["value"] for f in s5["fields"][:2]]))
    check("样例抽取：纯数字行尾标记不被当噪声剔除",
          "AI" not in s1_values.values() and "17750999620" in
          [f["value"] for f in rv.extract_for_material(case, "S5")["fields"]],
          s1_values)

    # 5) 覆盖率闸门：默认必须拦住（样例集覆盖不到全部必核字段），--force 才出表
    findings_path = SAMPLE_PACK / "_tmp-findings.json"
    findings_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    report_path = SAMPLE_PACK / "_tmp-report.md"
    proc = subprocess.run([sys.executable, str(SCRIPTS / "review.py"), "report",
                           "--findings", str(findings_path), "--out", str(report_path)],
                          capture_output=True, text=True)
    check("样例：未核项存在时闸门拦住出表（非零退出）", proc.returncode != 0, proc.returncode)
    # 未核原因有两种：材料没提供（缺件）／材料提供了但里面没这个栏目（字段名没对齐）。
    # 拦住时两种都要能打出来，人才知道该补件还是该重跑抽取。
    check("样例：拦住时打印未核清单与原因",
          "覆盖率自检未通过" in proc.stdout
          and ("无任何来源材料提供该字段" in proc.stdout
               or "材料里没有该栏目" in proc.stdout),
          proc.stdout[:300])
    proc = subprocess.run([sys.executable, str(SCRIPTS / "review.py"), "report",
                           "--findings", str(findings_path), "--out", str(report_path), "--force"],
                          capture_output=True, text=True)
    check("样例：--force 出表成功", proc.returncode == 0 and report_path.exists(),
          proc.stderr[-200:])
    body = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    check("样例：强制出表仍保留「未核」章节", "未核" in body, body[:200])

    # 6) 验收：按样例集基线判，六项全过
    baseline_path = SAMPLE_PACK / "coverage-baseline.json"
    proc = subprocess.run([sys.executable, str(SCRIPTS / "acceptance.py"),
                           "--materials", str(case), "--findings", str(findings_path),
                           "--baseline", str(baseline_path), "--json"],
                          capture_output=True, text=True)
    result = json.loads(proc.stdout) if proc.stdout.strip().startswith("{") else {}
    check("样例：验收通过（覆盖率按基线、零幻觉、三态与口径齐备）",
          proc.returncode == 0 and result.get("pass") is True, proc.stdout[:400])
    check("样例：覆盖率为基线值 0.6038",
          result.get("coverage") == 0.6038, result.get("coverage"))

    # 7) 基线守卫：把基线外的字段人为变成未核，必须判失败
    if baseline_path.exists():
        baseline = json.loads(baseline_path.read_text(encoding="utf-8"))
        shaved = json.loads(findings_path.read_text(encoding="utf-8"))
        keep_key = None
        for finding in shaved["findings"]:
            if finding.get("required") and finding.get("sources") \
                    and finding["key"] not in set(baseline["uncovered_keys"]):
                keep_key = finding["key"]
                break
        if keep_key:
            for finding in shaved["findings"]:
                if finding["key"] == keep_key:
                    finding["sources"] = []
            shaved_path = SAMPLE_PACK / "_tmp-shaved.json"
            shaved_path.write_text(json.dumps(shaved, ensure_ascii=False), encoding="utf-8")
            proc = subprocess.run([sys.executable, str(SCRIPTS / "acceptance.py"),
                                   "--materials", str(case), "--findings", str(shaved_path),
                                   "--baseline", str(baseline_path), "--json"],
                                  capture_output=True, text=True)
            check("样例：基线之外出现新增未核时判失败（闸门没放水）",
                  proc.returncode == 1, proc.stdout[:300])
            _unlink(shaved_path)
    for path in (findings_path, report_path):
        _unlink(path)


def test_autoreport(tmp):
    """子命令自动上报轨迹：靠模型记得调 report_channel 不可靠（真机实测没调）。

    这里只验「没注入上报地址时行为完全不变」与「有地址时按子命令产出标题」，
    真的网络上报在真机验收里验（需要 harness 令牌）。"""
    import os as _os

    saved = {key: _os.environ.get(key) for key in
             ("DWP_EXECUTION_EVENT_URL", "DWP_AGENT_TOOL_TOKEN", "DWP_TRACE_ID")}
    for key in saved:
        _os.environ.pop(key, None)
    try:
        case = build_case(tmp)
        rv._LAST_PAYLOAD.clear()
        rv._LAST_PAYLOAD.update(rv.inspect_materials(case))
        rv._autoreport("inspect", type("A", (), {"kind": None})())
        check("自动上报：没配地址时静默跳过", True)

        rv._LAST_PAYLOAD.clear()
        rv._LAST_PAYLOAD.update({"material": "D1", "extracted": 6, "requested": 8,
                                 "missing": [{"key": "x"}, {"key": "y"}]})
        title, detail = rv._summarize("extract", type("A", (), {"kind": None})())
        check("自动上报：抽取的标题带材料与数量",
              title == "抽取 D1：6/8 个字段" and "未定位 2 个" in detail, (title, detail))

        rv._LAST_PAYLOAD.clear()
        rv._LAST_PAYLOAD.update({"summary": {"total_fields": 93,
                                             "by_level": {"L2": 11, "L3": 35, "L4": 27}},
                                 "material_issues": [1, 2, 3, 4]})
        title, detail = rv._summarize("compare", type("A", (), {"kind": None})())
        check("自动上报：比对的标题带一致/不一致/待确认",
              title == "比对 93 个字段" and "一致 27 / 不一致 11 / 待确认 35" in detail,
              (title, detail))

        rv._LAST_PAYLOAD.clear()
        rv._LAST_PAYLOAD.update({"checked": 66, "miss": 0, "cross_line": 4})
        title, detail = rv._summarize("verify", type("A", (), {"kind": None})())
        check("自动上报：回查的标题带未命中与跨行命中",
              title == "证据回查 66 条" and "未命中 0 条" in detail and "跨行命中 4 条" in detail,
              (title, detail))

        rv._LAST_PAYLOAD.clear()
        rv._LAST_PAYLOAD.update({"coverage": {"coverage": 0.6226, "unchecked": [1] * 20}})
        title, detail = rv._summarize("report", type("A", (), {"kind": None})())
        check("自动上报：渲染的标题带覆盖率与未核数",
              title == "渲染核对表" and "0.6226" in detail and "未核 20 项" in detail,
              (title, detail))
    finally:
        for key, value in saved.items():
            if value is None:
                _os.environ.pop(key, None)
            else:
                _os.environ[key] = value


def build_flat_case(root):
    """平铺材料包：两笔业务混在一层，只靠 hint_business_no 提示区分（平台默认布局）。"""
    case = Path(root) / "PACK-FLAT"
    case.mkdir(parents=True, exist_ok=True)
    (case / "materials.json").write_text(json.dumps({
        "pack_id": "PACK-TEST",
        "layout": "flat",
        "materials": [
            {"code": "D1", "hint_business_no": "B2026001", "source_file": "营业执照.png"},
            {"code": "D2", "hint_business_no": "B2026001", "source_file": "开户合同.png"},
            {"code": "D8", "hint_business_no": "B2026002", "source_file": "开户截图.png"},
            {"code": "D9", "hint_business_no": "UNGROUPED", "source_file": "扫描件.png"},
        ],
    }, ensure_ascii=False), encoding="utf-8")
    bodies = {
        "D1": ["名称 | 某某测试有限公司", "统一社会信用代码 | 91350100MA2Y0001X"],
        "D2": ["名称 | 某某测试有限公司", "签署日期 | 2026-09-01"],
        "D8": ["名称 | 另一家测试有限公司", "开户截图里的值"],
        "D9": ["名称 | 第三家测试有限公司", "备注 | 单独一份"],
    }
    for code, lines in bodies.items():
        (case / ("%s.json" % code)).write_text(json.dumps({
            "material_code": code, "material_type": "M-CUST-01",
            "source_file": "%s.png" % code, "kind": "image", "text_lines": lines,
        }, ensure_ascii=False), encoding="utf-8")
    return case


def test_codes_and_groups(tmp):
    """平铺布局下的逻辑分笔：inspect 给提示、--codes 圈定本轮材料、verify 不被 codes 带偏。"""
    case = build_flat_case(tmp)
    info = rv.inspect_materials(case)
    check("平铺：清点出 4 份材料", info["material_count"] == 4, info["material_count"])
    check("平铺：layout 标记为 flat", info["layout"] == "flat", info.get("layout"))
    groups = {g["hint"]: g["codes"] for g in info["suggested_groups"]}
    check("平铺：按 hint_business_no 聚出两笔",
          groups.get("B2026001") == ["D1", "D2"] and groups.get("B2026002") == ["D8"], groups)
    check("平铺：没有提示的落到机构名兜底（不硬猜）",
          groups.get("第三家测试有限公司") == ["D9"], groups)

    only = rv.inspect_materials(case, codes=["D1", "D2"])
    check("--codes：只装载指定的材料",
          sorted(m["material_code"] for m in only["materials"]) == ["D1", "D2"],
          [m["material_code"] for m in only["materials"]])
    check("--codes：未选中的材料不报成「声明但缺失」",
          only["declared_but_absent"] == [], only["declared_but_absent"])
    check("--codes：selected_codes 回填", only["selected_codes"] == ["D1", "D2"],
          only["selected_codes"])
    check("--codes：大小写不敏感",
          sorted(rv.load_materials(case, codes=["d1", "D2"])) == ["D1", "D2"],
          sorted(rv.load_materials(case, codes=["d1", "D2"])))

    payload = rv.run_compare(case, keys=["sys01.cert_no"], codes=["D1", "D2"])
    check("compare --codes：本轮材料被写进结果",
          payload["selected_codes"] == ["D1", "D2"], payload["selected_codes"])

    fake = {"findings": [
        {"key": "sys01.cert_no", "sources": [
            {"material": "D1", "value": "91350100MA2Y0001X",
             "evidence_quote": "统一社会信用代码 | 91350100MA2Y0001X"}]},
        {"key": "sys01.cert_no", "sources": [
            {"material": "D8", "value": "开户截图里的值", "evidence_quote": "开户截图里的值"}]},
    ]}
    filtered = rv.verify_findings(case, fake, codes=["D1"])
    check("verify --codes：只回查引用到本轮材料的结论", filtered["checked"] == 1, filtered)
    check("verify --codes：仍用全量材料回查（真命中不被误判成幻觉）",
          filtered["miss"] == 0, filtered)


def test_audit_command(tmp):
    """一站式 audit：结论与分步等价、覆盖率闸门同样生效、--force 才出表、--codes 正确传递。"""
    case = build_case(tmp)
    out_dir = Path(tmp) / "audit-out"

    # 1) 默认：样例集覆盖不到全部必核字段 → 必须被闸门拦住（退出码 2），但 findings 已落盘
    code = rv.main(["audit", "--materials", str(case), "--out-dir", str(out_dir)])
    findings_path = out_dir / "findings.json"
    report_path = out_dir / "核对表.md"
    check("audit：findings 已落盘", findings_path.is_file(), str(findings_path))
    check("audit：未核项存在时拦住出表（退出码 2）",
          code == 2 and not report_path.exists(), code)
    payload = json.loads(findings_path.read_text(encoding="utf-8"))
    check("audit：覆盖率闸门与 report 同口径",
          payload["coverage"]["pass"] is False, payload["coverage"]["coverage"])
    result = dict(rv._LAST_PAYLOAD)
    check("audit：结果里同时带摘要与证据回查",
          "summary" in result and "verify" in result and result["report_written"] is False,
          list(result))

    # 2) 与分步 compare 的结论必须等价（audit 不许另起一套判定）
    stepwise = rv.run_compare(case, keys=["sys01.cert_no"])
    by_key = {f["key"]: f for f in payload["findings"]}
    ref = {f["key"]: f for f in stepwise["findings"]}
    check("audit：与分步 compare 的字段判定一致",
          by_key["sys01.cert_no"]["level"] == ref["sys01.cert_no"]["level"],
          (by_key["sys01.cert_no"]["level"], ref["sys01.cert_no"]["level"]))

    # 3) --force：未核项存在时仍出表，且保留未核章节
    code = rv.main(["audit", "--materials", str(case), "--out-dir", str(out_dir), "--force"])
    check("audit --force：仍能出表", code == 0 and report_path.exists(), code)
    body = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    check("audit --force：核对表含覆盖率行与免责声明",
          "必核字段覆盖率" in body and "以人工审核为准" in body, body[:200])

    # 4) --codes 逻辑分笔必须传递到 compare
    codes_dir = Path(tmp) / "audit-codes"
    rv.main(["audit", "--materials", str(case), "--codes", "S1,D7",
             "--out-dir", str(codes_dir), "--force"])
    coded = json.loads((codes_dir / "findings.json").read_text(encoding="utf-8"))
    check("audit --codes：只核指定材料", coded["selected_codes"] == ["D7", "S1"],
          coded["selected_codes"])

    # 5) 轨迹上报：audit 的标题要能反映「一致/不一致/待确认 + 是否出表」
    rv._LAST_PAYLOAD.clear()
    rv._LAST_PAYLOAD.update({"summary": {"total_fields": 93,
                                         "by_level": {"L2": 11, "L3": 35, "L4": 27}},
                             "coverage": {"coverage": 0.6226},
                             "verify": {"miss": 0},
                             "report_written": True})
    title, detail = rv._summarize("audit", type("A", (), {"kind": None})())
    check("audit：自动上报的标题带字段数与结果",
          title == "一站式核对：93 个字段" and "一致 27 / 不一致 11 / 待确认 35" in detail
          and "已出表" in detail, (title, detail))


def test_type_guess(tmp):
    """类型猜测：文件名别名命中、只在空值时写回、认不出就不硬猜。"""
    pack = Path(tmp) / "GUESS-001"
    pack.mkdir(parents=True, exist_ok=True)
    samples = {
        "D1": ("营业执照.png", "营业执照"),
        "D2": ("工商公示信息.png", "工商公示信息"),
        "D3": ("法人证件信息.png", "自然人身份证件"),
        "D4": ("三方存管协议.png", "三方存管协议"),
        "D5": ("开户合同签字页.png", "开户合同签字页"),
        "D6": ("资金账户开户申请表_已盖章.png", "资金账户开户申请表（已签章版）"),
    }
    for code, (fname, _name) in samples.items():
        (pack / ("%s.json" % code)).write_text(json.dumps({
            "material_code": code, "material_type": None, "kind": "image",
            "source_file": fname, "text_lines": ["某机构开户材料"], "fields": [],
        }, ensure_ascii=False), encoding="utf-8")
    (pack / "X9.json").write_text(json.dumps({
        "material_code": "X9", "material_type": None, "kind": "image",
        "source_file": "扫描件_20260101.png", "text_lines": ["无关键字样"], "fields": [],
    }, ensure_ascii=False), encoding="utf-8")

    guesses = {g["material"]: g for g in rv.apply_type_guesses(pack)}
    for code, (fname, want) in samples.items():
        check("类型猜测：%s → %s" % (fname, want),
              guesses[code]["material_type"] and guesses[code]["name"] == want, guesses[code])
    check("类型猜测：认不出的材料不硬猜",
          guesses["X9"]["material_type"] is None and guesses["X9"]["written"] is False,
          guesses["X9"])
    written = json.loads((pack / "D1.json").read_text(encoding="utf-8"))
    check("类型猜测：猜到的类型已写回材料文件",
          written["material_type"] == "M-CUST-01", written.get("material_type"))

    # 已有类型不被覆盖：模型改过的判定必须保留
    written["material_type"] = "M-CUST-09"
    (pack / "D1.json").write_text(json.dumps(written, ensure_ascii=False), encoding="utf-8")
    again = {g["material"]: g for g in rv.apply_type_guesses(pack)}
    check("类型猜测：已有类型不被覆盖（模型改过的保留）",
          again["D1"]["material_type"] == "M-CUST-09" and again["D1"]["written"] is False,
          again["D1"])

    # inspect 也要把猜测暴露出来，便于模型复核（仅在材料自身没有类型时）
    d2 = json.loads((pack / "D2.json").read_text(encoding="utf-8"))
    d2["material_type"] = None
    (pack / "D2.json").write_text(json.dumps(d2, ensure_ascii=False), encoding="utf-8")
    info = rv.inspect_materials(pack)
    guess_fields = [i.get("type_guess") for i in info["materials"] if i["material_code"] == "D2"]
    check("类型猜测：inspect 暴露 type_guess 供复核",
          guess_fields == ["M-CUST-02"], guess_fields)


# ==================================================================== 核对表渲染

_SEP = re.compile(r"^\|(?:\s*:?-{2,}:?\s*\|)+$")


def _cells(line):
    """拆一行 Markdown 表格。先把转义竖线占位，否则 `\\|` 会被当成列分隔。"""
    body = line.strip().replace("\\|", "\x00")
    parts = body.split("|")
    if parts and parts[0].strip() == "":
        parts = parts[1:]
    if parts and parts[-1].strip() == "":
        parts = parts[:-1]
    return [p.replace("\x00", "\\|").strip() for p in parts]


def _table_shapes(markdown):
    """返回 [(表头, 各行单元格列表)] —— 用来断言表格没有错列。"""
    shapes = []
    lines = markdown.splitlines()
    index = 0
    while index < len(lines):
        if (lines[index].lstrip().startswith("|")
                and index + 1 < len(lines) and _SEP.match(lines[index + 1].strip())):
            head = _cells(lines[index])
            rows = []
            index += 2
            while index < len(lines) and lines[index].lstrip().startswith("|"):
                rows.append(_cells(lines[index]))
                index += 1
            shapes.append((head, rows))
        else:
            index += 1
    return shapes


def build_gap_case(root):
    """只给一份系统侧 + 一份客户侧，且客户侧缺该栏目、必备材料大面积缺失。

    用来验「缺口置顶」与「两种缺口分开报」：材料缺失 vs 字段名没对齐。
    """
    case = Path(root) / "GAP-001"
    case.mkdir(parents=True, exist_ok=True)
    (case / "materials.json").write_text(json.dumps({
        "business_no": "GAP-001",
        "materials": [{"code": "S1", "material_type": "M-SYS-01"},
                      {"code": "D1", "material_type": "M-CUST-01"}],
    }, ensure_ascii=False), encoding="utf-8")
    # cert_no 的取值里带 `<script>`：这个值会进核对表，用来验 HTML 渲染是否转义
    (case / "S1.json").write_text(json.dumps({
        "material_code": "S1", "material_type": "M-SYS-01",
        "text_lines": ["证件类别 | 身份证", "证件编号 | <script>alert(1)</script>"],
        "fields": [{"key": "cert_type", "value": "身份证",
                    "evidence_quote": "证件类别 | 身份证", "readable": True, "confidence": 0.9},
                   {"key": "cert_no", "value": "<script>alert(1)</script>",
                    "evidence_quote": "证件编号 | <script>alert(1)</script>",
                    "readable": True, "confidence": 0.9}],
    }, ensure_ascii=False), encoding="utf-8")
    # 营业执照只抽到 credit_code，没有 cert_no / cert_type —— 制造「已提供但缺该栏目」
    (case / "D1.json").write_text(json.dumps({
        "material_code": "D1", "material_type": "M-CUST-01",
        "text_lines": ["统一社会信用代码 | 91350100MA2Y0001X"],
        "fields": [{"key": "credit_code", "value": "91350100MA2Y0001X",
                    "evidence_quote": "统一社会信用代码 | 91350100MA2Y0001X",
                    "readable": True, "confidence": 0.9}],
    }, ensure_ascii=False), encoding="utf-8")
    return case


def test_report_completeness(tmp):
    """核对表必须：缺口置顶、两种缺口分开、表格不错列、HTML 与 Markdown 同源。"""
    import report as rp

    case = build_gap_case(tmp)
    payload = rv.run_compare(case)
    payload["summary"] = rv.summarise(payload)
    payload["coverage"] = rv.coverage_check(payload)

    missing_codes = {m["code"] for m in payload["missing_required"]}
    check("核对表：必备材料缺失被写进 findings",
          {"M-CUST-03", "M-CUST-05", "M-CUST-06", "M-CUST-07"} <= missing_codes,
          sorted(missing_codes))
    unchecked = payload["coverage"]["unchecked"]
    check("核对表：未核字段带缺口归因",
          all("missing_materials" in i for i in unchecked), unchecked[:2])

    md = rp.build(payload)

    # 1) 缺口置顶：第一节就是完整性，且在材料清单之前
    check("核对表：第一节是「本轮材料完整性」",
          "## 零、本轮材料完整性" in md, md[:400])
    check("核对表：缺口节排在材料清单之前",
          md.index("本轮材料完整性") < md.index("比对材料清单"), "顺序不对")
    check("核对表：列出缺失的必备材料",
          "自然人身份证件" in md and "证券账户开户申请表" in md, "缺件未列出")
    check("核对表：未核字段按缺口归并（不是平铺几十行）",
          "未核字段与原因" in md and "缺口（主因）" in md, md[:600])
    check("核对表：两种缺口分开报（材料缺失 vs 字段名未对齐）",
          "材料已提供，但材料里没有对应栏目" in md, "两种缺口没分开")

    # 2) 表格不错列：每张表的表头列数必须等于每一行的单元格数
    bad = []
    for head, rows in _table_shapes(md):
        for row in rows:
            if len(row) != len(head):
                bad.append((head, row))
    check("核对表：所有 Markdown 表格表头与行单元格数一致（不错列）", not bad, bad[:2])

    # 3) 一致率与覆盖率各带分子分母，且是两个独立指标
    summary_rows = [row for head, rows in _table_shapes(md)
                    if head and head[0] == "指标" for row in rows]
    rate = [r for r in summary_rows if r[0].startswith("**一致率")]
    cover = [r for r in summary_rows if r[0].startswith("**必核字段覆盖率")]
    check("核对表：一致率与覆盖率各带分子分母",
          rate and cover and "/" in rate[0][1] and "/" in cover[0][1],
          (rate, cover))

    # 4) 宽表列名用材料名而不是代号
    check("核对表：宽表列头用材料名称而不是代号",
          "营业执照" in md and "M-CUST-01" not in md, "列头仍是代号")

    # 5) HTML：同一份文档块渲染，脚本内容必须被转义
    html_text = rp.build_html(payload)
    check("核对表：HTML 有 doctype 与表格", "<!DOCTYPE html>" in html_text
          and "<table>" in html_text, html_text[:80])
    check("核对表：HTML 与 Markdown 章节一致",
          "本轮材料完整性" in html_text and "核对结论" in html_text, "章节缺失")
    check("核对表：HTML 转义材料里的脚本（防注入）",
          "<script>alert(1)</script>" not in html_text and "&lt;script&gt;" in html_text,
          "未转义")

    # 6) 命令行：HTML 默认产出（不带任何参数）
    findings_path = Path(tmp) / "gap-findings.json"
    findings_path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    md_path = Path(tmp) / "核对表.md"
    html_path = Path(tmp) / "核对表.html"
    code = rp.main(["--findings", str(findings_path), "--out", str(md_path)])
    check("核对表：report.py 默认就产出 Markdown 与 HTML 两份",
          code == 0 and md_path.is_file() and html_path.is_file(), code)

    # 7) --html 可指定路径；--no-html 才只出 Markdown
    other_html = Path(tmp) / "custom.html"
    code = rp.main(["--findings", str(findings_path), "--out", str(md_path),
                    "--html", str(other_html)])
    check("核对表：report.py --html 可指定 HTML 路径",
          code == 0 and other_html.is_file(), code)

    plain_md = Path(tmp) / "核对表-只md.md"
    code = rp.main(["--findings", str(findings_path), "--out", str(plain_md), "--no-html"])
    check("核对表：report.py --no-html 只出 Markdown",
          code == 0 and plain_md.is_file()
          and not plain_md.with_suffix(".html").is_file(), code)


def test_codes_validation_and_product_files(tmp):
    """未知代号要当场报错；产物文件不能被当成材料。"""
    case = build_case(tmp)

    try:
        rv.load_materials(case, codes=["NOPE"])
        raised = False
    except SystemExit as exc:
        raised = "未知材料代号" in str(exc)
    check("代号校验：未知 --codes 当场报错（不再静默忽略）", raised, "没报错")

    # 真机踩过：findings.json 被写回材料包目录，下一轮被当成一份材料
    (case / "findings.json").write_text(json.dumps({
        "business_no": "TEST-001", "selected_codes": ["S1"],
        "findings": [], "summary": {}, "materials": [],
    }, ensure_ascii=False), encoding="utf-8")
    loaded = rv.load_materials(case)
    check("产物过滤：findings.json 不被当成材料",
          "findings" not in loaded and sorted(loaded) == ["D3", "D7", "S1"], sorted(loaded))
    try:
        rv.load_materials(case, codes=["findings"])
        raised = False
    except SystemExit as exc:
        raised = "未知材料代号" in str(exc)
    check("产物过滤：--codes findings 报未知代号", raised, "没报错")
    try:
        os.remove(str(case / "findings.json"))
    except OSError:
        pass


def test_audit_html(tmp):
    """audit 默认就产出 Markdown 与 HTML 两份；--no-html 才只出 Markdown。"""
    case = build_case(tmp)
    out_dir = Path(tmp) / "audit-html"
    code = rv.main(["audit", "--materials", str(case), "--out-dir", str(out_dir), "--force"])
    md_path = out_dir / "核对表.md"
    html_path = out_dir / "核对表.html"
    check("audit：默认就落盘 Markdown 与 HTML 两份",
          code == 0 and md_path.is_file() and html_path.is_file(), (code, str(html_path)))
    result = dict(rv._LAST_PAYLOAD)
    check("audit：结果里回报 HTML 路径",
          result.get("report_html") == str(html_path), result.get("report_html"))
    check("audit：next_hint 明确禁止自写报告",
          "不要自己写比对脚本" in (result.get("next_hint") or ""), result.get("next_hint"))
    check("audit：next_hint 交代 HTML 是本轮交付物",
          "HTML" in (result.get("next_hint") or ""), result.get("next_hint"))

    off_dir = Path(tmp) / "audit-no-html"
    code = rv.main(["audit", "--materials", str(case), "--out-dir", str(off_dir),
                    "--force", "--no-html"])
    check("audit --no-html：只出 Markdown",
          code == 0 and (off_dir / "核对表.md").is_file()
          and not (off_dir / "核对表.html").is_file(), code)

    # 被覆盖率闸门拦住时不能回报一个并不存在的 HTML 路径（模型会照着去读）
    blocked_dir = Path(tmp) / "audit-blocked"
    code = rv.main(["audit", "--materials", str(case), "--out-dir", str(blocked_dir)])
    blocked = dict(rv._LAST_PAYLOAD)
    check("audit：闸门拦住时 report_html 为空且不产出文件",
          code != 0 and not blocked.get("report_html")
          and not (blocked_dir / "核对表.html").is_file(),
          (code, blocked.get("report_html")))


def main():
    tmp = Path(tempfile.mkdtemp(prefix="skill-toolkit-"))
    try:
        case = build_case(tmp)
        test_inspect(case)
        test_rules()
        test_extract(case)
        test_extract_quality(tmp)
        test_system_side_is_baseline(tmp)
        test_precheck_entity_keys()
        test_compare_and_verify(case)
        test_report_gate(case, tmp)
        test_report_channel()
        test_version_convergence(tmp)
        test_acceptance(case, tmp)
        test_compare_reference_path(case, tmp)
        test_autoreport(tmp)
        test_codes_and_groups(tmp)
        test_audit_command(tmp)
        test_type_guess(tmp)
        test_report_completeness(tmp)
        test_codes_validation_and_product_files(tmp)
        test_audit_html(tmp)
        test_sample_pack()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("-" * 60)
    print("通过 %d 项，失败 %d 项" % (len(PASSED), len(FAILED)))
    if FAILED:
        for name in FAILED:
            print("  失败：%s" % name)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
