"""GFM → 企业微信机器人 markdown 子集的降级转换（纯函数，无副作用）。

背景：平台会话侧已放开让模型输出标准 GFM（表格 + 围栏代码块），但企业微信机器人的
markdown **不支持表格与围栏代码块**，原样透传会让用户在企微里看到裸的 ``|`` 与 ``` 围栏，
比纯文本更糟。这里把模型输出降级成企微能正确渲染的子集：

- 围栏代码块：去掉 fence，整体包成 ``>`` 引用块（企微支持引用），保留语言标注；
- 表格：转成「· 列名：值；列名：值」逐行，丢弃 ``|---|---|`` 分隔行；
- ATX 标题：去掉 ``#`` 标记，保留文字；
- 图片：``![alt](url)`` → ``[图片：alt](url)``（企微不会渲染图片 markdown）。

无结构化内容时**原样返回**（幂等）：转换结果里已不存在围栏与表格，再次转换不会二次改写。
"""

from __future__ import annotations

import re

# ATX 标题：允许前后各至多 3 个空格缩进，结尾可带闭合的 # 序列。
_HEADING_RE = re.compile(r"^ {0,3}(#{1,6})[ \t]+(.*?)[ \t]*#*[ \t]*$")
# 图片：![alt](url)
_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
# 转义竖线占位符（避免 split 时被当作列分隔符）
_ESCAPED_PIPE = "\x00PIPE\x00"


def _fence_info(line: str) -> str | None:
    """返回围栏行的语言标注（无标注返回空串）；不是围栏行返回 None。"""
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None
    for marker in ("```", "~~~"):
        if stripped.startswith(marker):
            info = stripped[len(marker):].strip()
            # 信息串里不允许再出现反引号（GFM 规定），否则视为普通文本行
            if marker == "```" and "`" in info:
                return None
            return info.split()[0] if info else ""
    return None


def _is_delimiter_row(line: str) -> bool:
    """判断是否为 GFM 表格分隔行，如 ``| --- | :--: |``。"""
    stripped = line.strip()
    if not stripped or "|" not in stripped or "-" not in stripped:
        return False
    return all(ch in "|-: \t" for ch in stripped)


def _split_row(line: str) -> list[str]:
    """按未转义的 ``|`` 切分表格行，去掉首尾空单元。"""
    text = line.strip().replace("\\|", _ESCAPED_PIPE)
    if text.startswith("|"):
        text = text[1:]
    if text.endswith("|"):
        text = text[:-1]
    cells = [cell.replace(_ESCAPED_PIPE, "|").strip() for cell in text.split("|")]
    return cells


def _render_code_block(lang: str, body: list[str]) -> list[str]:
    """围栏代码块 → 企微引用块（去 fence，保留语言标注与内部空行）。"""
    if not body and not lang:
        return []
    out: list[str] = []
    if lang:
        out.append(f"> 代码（{lang}）")
    for raw in body:
        out.append(f"> {raw}".rstrip())
    return out


def _render_table(header: list[str], rows: list[list[str]]) -> list[str]:
    """表格 → 「· 列名：值；列名：值」逐行（完全不用竖线，避免裸符号观感）。"""
    if not rows:
        labels = [item for item in header if item]
        return ["· " + "、".join(labels)] if labels else []
    out: list[str] = []
    for row in rows:
        parts: list[str] = []
        for index, value in enumerate(row):
            if not value:
                continue
            label = header[index] if index < len(header) else ""
            parts.append(f"{label}：{value}" if label else value)
        if parts:
            out.append("· " + "；".join(parts))
    return out


def _convert_line(line: str) -> str:
    """单行降级：标题去 ``#``、图片转链接；其余保持原样。"""
    heading = _HEADING_RE.match(line)
    if heading:
        line = heading.group(2).strip()
    return _IMAGE_RE.sub(lambda m: f"[图片：{m.group(1)}]({m.group(2)})" if m.group(1)
                         else f"[图片]({m.group(2)})", line)


def to_wecom_markdown(text: str) -> str:
    """把模型输出的 GFM 文本降级为企业微信机器人可渲染的 markdown。"""
    if not text or not text.strip():
        return text

    lines = text.split("\n")
    total = len(lines)
    out: list[str] = []
    index = 0
    while index < total:
        line = lines[index]

        info = _fence_info(line)
        if info is not None:
            index += 1
            body: list[str] = []
            while index < total and _fence_info(lines[index]) is None:
                body.append(lines[index])
                index += 1
            # 未闭合围栏：把剩余内容全部当作代码体，末尾不吞行。
            if index < total:
                index += 1
            out.extend(_render_code_block(info, body))
            continue

        header = _split_row(line) if "|" in line else None
        if (header is not None and index + 1 < total
                and _is_delimiter_row(lines[index + 1])):
            index += 2
            rows: list[list[str]] = []
            while (index < total and lines[index].strip() and "|" in lines[index]
                   and _fence_info(lines[index]) is None
                   and not _is_delimiter_row(lines[index])):
                rows.append(_split_row(lines[index]))
                index += 1
            out.extend(_render_table(header, rows))
            continue

        out.append(_convert_line(line))
        index += 1

    return "\n".join(out)
