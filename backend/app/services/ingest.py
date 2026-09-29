#!/usr/bin/env python3
"""唯一 ingest 实现：任意材料 → 统一的「文本行 + 待视觉转写的图像页」。

这份文件是 **单一真相**，三处共用，改一处必须同步另两处：
- 技能包 `scripts/ingest.py`（本文件，容器内路径 `/workspace/skills/<slug>/scripts/ingest.py`）
- 平台 `backend/app/services/ingest.py`（依赖齐全，逐字同源）
- 抽取评测 `07_抽取评测/runner/ingest.py`（薄壳，直接 import 本文件）

五条硬约定：
1. 先认文件头魔数，后缀只作兜底 —— 对齐平台 `attachments._validate_signature` 的防改名思路；
2. 未知类型显式报 UnsupportedInput，绝不静默按图片处理；
3. 文本类输入零模型成本：直读进转写文本，既不花一次调用，也不会引入 OCR 错字；
4. PDF 按页判文本层：有文本层的页直读、扫描页渲染成图走视觉 —— 支持电子页 + 扫描页混合件；
5. 图像一律先过自适应归一化：方向纠正、色彩统一、多帧展开、尺寸收敛，再交给视觉。

核心纯 stdlib；PIL / pdfplumber / python-docx / openpyxl 按需 import，
缺依赖时报可读错误并给出提示，不静默降级成错结果。
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path

KIND_TEXT = "text"
KIND_DOCX = "docx"
KIND_XLSX = "xlsx"
KIND_PDF = "pdf"
KIND_IMAGE = "image"

# 直读类：不调视觉模型，也不需要印章/视觉三态通道
DIRECT_KINDS = (KIND_TEXT, KIND_DOCX, KIND_XLSX)
# 证据闸门走严格口径（值必须落在单一行内）的类型：直读文本没有跨行折行的理由，
# 而 OCR 转写行可能被切块切断，所以图像与 PDF 仍走全局命中口径。
STRICT_GATE_KINDS = (KIND_TEXT, KIND_DOCX, KIND_XLSX)

IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
TEXT_SUFFIXES = {".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".xml",
                 ".html", ".htm", ".log", ".yaml", ".yml", ".ini", ".conf"}
DELIMITED_SUFFIXES = {".csv", ".tsv"}
# 多帧图（TIFF / GIF）展开的上限，防止一张几千帧的图把任务撑爆
MAX_IMAGE_FRAMES = 20

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF = (b"GIF87a", b"GIF89a")
_TIFF = (b"II*\x00", b"MM\x00*")
_OLE = b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"

# ---------------------------------------------------------------- 自适应参数
# 图像长边：太小的（手机截图裁块、证件小图）放大到 1600，超大的（4000+ 扫描件）压到 4000。
IMAGE_LONG_SIDE_MIN = 1200       # 长边低于此值认为偏小，需要放大
IMAGE_LONG_SIDE_TARGET = 1600    # 放大目标长边
IMAGE_LONG_SIDE_MAX = 4000       # 长边上限，超出按比例压回
IMAGE_MAX_UPSCALE = 4.0          # 放大倍数上限，避免糊图被撑成假细节

# PDF 扫描页渲染：按页面物理尺寸反推 DPI，使长边落到目标像素，再夹在上下限之间。
PDF_RENDER_TARGET_PX = 2600
PDF_DPI_MIN = 120
PDF_DPI_MAX = 600
PDF_MIN_CHARS = 20               # A4 页面的文本层阈值基准
A4_AREA_PT = 595.0 * 842.0       # 阈值按页面积缩放的分母
PDF_MIN_CHARS_FLOOR = 6          # 小页面的阈值下限


class UnsupportedInput(Exception):
    """无法识别的输入类型。调用方必须显式处理，不允许降级成图片。"""


def normalize_line(text):
    """转写行归一化：Tab 变成单元格分隔符 ` | `，连续空白压成一个空格。

    文本直读（txt/csv/xlsx）与图像 OCR 必须共用同一套行口径，否则抽取提示词里
    「表格单元格用 | 分隔」的约定对 Tab 分隔的文本导出不成立。
    """
    s = str(text).replace("\t", " | ")
    return re.sub(r"[ \u3000\u2002\u2003]+", " ", s).strip()


# ---------------------------------------------------------------- 类型探测

def _sniff_magic(head, path, size, data=None):
    """按文件头判类型。返回 None 表示魔数不认识（交由后缀/文本启发式兜底）。"""
    if head.startswith(b"%PDF-"):
        return KIND_PDF
    if head.startswith(_PNG) or head.startswith(_JPEG) \
            or head.startswith(_GIF) or head.startswith(_TIFF):
        return KIND_IMAGE
    if head[:2] == b"BM" and len(head) >= 14:
        # BMP 头 2-6 字节是文件长度；不校验的话任何以 BM 开头的文本都会被误判成图片
        if int.from_bytes(head[2:6], "little") == size:
            return KIND_IMAGE
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return KIND_IMAGE
    if head[:4] == b"PK\x03\x04":
        # OOXML 是 zip 容器，docx/xlsx/pptx 魔数相同，只能看包内结构
        try:
            if data is not None:
                with zipfile.ZipFile(io.BytesIO(data)) as zf:
                    names = zf.namelist()
            else:
                with zipfile.ZipFile(path) as zf:
                    names = zf.namelist()
        except Exception:  # noqa: BLE001 - 打不开的 zip 交给后缀兜底
            return None
        if any(n.startswith("word/") for n in names):
            return KIND_DOCX
        if any(n.startswith("xl/") for n in names):
            return KIND_XLSX
    return None


def _decode(raw):
    if raw.startswith(b"\xef\xbb\xbf"):
        return raw.decode("utf-8-sig"), "utf-8-bom"
    for enc in ("utf-8", "gb18030"):
        try:
            return raw.decode(enc), enc
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", "replace"), "utf-8-replace"


def _looks_like_text(path):
    raw = Path(path).open("rb").read(65536)
    return b"\x00" not in raw


def _looks_like_text_bytes(data):
    return b"\x00" not in data[:65536]


def detect_kind_bytes(data, filename=""):
    """按内存里的字节判类型（上传校验用，不落盘）。返回 (kind, detected_by)。"""
    size = len(data)
    head = data[:16]
    kind = _sniff_magic(head, None, size, data=data)
    if kind:
        return kind, "magic"
    suffix = Path(filename or "").suffix.lower()
    if head.startswith(_OLE):
        raise UnsupportedInput(
            "%s：检测到 OLE2 复合文档（老版 doc/xls/ppt），当前不支持，请另存为 docx/xlsx"
            % (filename or "文件"))
    if suffix in IMAGE_SUFFIXES:
        raise UnsupportedInput(
            "%s：后缀是图片但文件头不是已知图片格式（疑似改名或文件损坏）" % (filename or "文件"))
    if suffix in TEXT_SUFFIXES:
        return KIND_TEXT, "suffix"
    if _looks_like_text_bytes(data):
        return KIND_TEXT, "text-heuristic"
    raise UnsupportedInput(
        "%s：无法识别的文件类型（后缀 %s，文件头 %s），拒绝按图片处理"
        % (filename or "文件", suffix or "(无)", head[:8].hex()))


def detect_kind(path):
    """返回 (kind, detected_by)，detected_by ∈ {magic, suffix, text-heuristic}。"""
    path = Path(path)
    size = path.stat().st_size
    with path.open("rb") as fh:
        head = fh.read(16)
    kind = _sniff_magic(head, path, size)
    if kind:
        return kind, "magic"
    suffix = path.suffix.lower()
    if head.startswith(_OLE):
        raise UnsupportedInput(
            "%s：检测到 OLE2 复合文档（老版 doc/xls/ppt），当前不支持，请另存为 docx/xlsx"
            % path.name)
    if suffix in IMAGE_SUFFIXES:
        raise UnsupportedInput(
            "%s：后缀是图片但文件头不是已知图片格式（疑似改名或文件损坏）" % path.name)
    if suffix in TEXT_SUFFIXES:
        return KIND_TEXT, "suffix"
    if _looks_like_text(path):
        return KIND_TEXT, "text-heuristic"
    raise UnsupportedInput(
        "%s：无法识别的文件类型（后缀 %s，文件头 %s），拒绝按图片处理"
        % (path.name, suffix or "(无)", head[:8].hex()))


# ---------------------------------------------------------------- 文本类读取

def _split_lines(text):
    out = []
    for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = normalize_line(line)
        if line:
            out.append(line)
    return out


def read_text_lines(path):
    raw = Path(path).read_bytes()
    text, enc = _decode(raw)
    return _split_lines(text), enc


def read_delimited_lines(path):
    """csv/tsv 走 csv 模块解析，单元格统一用 ` | ` 连接 —— 与图像转写的表格口径一致。"""
    raw = Path(path).read_bytes()
    text, enc = _decode(raw)
    dialect = "excel-tab" if Path(path).suffix.lower() == ".tsv" else "excel"
    lines = []
    for row in csv.reader(io.StringIO(text), dialect):
        cells = [normalize_line(c) for c in row]
        if any(cells):
            lines.append(" | ".join(cells))
    return lines, enc


def read_docx_lines(path):
    """docx 直读：段落 + 表格单元格。零模型成本。"""
    try:
        import docx  # python-docx
    except ImportError as exc:  # noqa: BLE001
        raise UnsupportedInput(
            "%s：需要 python-docx 才能直读 docx（当前环境未安装）：%s" % (Path(path).name, exc))

    doc = docx.Document(str(path))
    lines = []
    for para in doc.paragraphs:
        if para.text.strip():
            lines.append(normalize_line(para.text))
    for table in doc.tables:
        for row in table.rows:
            cells = []
            for cell in row.cells:
                text = normalize_line(cell.text)
                if not cells or cells[-1] != text:
                    cells.append(text)
            if any(cells):
                lines.append(" | ".join(cells))
    return lines


def read_xlsx_lines(path):
    try:
        import openpyxl
    except ImportError as exc:  # noqa: BLE001
        raise UnsupportedInput(
            "%s：需要 openpyxl 才能直读 xlsx（当前环境未安装）：%s" % (Path(path).name, exc))

    wb = openpyxl.load_workbook(str(path), read_only=True, data_only=True)
    lines = []
    try:
        for ws in wb.worksheets:
            lines.append("[[工作表]] %s" % ws.title)
            for row in ws.iter_rows(values_only=True):
                cells = ["" if c is None else normalize_line(c) for c in row]
                if any(cells):
                    lines.append(" | ".join(cells))
    finally:
        wb.close()
    return lines


# ---------------------------------------------------------------- 图像归一化

def pil_modules():
    """按需加载 PIL 并打开容错开关。缺 Pillow 时报可读错误。"""
    try:
        from PIL import Image, ImageFile, ImageOps
    except ImportError as exc:  # noqa: BLE001
        raise UnsupportedInput("需要 Pillow 才能处理图像（当前环境未安装）：%s" % exc)
    # 扫描件常被截断，容错读取比整份失败更符合业务预期
    ImageFile.LOAD_TRUNCATED_IMAGES = True
    Image.MAX_IMAGE_PIXELS = None
    return Image, ImageFile, ImageOps


def adaptive_scale(width, height):
    """按长边决定缩放倍数：<1200 放大到 1600（上限 4x）；>4000 压到 4000；其余原样。"""
    long_side = max(int(width), int(height), 1)
    if long_side < IMAGE_LONG_SIDE_MIN:
        return min(IMAGE_MAX_UPSCALE, float(IMAGE_LONG_SIDE_TARGET) / long_side)
    if long_side > IMAGE_LONG_SIDE_MAX:
        return float(IMAGE_LONG_SIDE_MAX) / long_side
    return 1.0


def _flatten_to_rgb(im):
    """统一成 8bit RGB：透明底合成白底、CMYK/16bit/调色板图一律转过来。"""
    Image, _ImageFile, _ImageOps = pil_modules()
    if im.mode in ("RGBA", "LA") or (im.mode == "P" and "transparency" in im.info):
        rgba = im.convert("RGBA")
        canvas = Image.new("RGB", rgba.size, (255, 255, 255))
        canvas.paste(rgba, mask=rgba.split()[-1])
        return canvas
    if im.mode in ("I;16", "I;16B", "I;16L", "I", "F"):
        # 16bit 灰度/浮点扫描件：线性拉伸到 8bit，避免 convert("RGB") 直接截断成黑图
        try:
            return im.convert("L").convert("RGB")
        except Exception:  # noqa: BLE001
            return im.convert("RGB")
    if im.mode not in ("RGB", "L"):
        return im.convert("RGB")
    return im.convert("RGB") if im.mode == "L" else im


def normalize_image(src, out_dir, prefix, scale_fn=adaptive_scale):
    """自适应归一化一张图（或一张多帧图），返回帧清单。

    处理内容：EXIF 方向纠正 → 多帧展开（TIFF/GIF）→ 色彩统一（透明底/CMYK/16bit）
    → 尺寸收敛（长边 <1200 放大、>4000 压缩）。

    返回 [{"path", "size", "orig_size", "frame", "frames", "notes"}]
    """
    Image, _ImageFile, ImageOps = pil_modules()
    src = Path(src)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    frames = []
    with Image.open(src) as im:
        total = int(getattr(im, "n_frames", 1) or 1)
        truncated = total > MAX_IMAGE_FRAMES
        if truncated:
            total = MAX_IMAGE_FRAMES
        for idx in range(total):
            if total > 1:
                im.seek(idx)
            frame = im.copy()
            notes = []
            if truncated:
                notes.append("多帧图仅取前 %d 帧" % MAX_IMAGE_FRAMES)
            try:
                fixed = ImageOps.exif_transpose(frame)
                if fixed is not None and fixed.size != frame.size:
                    notes.append("已按 EXIF 方向纠正")
                    frame = fixed
            except Exception:  # noqa: BLE001 - EXIF 缺失/损坏不影响主流程
                pass
            rgb = _flatten_to_rgb(frame)
            orig_size = list(rgb.size)
            factor = scale_fn(rgb.size[0], rgb.size[1])
            if abs(factor - 1.0) > 1e-3:
                target = (max(1, int(round(rgb.size[0] * factor))),
                          max(1, int(round(rgb.size[1] * factor))))
                rgb = rgb.resize(target, Image.LANCZOS)
                notes.append("尺寸 %dx%d → %dx%d（x%.2f）"
                             % (orig_size[0], orig_size[1], rgb.size[0], rgb.size[1], factor))
            name = ("%s.png" % prefix) if total == 1 else ("%s_f%02d.png" % (prefix, idx + 1))
            path = out_dir / name
            # 低压缩级别：产物只喂视觉接口，不做无损比对。optimize=True 会跑 zlib
            # 参数搜索，在大扫描件上是秒级 CPU，且正好落在解析关键路径上。
            rgb.save(path, "PNG", compress_level=1)
            frames.append({"path": str(path), "size": list(rgb.size), "orig_size": orig_size,
                           "frame": idx + 1, "frames": int(getattr(im, "n_frames", 1) or 1),
                           "notes": notes})
    return frames


# ---------------------------------------------------------------- PDF

def pdf_page_dpi(page, target_px=PDF_RENDER_TARGET_PX,
                 dpi_min=PDF_DPI_MIN, dpi_max=PDF_DPI_MAX):
    """按页面物理尺寸反推渲染 DPI：长边落到 target_px 附近，再夹在 [dpi_min, dpi_max]。

    A4 竖版（595x842pt）约得 222 DPI，A3 横版自动降到下限附近，名片大小的页则升到上限。
    """
    try:
        w_pt = float(page.width or 0)
        h_pt = float(page.height or 0)
    except Exception:  # noqa: BLE001
        w_pt = h_pt = 0.0
    long_pt = max(w_pt, h_pt)
    if long_pt <= 0:
        return 200
    dpi = int(round(72.0 * target_px / long_pt))
    return max(dpi_min, min(dpi_max, dpi))


def pdf_min_chars(page, base=PDF_MIN_CHARS):
    """文本层阈值按页面积缩放：A4 用基准值，小页按比例下调（但不低于下限）。"""
    try:
        area = float(page.width or 0) * float(page.height or 0)
    except Exception:  # noqa: BLE001
        area = 0.0
    if area <= 0:
        return base
    return max(PDF_MIN_CHARS_FLOOR, int(round(base * area / A4_AREA_PT)))


def read_pdf(path, page_dir, prefix, base_min_chars=PDF_MIN_CHARS, target_px=PDF_RENDER_TARGET_PX):
    """逐页判文本层：够长就直读，太短就当扫描页渲染成 PNG 交给视觉转写。

    单页渲染失败只记一笔、继续后面的页（扫描件常有单页损坏，不该整份失败）。
    加密 PDF 先试空密码。
    """
    try:
        import pdfplumber
    except ImportError as exc:  # noqa: BLE001
        raise UnsupportedInput("需要 pdfplumber 才能处理 PDF（当前环境未安装）：%s" % exc)

    path = Path(path)
    page_dir = Path(page_dir)
    text_lines = []
    image_pages = []
    pages = []
    notes = []

    kwargs = {}
    try:
        pdf = pdfplumber.open(str(path))
    except Exception as exc:  # noqa: BLE001 - 多半是加密
        try:
            pdf = pdfplumber.open(str(path), password="")
            notes.append("PDF 已用空密码解锁")
        except Exception:  # noqa: BLE001
            raise UnsupportedInput(
                "%s：PDF 打开失败（可能已加密且非空密码）：%s: %s"
                % (path.name, type(exc).__name__, exc))

    with pdf:
        for idx, page in enumerate(pdf.pages, start=1):
            raw = ""
            try:
                raw = page.extract_text() or ""
            except Exception as exc:  # noqa: BLE001
                notes.append("第 %d 页文本层提取失败：%s" % (idx, type(exc).__name__))
            chars = len(re.sub(r"\s", "", raw))
            if chars >= pdf_min_chars(page, base_min_chars):
                text_lines.append("[[第%d页]]" % idx)
                text_lines.extend(_split_lines(raw))
                pages.append({"page": idx, "source": "text", "chars": chars, "dpi": None})
                continue
            dpi = pdf_page_dpi(page, target_px)
            page_dir.mkdir(parents=True, exist_ok=True)
            png = page_dir / ("%s_p%02d.png" % (prefix, idx))
            try:
                image = page.to_image(resolution=dpi).original
                # 同 normalize_image：渲染页只喂视觉接口，低压缩级别即可。
                image.convert("RGB").save(png, "PNG", compress_level=1)
            except Exception as exc:  # noqa: BLE001
                notes.append("第 %d 页无文本层且渲染失败（%s: %s），已跳过"
                             % (idx, type(exc).__name__, exc))
                pages.append({"page": idx, "source": "failed", "chars": chars, "dpi": dpi})
                continue
            pages.append({"page": idx, "source": "image", "chars": chars, "dpi": dpi})
            image_pages.append({"page": idx, "path": str(png), "chars": chars, "dpi": dpi})

    return text_lines, image_pages, pages, notes


# ---------------------------------------------------------------- 统一入口

def ingest(path, work_dir, prefix, image_normalize=True, **opts):
    """任意输入 → {kind, text_lines, image_pages, pages, detected_by, encoding, notes}。

    image_pages 里每项带 prefix，供切块/印章通道给产物命名（多页时按页区分）。
    work_dir 下会落归一化后的图片与 PDF 渲染页，调用方负责清理。
    """
    path = Path(path)
    if not path.exists():
        raise UnsupportedInput("文件不存在：%s" % path)
    work_dir = Path(work_dir)
    kind, how = detect_kind(path)
    plan = {"kind": kind, "source": str(path), "file": path.name, "detected_by": how,
            "text_lines": [], "image_pages": [], "pages": [], "encoding": None, "notes": []}

    if kind == KIND_TEXT:
        if path.suffix.lower() in DELIMITED_SUFFIXES:
            plan["text_lines"], plan["encoding"] = read_delimited_lines(path)
        else:
            plan["text_lines"], plan["encoding"] = read_text_lines(path)
    elif kind == KIND_DOCX:
        plan["text_lines"] = read_docx_lines(path)
    elif kind == KIND_XLSX:
        plan["text_lines"] = read_xlsx_lines(path)
    elif kind == KIND_PDF:
        lines, image_pages, pages, notes = read_pdf(
            path, work_dir / "pages", prefix,
            base_min_chars=opts.get("min_chars", PDF_MIN_CHARS),
            target_px=opts.get("target_px", PDF_RENDER_TARGET_PX))
        plan["text_lines"] = lines
        plan["image_pages"] = image_pages
        plan["pages"] = pages
        plan["notes"].extend(notes)
    elif kind == KIND_IMAGE:
        frames = ([{"path": str(path), "size": None, "orig_size": None,
                    "frame": 1, "frames": 1, "notes": ["未归一化（调用方关闭了归一化）"]}]
                  if not image_normalize else
                  normalize_image(path, work_dir / "images", prefix))
        for frame in frames:
            page = frame["frame"]
            plan["image_pages"].append({"page": page, "path": frame["path"], "chars": 0,
                                        "frame": frame["frame"], "frames": frame["frames"],
                                        "size": frame["size"], "orig_size": frame["orig_size"]})
            plan["pages"].append({"page": page, "source": "image", "chars": 0, "dpi": None})
            plan["notes"].extend(frame["notes"])
    else:  # pragma: no cover - detect_kind 已穷尽
        raise UnsupportedInput("%s：未支持的 kind=%s" % (path.name, kind))

    multi = len(plan["image_pages"]) > 1
    for item in plan["image_pages"]:
        item["prefix"] = ("%s_p%02d" % (prefix, item["page"])) if multi else prefix
    return plan


def summarize(plan):
    note = ("；%s" % "；".join(plan["notes"])) if plan.get("notes") else ""
    return "%s（%s）直读 %d 行 / 待视觉转写 %d 页%s" % (
        plan["kind"], plan["detected_by"], len(plan["text_lines"]),
        len(plan["image_pages"]), note)
