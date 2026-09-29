"""N-01 ~ N-12 归一化规则实现。纯 stdlib，不依赖第三方库。

每个函数对应 rules.json 里的一个编号，改动规则时同步改这里与 rules.json。
"""

from __future__ import annotations

import json
import re
from pathlib import Path

RULES_DIR = Path(__file__).resolve().parent.parent / "rules"

_FULL2HALF = {ord(c): ord(c) - 0xFEE0 for c in
              "０１２３４５６７８９ＡＢＣＤＥＦＧＨＩＪＫＬＭＮＯＰＱＲＳＴＵＶＷＸＹＺ"
              "ａｂｃｄｅｆｇｈｉｊｋｌｍｎｏｐｑｒｓｔｕｖｗｘｙｚ"}
_FULL2HALF[ord("：")] = ord(":")
_FULL2HALF[ord("（")] = ord("(")
_FULL2HALF[ord("）")] = ord(")")
_FULL2HALF[ord("　")] = ord(" ")

_ZW = re.compile(r"[\u200b-\u200f\ufeff]")
# 转写会在数字与单位之间插空格（`1989 年 05 月 29 日`），分隔符两侧都要容忍空格
_DATE_TOKEN = re.compile(r"(\d{4})\s*[-./年]?\s*(\d{1,2})\s*[-./月]?\s*(\d{1,2})")
_YM_TOKEN = re.compile(r"^(\d{4})(\d{2})$")

_CN_DIGITS = {"零": 0, "〇": 0, "一": 1, "壹": 1, "二": 2, "贰": 2, "两": 2, "三": 3, "叁": 3,
              "四": 4, "肆": 4, "五": 5, "伍": 5, "六": 6, "陆": 6, "七": 7, "柒": 7,
              "八": 8, "捌": 8, "九": 9, "玖": 9}


def load_synonyms(path=None):
    with open(path or (RULES_DIR / "synonyms.json"), "r", encoding="utf-8") as fh:
        return json.load(fh)


def clean(v):
    """全角转半角、去零宽字符、去首尾空白（N-08 的基础动作）。"""
    if v is None:
        return ""
    s = str(v).translate(_FULL2HALF)
    s = _ZW.sub("", s)
    return s.strip()


def empty_state(v):
    """N-06 空值三态：blank / dash / na / value。

    判据与 `rules/synonyms.json` 的 `empty_states` 保持一致。**「无」不算空值** ——
    它是「不良诚信记录」「是否存在上一层备案主体」这类栏位的正常取值，判成空值会让
    「系统=无 / 申请表=☑无」落成待确认（真机上就是这么丢掉一条本该判一致的结论）。
    """
    s = clean(v)
    if s == "":
        return "blank"
    if s in {"-", "--", "—", "/", "无数据"}:
        return "dash"
    if s.upper() in {"N/A", "NA", "不适用", "不涉及"}:
        return "na"
    return "value"


# ------------------------------------------------------------------ 单项归一

def n_text(v):
    """N-08 文本清洗。"""
    s = clean(v)
    s = re.sub(r"[\s]+", "", s)
    s = s.replace("，", ",").replace("、", ",").replace("；", ";").replace("：", ":")
    return s.upper()


def n_date(v, long_terms=("30001231", "3000年12月31日", "长期", "长期有效", "无固定期限")):
    """N-01 日期归一 + N-02 长期有效归一。支持单日期与日期区间（起~止）。"""
    s = clean(v)
    if not s:
        return ""
    for token in long_terms:
        s = s.replace(token, "LONG_TERM")
    found = []
    for m in _DATE_TOKEN.finditer(s):
        y, mo, d = m.group(1), int(m.group(2)), int(m.group(3))
        if not (1 <= mo <= 12 and 1 <= d <= 31):
            continue
        found.append("%s-%02d-%02d" % (y, mo, d))
    if not found:
        m = _YM_TOKEN.match(s)
        if m:
            return "%s-%s" % (m.group(1), m.group(2))
        return s
    if len(found) >= 2:
        return "%s..%s" % (found[0], found[-1])
    return found[0]


def cn_num(s):
    """中文数字转阿拉伯数字（支持万 / 亿分段）。"""
    total = section = number = 0
    for ch in str(s):
        if ch in _CN_DIGITS:
            number = _CN_DIGITS[ch]
        elif ch in ("十", "拾"):
            section += (number or 1) * 10
            number = 0
        elif ch in ("百", "佰"):
            section += (number or 1) * 100
            number = 0
        elif ch in ("千", "仟"):
            section += (number or 1) * 1000
            number = 0
        elif ch in ("万", "萬"):
            total += (section + number) * 10000
            section = number = 0
        elif ch == "亿":
            total = (total + section + number) * 100000000
            section = number = 0
    return total + section + number


def n_money(v):
    """N-03 金额归一：统一到万元，保留 2 位小数。"""
    s = clean(v)
    if not s:
        return ""
    t = re.sub(r"[人民币￥¥,，\s]", "", s)
    # 含阿拉伯数字时走数字分支（`1000万` 这类混写不能被中文数字解析器吃掉）
    if re.search(r"[0-9]", t):
        m = re.search(r"([0-9]+(?:\.[0-9]+)?)", t)
        if not m:
            return t
        val = float(m.group(1))
        if "亿" in t:
            val *= 10000.0
        elif "万" in t or "萬" in t:
            pass
        elif "元" in t or "圆" in t:
            val /= 10000.0
        elif val >= 100000:
            val /= 10000.0
        return "%.2f" % val
    if not re.search(r"[零一二三四五六七八九十百千万亿壹贰叁肆伍陆柒捌玖拾佰仟萬]", t):
        return t
    has_wan = ("万" in t) or ("萬" in t)
    val = float(cn_num(t))
    if not val:
        return t
    return "%.2f" % (val / 10000.0 if has_wan else val)


def n_address(v):
    """N-05 地址归一。行政层级前缀省略由 J-05 处理，这里只做清洗与层级统一。"""
    s = clean(v)
    s = re.sub(r"[\s]+", "", s)
    s = s.replace("省省", "省").replace("市市", "市")
    return s


def n_name(v):
    """N-09 姓名归一，返回 (归一化值, 是否含掩码)。"""
    s = clean(v)
    s = re.sub(r"[\s]+", "", s)
    masked = bool(re.search(r"[*＊×xX]{1,}|●|○", s))
    return s, masked


def n_percent(v):
    """N-10 百分比归一。"""
    s = clean(v)
    m = re.search(r"([0-9]+(?:\.[0-9]+)?)\s*%?", s)
    if not m:
        return s
    val = float(m.group(1))
    if "%" not in s and val <= 1:
        val *= 100
    return "%.2f%%" % val


def n_number(v):
    """N-11 号码归一：去空格与分隔符，去国际前缀。"""
    s = clean(v)
    s = re.sub(r"^(\+86|0086|86)", "", s)
    s = re.sub(r"[^0-9]", "", s)
    return s


def n_enum(v):
    """N-12 枚举括号内容：只留主体枚举，括号内说明另存。"""
    s = clean(v)
    main = re.sub(r"[（(][^）)]*[）)]", "", s)
    return main.strip() or s


def group_of(value, groups):
    """在同义词组表里找 value 所属的组；找不到返回 None。"""
    target = n_text(value)
    for group in groups:
        for item in group:
            if n_text(item) == target:
                return group
    return None


def group_key(group):
    """把同义词组压成可比较、可哈希的规范串（组内任一同义写法都映射到同一串）。"""
    return "SYN::" + "|".join(sorted(n_text(x) for x in group))


def n_cert_type(v, syn):
    group = group_of(v, syn.get("cert_type", []))
    return group_key(group) if group else n_text(v)


def n_org_type(v, syn):
    group = group_of(v, syn.get("org_type", []))
    return group_key(group) if group else n_text(v)


def n_generic_enum(v, syn, key):
    group = group_of(v, syn.get(key, []))
    return group_key(group) if group else n_enum(v)


# ------------------------------------------------------------------ 调度

def normalize_value(codes, value, syn, field_key=""):
    """按字段配置的归一化编号列表依次处理，返回归一化结果与命中标记。"""
    result = clean(value)
    tags = []
    for code in codes or []:
        if code == "N-01":
            result = n_date(result)
        elif code == "N-02":
            result = n_date(result)
        elif code == "N-03":
            result = n_money(result)
        elif code == "N-04":
            result = n_cert_type(result, syn)
        elif code == "N-05":
            result = n_address(result)
        elif code == "N-07":
            result = n_org_type(result, syn)
        elif code == "N-08":
            result = n_text(result)
        elif code == "N-09":
            result, masked = n_name(result)
            if masked:
                tags.append("masked_name")
        elif code == "N-10":
            result = n_percent(result)
        elif code == "N-11":
            result = n_number(result)
        elif code == "N-12":
            result = n_enum(result)
    if not codes:
        result = n_text(result)
    return {"normalized": result, "tags": tags}
