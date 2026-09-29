"""P-01 ~ P-06 前置校验、J-01 ~ J-10 判定、L1 ~ L5 分级。纯 stdlib。

判定优先级：不一致（L2）> 待确认（L3）> 一致（L4）。任取两个有值来源归一化后不等即判 L2，
不因为多数来源一致而放过少数分歧——分歧本身就是需要人工看的东西。
"""

from __future__ import annotations

import re

import normalize as nz

LEVEL_NAME = {"L1": "严重风险", "L2": "不一致", "L3": "待确认", "L4": "一致", "L5": "系统独有"}
VISUAL_KEYS = {"applicant_signature", "operator_signature", "reviewer_signature", "customer_signature",
               "party_a_seal", "party_a_stamp", "business_stamp", "agency_seal", "seal", "seal_text"}


def _same(a, b):
    return a == b


def _prefix_contained(short, long_):
    """N-05 / J-05：短值被长值包含，且差异只在行政层级前缀。"""
    if not short or not long_:
        return False
    return (short in long_) or (long_ in short)


def judge_field(field, sources, syn):
    """sources: [{"material":..., "value":..., "readable":..., "position":...}]"""
    key = field["key"]
    codes = field.get("normalize") or []
    declared = field.get("sources") or []
    declared_codes = [s.get("material") for s in declared]

    prepared = []
    for src in sources:
        raw = src.get("value")
        state = nz.empty_state(raw)
        item = dict(src)
        item["empty_state"] = state
        if state == "value":
            norm = nz.normalize_value(codes, raw, syn, key)
            item["normalized"] = norm["normalized"]
            item["tags"] = norm["tags"]
        else:
            item["normalized"] = ""
            item["tags"] = [state]
        prepared.append(item)

    valid = [s for s in prepared if s["empty_state"] == "value"]
    missing = [s for s in prepared if s["empty_state"] != "value"]

    # J-08 系统独有
    if not declared:
        return _result(field, prepared, "L5", "J-08", "系统侧字段无外部来源，不计入一致率分母")

    # J-07 视觉三态
    if field.get("judge") == "J-07" or any(str(s.get("path", "")).endswith(tuple(VISUAL_KEYS)) for s in declared):
        states = []
        for s in valid:
            text = str(s.get("value") or "")
            if any(t in text for t in ("看不清", "模糊", "无法辨认")):
                states.append("看不清")
            elif nz.empty_state(text) == "value":
                states.append("有")
            else:
                states.append("无")
        if not valid:
            return _result(field, prepared, "L3", "J-07", "视觉判定：看不清（无有效读数），需人工看图")
        if "看不清" in states:
            return _result(field, prepared, "L3", "J-07", "视觉判定：看不清，需人工看图")
        return _result(field, prepared, "L3", "J-07",
                       "视觉判定：%s（印章/签字类只出三态，不给一致性结论）" % "、".join(sorted(set(states))))

    # J-06 单边为空 / 无任何来源
    #   系统录入侧是**基准方**（side=system），不是对照方：只有基准方有值、客户侧一份都没
    #   读出来时，结论是「无从比对」而不是「一致」。真机上系统侧取值缺失时，单个客户侧来源
    #   会被判成 L4（归一化后一致），造出一整片假一致 —— 这正是判定准确率的最大来源。
    if not valid:
        return _result(field, prepared, "L3", "J-06", "全部来源均未读出，待人工确认或补件")
    valid_sys = [s for s in valid if s.get("side") == "system"]
    valid_cust = [s for s in valid if s.get("side") != "system"]
    if not valid_cust:
        return _result(field, prepared, "L3", "J-06",
                       "客户侧来源均未读出取值，无从与系统录入值比对")
    if not valid_sys:
        return _result(field, prepared, "L3", "J-06",
                       "系统录入侧该栏为空，单边取值待人工确认")

    # 归一化后是否存在分歧
    distinct = {s["normalized"] for s in valid}
    if len(distinct) > 1:
        # J-04 同义命中
        syn_key = {"N-04": "cert_type", "N-07": "org_type"}.get(codes[0] if codes else "")
        if syn_key:
            groups = [nz.group_of(s.get("value"), syn.get(syn_key, [])) for s in valid]
            if all(g is not None for g in groups) and len({tuple(g) for g in groups}) == 1:
                return _result(field, prepared, "L4", "J-04", "命中同义词表，判一致")
        # N-12 枚举主体一致
        if "N-12" in codes:
            mains = {nz.n_enum(s.get("value")) for s in valid}
            if len(mains) == 1:
                return _result(field, prepared, "L4", "J-04", "枚举主体一致，括号内说明差异忽略")
        # J-09 体系映射待定（要排在 J-05 之前：体系不同的字段本来就没有可比性，
        # 用「短值被长值包含」去兜底会把 `机构` 与 `有限责任公司` 判成一致）
        if field.get("judge") == "J-09":
            return _result(field, prepared, "L3", "J-09", "双方分类体系不同且无映射表，需人工确认")
        # J-05 包含一致
        if len(valid) == 2 and _prefix_contained(valid[0]["normalized"], valid[1]["normalized"]):
            return _result(field, prepared, "L4", "J-05", "短值被长值包含，差异仅为行政层级前缀")
        # 掩码姓名降级
        if any("masked_name" in s.get("tags", []) for s in valid):
            return _result(field, prepared, "L3", "J-06", "姓名含掩码符号，降级为待确认，不判不一致")
        detail = "；".join("%s=%s" % (s["material"], s.get("value")) for s in valid)
        return _result(field, prepared, "L2", "J-02" if len(valid) > 1 else "J-03",
                       "归一化后仍不等：%s" % detail)

    if missing:
        return _result(field, prepared, "L3", "J-06",
                       "单边为空：%s 未读出" % "、".join(s["material"] for s in missing))
    code = "J-01" if len(valid) == 1 else "J-02"
    return _result(field, prepared, "L4", code, "归一化后一致")


def _result(field, prepared, level, judge_code, detail):
    return {
        "key": field["key"],
        "name": field.get("name") or field["key"],
        "system_group": field.get("system_group"),
        "required": bool(field.get("required")),
        "level": level,
        "level_name": LEVEL_NAME.get(level, level),
        "judge": judge_code,
        "normalize": field.get("normalize") or [],
        "detail": detail,
        "sources": prepared,
        # 规则表里**声明**的来源材料（不论本轮是否真的提供了值）。
        # 渲染核对表要靠它确定「这一节应该有哪几列」：只用 sources 的话，
        # 某份材料本轮缺失时列会凭空消失，逐字段表格的列数与表头就对不上了。
        "declared_sources": field.get("sources") or [],
    }


# ------------------------------------------------------------------ 前置校验

def precheck_entity(materials):
    """P-01 主体一致性：以统一社会信用代码为主键，找出非本户材料。

    只比机构类材料（营业执照 / 工商公示 / 三方存管协议 / 两张申请表）。自然人证件（M-CUST-03）
    的证件号是个人身份号码、系统录入截图（M-SYS-*）是基准方，都不参与机构主体比对
    （自然人由 P-05 做同人归一）。

    三条刻意的收敛，都是真样例逼出来的：
    1. **主键必须先过格式校验**。真机上把 `或注册号`、`及有效期` 这类标签残渣当成信用代码
       投了票，「主材料组」落到了三方存管协议上，营业执照与两张申请表全被判成非本户 ——
       39 个字段的来源被整份排除，逐字段比对直接瘫掉。
    2. **只差 1~2 个字符视为同一主体的录入/识别误差**，只出字段级不一致（L2），不判非本户。
       营业执照与系统录入只差末位一个字符时判「非本户」明显过头。
    3. **有效主键不足 2 份就跳过 P-01**，宁可不下结论，也不拿一份材料去投「多数票」。

    排除只作用于**字段来源**，材料本身仍进清单、仍做签章与完备性检查。
    """
    entity_types = {"M-CUST-01", "M-CUST-02", "M-CUST-04", "M-CUST-05", "M-CUST-06"}
    issues = []
    groups = {}
    for code, mat in sorted(materials.items()):
        if mat.get("material_type") not in entity_types:
            continue
        key = _subject_key(mat)
        if key:
            groups.setdefault(key, []).append(code)
    if len(groups) < 2:
        return issues
    main = max(groups.items(), key=lambda kv: (len(kv[1]), kv[0]))[0]
    for key, codes in groups.items():
        if key == main or _key_near(key, main):
            continue
        for code in codes:
            issues.append({
                "type": "P-01", "level": "L1", "material": code,
                "detail": "统一社会信用代码 %s 与主材料组（%s）不一致，疑似非本户材料，"
                          "不参与逐字段比对" % (key, main),
            })
    # 名称兜底：没有有效主键、但材料自带公司名称（印章图、协议甲方）的，按名称判非本户。
    # 只在这两份名称都带公司后缀时下结论 —— `机构开户审核` 这种录入名不是公司名，
    # 拿它当判据会把本户材料判成非本户。
    main_codes = sorted(groups.get(main, []))
    ref_name = next((nm for nm in (_org_name(materials[c]) for c in main_codes) if nm), None)
    if ref_name:
        for code, mat in sorted(materials.items()):
            if code in {c for codes in groups.values() for c in codes}:
                continue
            name = _org_name(mat)
            if name and name != ref_name:
                issues.append({
                    "type": "P-01", "level": "L1", "material": code,
                    "detail": "机构名称「%s」与主材料组（%s）不符，疑似非本户材料，不参与逐字段比对"
                              % (name, ref_name),
                })
    return issues


# 统一社会信用代码字符集（不含 I / O / S / V / Z）
_USCC_CHARS = re.compile(r"[0-9A-HJ-NPQRTUWXY]{18}")
_ID_BIRTH = re.compile(r"^(19|20)\d{2}(0[1-9]|1[0-2])(0[1-9]|[12]\d|3[01])$")
_ORG_SUFFIX = ("有限公司", "股份有限公司", "事务所", "合伙企业", "集团", "银行", "证券", "基金")


def _extract_uscc(value):
    """从一段文本里取出统一社会信用代码；取不到返回 None。

    身份证号也是 18 位数字，但它第 7~14 位是出生日期 —— 用这条把它排除掉，
    否则两张申请表里的自然人证件号会被当成机构主体键投进 P-01。
    """
    s = re.sub(r"[^0-9A-Za-z]", "", nz.clean(value) or "").upper()
    for m in _USCC_CHARS.finditer(s):
        token = m.group(0)
        if _ID_BIRTH.match(token[6:14]):
            continue
        return token
    return None


def _subject_key(mat):
    """材料里的机构主体键：优先统一社会信用代码，其次「证件类别=营业执照」的证件编号。"""
    cc = _pick(mat, ("credit_code",))
    if cc:
        key = _extract_uscc(cc)
        if key:
            return key
    ct = nz.clean(_pick(mat, ("cert_type",)) or "")
    if "营业执照" in ct or "信用代码" in ct:
        return _extract_uscc(_pick(mat, ("cert_no",)) or "")
    return None


def _key_near(a, b, tolerance=2):
    """两个主体键是否只差少量字符（同一主体的录入/识别误差）。"""
    if not a or not b or len(a) != len(b):
        return False
    return sum(1 for x, y in zip(a, b) if x != y) <= tolerance


def _org_name(mat):
    """材料自带的机构名称（只在带公司后缀时返回，避免把录入名当公司名）。"""
    for item in mat.get("fields") or []:
        if item.get("key") not in ("enterprise_name", "name", "institution_name",
                                   "full_name", "seal_text", "party_a"):
            continue
        value = nz.clean(item.get("value"))
        if value and any(suffix in value for suffix in _ORG_SUFFIX):
            return value
    return None


def precheck_internal(materials):
    """P-02 材料内部自洽性：同一材料内同一字段出现多个不同取值。"""
    issues = []
    for code, mat in materials.items():
        seen = {}
        for item in mat.get("fields", []):
            key = item.get("key")
            val = nz.clean(item.get("value"))
            if not key or not val:
                continue
            if key in seen and seen[key] != val:
                issues.append({
                    "type": "P-02", "level": "L3", "material": code,
                    "detail": "字段 %s 在材料内前后不一致（%s / %s），材料质量异常" % (key, seen[key], val),
                })
            seen.setdefault(key, val)
    return issues


def precheck_versions(materials, material_types):
    """P-04 多版本收敛：同一材料类型多份时，只取已签章版本。"""
    issues = []
    by_type = {}
    for code, mat in materials.items():
        by_type.setdefault(mat.get("material_type"), []).append(code)
    for mtype, codes in by_type.items():
        if len(codes) < 2:
            continue
        signed = [c for c in codes if _is_signed(materials[c])]
        keep = signed[0] if signed else codes[0]
        issues.append({
            "type": "P-04", "level": "L3", "material": keep,
            "detail": "%s 存在 %d 个版本（%s），按 P-04 只取已签章版本 %s 参与比对"
                      % (mtype, len(codes), "、".join(codes), keep),
        })
    return issues


def precheck_completeness(materials, material_types):
    """P-03 完备性：必备材料是否齐备，签章是否完整。"""
    issues = []
    present_types = {m.get("material_type") for m in materials.values()}
    for item in material_types.get("customer_side", []):
        if item.get("required") is True and item["code"] not in present_types:
            issues.append({"type": "P-03", "level": "L3", "material": item["code"],
                           "detail": "必备材料缺失：%s" % item["name"]})
    for code, mat in materials.items():
        if mat.get("material_type") != "M-CUST-06":
            continue
        for key, label in (("company_stamp_text", "公章"), ("customer_signature", "客户签字"),
                           ("operator_signature", "经办人签字")):
            if not _pick(mat, (key,)):
                issues.append({"type": "P-03", "level": "L3", "material": code,
                               "detail": "必备签章缺失：%s" % label})
    return issues


def precheck_same_person(findings, materials):
    """P-05 同人多角色归一：证件编号相同的角色共用一次证件类结论。"""
    cert_values = {}
    for mat in materials.values():
        for item in mat.get("fields", []):
            if item.get("key") in ("cert_no", "contact_cert_no", "operator_cert_no",
                                   "legal_rep_cert_no", "controller_cert_no") and item.get("value"):
                cert_values.setdefault(nz.n_number(item["value"]), []).append(item["key"])
    notes = []
    for cert, keys in cert_values.items():
        if len(keys) > 1:
            notes.append("证件编号 %s 在多个角色中重复出现（%s），按 P-05 视为同一自然人，证件类字段共用一次结论"
                         % (cert[-6:], "、".join(sorted(set(keys)))))
    return notes


def _pick(mat, keys):
    for item in mat.get("fields", []):
        if item.get("key") in keys and nz.empty_state(item.get("value")) == "value":
            return item["value"]
    return None


# 签章字段的判据：显式清单（防止命名不含 hint 的字段漏判）+ 命名 hint（覆盖后续新增）。
# 不含裸 "sign"：那会把 design 之类的字段误判成签章件。
_SEAL_PATHS = {
    "company_stamp_text", "party_a_seal", "applicant_signature", "seal", "agency_seal",
    "seal_text", "business_stamp", "operator_signature", "institution_seal",
}
_SEAL_HINTS = ("seal", "stamp", "signature")


def seal_paths_from_rules(fields):
    """从规则表补充签章字段路径：判定编号 J-07 的字段所引用的材料内路径。"""
    out = set()
    for field in fields or []:
        if field.get("judge") != "J-07":
            continue
        for src in field.get("sources") or []:
            if (src or {}).get("path"):
                out.add(src["path"])
    return out


def is_seal_key(key, seal_keys=None):
    if not key:
        return False
    if seal_keys and key in seal_keys:
        return True
    if key in _SEAL_PATHS:
        return True
    low = str(key).lower()
    return any(hint in low for hint in _SEAL_HINTS)


def is_signed(mat, seal_keys=None):
    """这份材料是否带签章（P-04 多版本收敛的取舍依据）。

    签章字段名不止一种写法：开户合同签字页是 `business_stamp` / `operator_signature`，
    印章图是 `seal_text`，合同是 `party_a_seal`。只认一份硬编码清单会把「已签章版本」
    判成未签章，多版本收敛就会淘汰错的那一份 —— 这是拿真样例跑出来的。
    """
    for item in mat.get("fields", []):
        if is_seal_key(item.get("key"), seal_keys) and nz.empty_state(item.get("value")) == "value":
            return True
    return False


# 兼容旧调用名
_is_signed = is_signed
