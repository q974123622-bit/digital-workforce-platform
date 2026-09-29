"""上传即转写：图片与扫描页在后台转成文本并缓存，模型只读文本。

为什么放在上传阶段：单轮执行有 900/1200 秒的硬上限，读图（每次一次工具往返 +
一次视觉调用）会把预算吃掉一大半。上传是异步的、用户本来就在等，把「图 → 文本」
放在这里，执行阶段只剩「读文本 + 跑脚本 + 规划」。

三条口径：
1. **文本是事实层**：转写结果落 `AttachmentChunk`，同时旁挂一份
   `<原文件>.transcript.txt` 供人工核对与材料物化；抽取阶段的取值必须能在转写文本里原文命中。
2. **并发有上限**：网关实测 16 路全 200、24 路 502（不是 429），所以全局信号量
   默认压到 8，宁可慢一点也不要把整批打挂。单请求可带多图（默认 4 张一批）。
3. **失败只降级不静默**：视觉不可用时抛 `OCR_REQUIRED`，由附件状态机落
   `ocr_required` 并给出人话提示，绝不用空内容冒充成功。
"""

from __future__ import annotations

import base64
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import config
from .llm import DeepSeekProvider, LLMUnavailableError

logger = logging.getLogger(__name__)

IMAGE_TYPES = {"png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"}
DEFAULT_VISION_MODEL = "ascend-qwen3.6-27b"   # 平台实测只有它能真读图

TRANSCRIBE_PROMPT = """你是单据转写引擎。把图片里所有可见文字逐字转写出来。
规则：
1. 不要总结、不要翻译、不要补全、不要解释，只转写。
2. 按原始排版从上到下、从左到右逐行输出。
3. 被涂黑、被印章覆盖、被水印压住而无法辨认的字符，每个看不清的字符写一个 * 号。
4. 整块完全无法辨认时不要编内容，输出一行 [[UNREADABLE]] 加一句区域描述。
5. 手写内容按最佳判读转写，不确定的单个字符用 ? 代替。
6. 表格按行转写，同一行的单元格用 | 分隔，并保持「字段名 | 值」的原始顺序。
只输出 JSON，不要任何多余文字：
{"lines":[{"text":"..."}],"unreadable_regions":[{"desc":"..."}]}"""

_semaphore: threading.Semaphore | None = None
_semaphore_lock = threading.Lock()


def _max_inflight() -> int:
    try:
        value = int(config.get("DWP_VISION_MAX_INFLIGHT", "8") or "8")
    except (TypeError, ValueError):
        value = 8
    return max(1, min(value, 16))       # 硬上限 16：网关 24 路就 502


def _batch_size() -> int:
    try:
        value = int(config.get("DWP_VISION_BATCH", "4") or "4")
    except (TypeError, ValueError):
        value = 4
    return max(1, min(value, 8))


def _retry_attempts() -> int:
    """视觉失败后的重试次数（不含首次），默认 1。

    单张图卡住的最坏路径 = (重试 + 1) × 单请求超时 + 退避。早先写死 2 次重试 +
    180 秒超时，最坏 ≈ 9 分钟，一张图就能把整轮预算吃穿；收紧到默认 1 次后最坏
    约 4 分钟，且超时本身也已可配（``DWP_VISION_TIMEOUT_SECONDS``）。
    """
    try:
        value = int(config.get("DWP_VISION_RETRY", "1") or "1")
    except (TypeError, ValueError):
        value = 1
    return max(0, min(value, 5))


def _backoff_seconds() -> float:
    """重试前的退避基数（秒），第 n 次重试等 ``base × n``。"""
    try:
        value = float(config.get("DWP_VISION_BACKOFF_SECONDS", "1.0") or "1.0")
    except (TypeError, ValueError):
        value = 1.0
    return max(0.0, min(value, 30.0))


def _vision_timeout() -> float:
    """单次视觉请求超时（秒），默认 120。

    单请求实测可到 79 秒，所以不能压得太低；但也必须比早先写死的 180 秒更早失败，
    否则最坏路径会把「上传即转写」拖成分钟级。
    """
    try:
        value = float(config.get("DWP_VISION_TIMEOUT_SECONDS", "120") or "120")
    except (TypeError, ValueError):
        value = 120.0
    return max(10.0, min(value, 300.0))


def vision_semaphore() -> threading.Semaphore:
    """全局在飞上限。附件是并发处理的，只有全局信号量才兜得住总量。"""
    global _semaphore
    if _semaphore is None:
        with _semaphore_lock:
            if _semaphore is None:
                _semaphore = threading.Semaphore(_max_inflight())
    return _semaphore


def vision_enabled() -> bool:
    if config.get("DWP_ATTACHMENT_VISION_ENABLED", "1") not in ("1", "true", "True", "yes"):
        return False
    return bool(config.get("DEEPSEEK_API_KEY"))


def _data_url(path: Path) -> str:
    mime = {
        ".png": "image/png", ".jpg": "image/jpeg", ".jpeg": "image/jpeg",
        ".webp": "image/webp", ".bmp": "image/bmp", ".gif": "image/gif",
        ".tif": "image/tiff", ".tiff": "image/tiff",
    }.get(path.suffix.lower(), "image/png")
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return "data:%s;base64,%s" % (mime, encoded)


def _parse_lines(raw: str) -> tuple[list[str], list[str]]:
    """从模型输出里取转写行与不可读区域，JSON 解析失败就退化成逐行文本。"""
    import json
    import re

    text = (raw or "").strip()
    if not text:
        return [], []
    fence = re.search(r"```(?:json)?\s*(.+?)```", text, re.S)
    if fence:
        text = fence.group(1).strip()
    data = None
    try:
        data = json.loads(text)
    except Exception:  # noqa: BLE001 - 模型没给 JSON 时按纯文本兜底
        data = None
    lines: list[str] = []
    regions: list[str] = []
    if isinstance(data, dict):
        for item in data.get("lines") or []:
            value = item.get("text") if isinstance(item, dict) else item
            if str(value or "").strip():
                lines.append(str(value).strip())
        for item in data.get("unreadable_regions") or []:
            desc = item.get("desc") if isinstance(item, dict) else item
            if str(desc or "").strip():
                regions.append(str(desc).strip())
    elif isinstance(data, list):
        lines = [str(x).strip() for x in data if str(x or "").strip()]
    if not lines and not regions:
        lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines, regions


def _transcribe_batch(paths: list[Path], *, employee_id: str = "") -> tuple[list[str], list[str]]:
    """一批图一次请求（网关支持单请求多图）。"""
    content: list[dict] = [{"type": "text", "text": TRANSCRIBE_PROMPT}]
    for path in paths:
        content.append({"type": "image_url", "image_url": {"url": _data_url(path)}})
    messages = [{"role": "user", "content": content, "source": "demo"}]
    model = config.get("DWP_VISION_MODEL", DEFAULT_VISION_MODEL) or DEFAULT_VISION_MODEL
    provider = DeepSeekProvider()
    retries = _retry_attempts()
    timeout = _vision_timeout()
    last_error: Exception | None = None
    for attempt in range(retries + 1):
        try:
            with vision_semaphore():
                resp = provider.vision(messages, model=model, timeout=timeout)
            return _parse_lines(resp.content)
        except (LLMUnavailableError, OSError) as exc:  # noqa: BLE001
            last_error = exc
            if attempt < retries:
                time.sleep(_backoff_seconds() * (attempt + 1))
    logger.warning("附件视觉转写失败（%d 张图）：%s", len(paths), last_error)
    raise LLMUnavailableError("视觉转写失败：%s" % last_error)


def transcribe_images(paths: list[Path], *, employee_id: str = "") -> tuple[list[str], list[str]]:
    """逐批转写图片，返回 (转写行, 不可读区域描述)。批次内并行，全局在飞受信号量约束。"""
    paths = [Path(p) for p in paths]
    if not paths:
        return [], []
    batch = _batch_size()
    batches = [paths[i:i + batch] for i in range(0, len(paths), batch)]
    lines: list[str] = []
    regions: list[str] = []
    if len(batches) == 1:
        got_lines, got_regions = _transcribe_batch(batches[0], employee_id=employee_id)
        return got_lines, got_regions
    with ThreadPoolExecutor(max_workers=min(len(batches), _max_inflight())) as pool:
        for got_lines, got_regions in pool.map(
                lambda b: _transcribe_batch(b, employee_id=employee_id), batches):
            lines.extend(got_lines)
            regions.extend(got_regions)
    return lines, regions


def transcript_text(lines: list[str], regions: list[str] | None = None) -> str:
    body = "\n".join(lines)
    if regions:
        body += "\n" + "\n".join("[[UNREADABLE]] %s" % item for item in regions)
    return body


def write_transcript_file(source: Path, text: str) -> Path:
    """旁挂一份转写文本，供人工核对与材料物化。"""
    target = source.with_suffix(source.suffix + ".transcript.txt")
    target.write_text(text, encoding="utf-8")
    return target


def transcribe_pages(page_paths: list[Path], *, employee_id: str = "") -> tuple[list[str], list[str]]:
    if not vision_enabled():
        raise RuntimeError("OCR_REQUIRED")
    return transcribe_images(page_paths, employee_id=employee_id)


__all__ = [
    "IMAGE_TYPES", "TRANSCRIBE_PROMPT", "transcribe_images", "transcribe_pages",
    "transcript_text", "write_transcript_file", "vision_enabled", "vision_semaphore",
]
