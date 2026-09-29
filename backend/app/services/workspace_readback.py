"""dsh 没给出最终答复时，读回它在工作区留下的产物当答复。

为什么需要：dsh 是一次性 CLI 调用，最终答复只从 stdout 出来。模型把预算花在工具调用上、
或者进程被终止时，stdout 可能是空的 —— 执行会被判失败、用户看到空气泡，而它在
`/workspace` 里其实已经写出了核对表。核对表是**产物**不是过程，读回来给用户，
比报一句「没有内容」有用得多。

三条约束（都是为了不把旧东西、过程文件当成结论）：

1. **只读文本类产物**（`.md` / `.txt` / `.csv`），不读 `json` 中间件 —— `findings.json`
   是工具之间的交换格式，直接贴给业务同事看没有意义，还会把整份规则表带出来。
2. **只读本轮动过的文件**（按 mtime 过滤），避免把上一轮留下的旧表当成本轮结论。
3. **不读平台自己写的东西**（`skills/` 投放的技能包、`materials/` 物化的材料），
   它们不是模型产物。

读回来的内容在答复里明确标注「本轮没有给出最终答复」与文件来源，用户能一眼看出这是兜底。
"""

from __future__ import annotations

import time
from pathlib import Path

from . import runtime_manager

# 只看这些后缀：核对表是 Markdown，导出数据是 csv/txt；json 是过程文件
READABLE_SUFFIXES = (".md", ".markdown", ".txt", ".csv")

# 平台自己写的目录，不是模型产物
PLATFORM_DIRS = ("skills", "materials", "parked", ".dwp")

# 单份产物最多取这么多字符，以及整份兜底答复的总上限
MAX_FILE_CHARS = 6000
MAX_TOTAL_CHARS = 18000
MAX_FILES = 5

# mtime 早于「本轮开始」超过这么多秒就不算本轮产物（容器与宿主时钟可能有偏差）
_MTIME_SLACK_SECONDS = 5


def workspace_dir(employee_id: str) -> Path:
    """容器内 ``/workspace`` 对应的宿主/backend 侧目录。"""
    return Path(runtime_manager.workspace_ref(employee_id)) / "workspace"


def collect(
    employee_id: str,
    *,
    since: float | None = None,
    max_files: int = MAX_FILES,
    max_file_chars: int = MAX_FILE_CHARS,
) -> list[dict]:
    """收集本轮模型在工作区写下的文本产物，按修改时间从新到旧。"""
    root = workspace_dir(employee_id)
    if not root.is_dir():
        return []
    cutoff = (since - _MTIME_SLACK_SECONDS) if since else None
    found: list[dict] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(root)
        if any(part.startswith(".") for part in relative.parts):
            continue
        if relative.parts and relative.parts[0] in PLATFORM_DIRS:
            continue
        if path.suffix.lower() not in READABLE_SUFFIXES:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if cutoff is not None and stat.st_mtime < cutoff:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        if not text:
            continue
        truncated = len(text) > max_file_chars
        found.append({
            "name": relative.as_posix(),
            "text": text[:max_file_chars],
            "truncated": truncated,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
        })
    found.sort(key=lambda item: item["mtime"], reverse=True)
    return found[:max_files]


def render(employee_id: str, *, since: float | None = None,
           reason: str = "") -> str:
    """把工作区产物拼成一份兜底答复。没有产物时返回空串（调用方据此判失败）。"""
    return render_artifacts(collect(employee_id, since=since), reason=reason)


def render_artifacts(artifacts: list[dict], *, reason: str = "") -> str:
    """按已收集到的产物拼答复。分成两步是为了让调用方只读一次工作区。"""
    if not artifacts:
        return ""

    parts = [
        "本轮AI员工没有返回最终答复"
        + ("（%s）" % reason if reason else "")
        + "，以下是它在工作区里已经产出的文件内容，供你直接查看：",
    ]
    for index, item in enumerate(artifacts, start=1):
        parts.append("\n---\n### %d. %s%s\n\n%s" % (
            index, item["name"],
            "（内容过长，已截断）" if item["truncated"] else "",
            item["text"],
        ))
    parts.append(
        "\n---\n以上内容由AI员工在执行过程中直接写入工作区，平台原文读回、未做改写；"
        "如果结论不完整，可以再发一条消息让它继续。"
    )
    text = "\n".join(parts)
    if len(text) > MAX_TOTAL_CHARS:
        text = text[:MAX_TOTAL_CHARS].rstrip() + "\n\n（内容过长，已截断）"
    return text


def summarize(artifacts: list[dict]) -> str:
    """给执行轨迹用的一句话说明。"""
    if not artifacts:
        return "工作区没有可读回的产物"
    names = "、".join(item["name"] for item in artifacts[:3])
    more = "等 %d 份" % len(artifacts) if len(artifacts) > 3 else ""
    return "已读回 %s%s" % (names, more)


def run_started_at() -> float:
    """调用方在发起 dsh 之前取一次，作为「本轮产物」的时间下界。"""
    return time.time()
