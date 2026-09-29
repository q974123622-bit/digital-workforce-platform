"""会话附件预览：类型判定、原文件定位与缩略图渲染。

设计要点：

* **「能不能预览」只有一个判定来源**：``preview_kind()``。后端把它写进出参
  ``ConversationAttachmentOut.preview_kind``，前端只读这个字段，不再到处按
  ``file_type`` 字符串判断。
* **缩略图统一放独立根目录** ``attachment_root() / "_thumbs" / <attachment_id> /``，
  而不是附件同目录：``attachments.materialize_materials()`` 是按 DB 行遍历的
  （不会误把缩略图当材料），但 ``conversation_cleanup`` 删附件父目录时会因残留
  文件失败。独立目录让清理逻辑一处收敛（见 ``conversation_cleanup._remove_thumbs``）。
* 图像处理口径一律复用 ``ingest`` 里现成的函数（EXIF 方向、透明底合白、CMYK/16bit
  统一成 RGB），不另写一份 —— 同源副本已有一次踩坑记录。
* 任何失败都抛 ``PreviewUnavailable``（带中文原因），由 router 转成可读的
  404/422，绝不 500 静默。
"""

from __future__ import annotations

import os
from pathlib import Path

from . import attachment_vision, ingest

# 图片类型复用视觉链路的口径，避免两处各写一份
IMAGE_PREVIEW_TYPES = frozenset(attachment_vision.IMAGE_TYPES)
THUMB_DIRNAME = "_thumbs"
THUMB_WIDTH = 480
THUMB_QUALITY = 82


class PreviewUnavailable(Exception):
    """无法生成预览（文件缺失、损坏、类型不支持、PDF 加密等）。"""


def preview_kind(file_type: str) -> str:
    """附件可预览类型：``"image"`` / ``"pdf"`` / ``"none"``。

    这是全链路唯一判定来源；前端 ``previewKindOf()`` 与它一一对应。
    """
    kind = (file_type or "").strip().lower().lstrip(".")
    if kind in IMAGE_PREVIEW_TYPES:
        return "image"
    if kind == "pdf":
        return "pdf"
    return "none"


def previewable(file_type: str) -> bool:
    return preview_kind(file_type) != "none"


def _root() -> Path:
    # 延迟 import，避免 services 内部循环依赖（attachments 依赖 material_pack 等）
    from .attachments import attachment_root

    return attachment_root()


def resolve_storage_path(row) -> Path:
    """把 ``storage_path`` 拼成绝对路径，并做越界防护。

    与 ``conversation_cleanup._remove_disk_files`` 同一套口径：resolve 之后必须
    仍落在 ``attachment_root()`` 之内，否则视为被篡改，抛 ``PreviewUnavailable``。
    """
    relative = (getattr(row, "storage_path", "") or "").strip()
    if not relative:
        raise PreviewUnavailable("附件缺少存储路径")
    try:
        root = _root().resolve()
    except Exception as exc:  # noqa: BLE001 - 目录不可用
        raise PreviewUnavailable("附件存储目录不可用：%s" % exc) from exc
    target = (root / relative).resolve()
    if target == root or root not in target.parents:
        raise PreviewUnavailable("附件存储路径越界")
    return target


def thumb_cache_path(row, width: int = THUMB_WIDTH) -> Path:
    """缩略图缓存文件路径：``_thumbs/<attachment_id>/<sha256前16>-<width>.jpg``。"""
    digest = (getattr(row, "sha256", "") or "")[:16] or "nohash"
    return _root() / THUMB_DIRNAME / str(row.id) / ("%s-%d.jpg" % (digest, int(width)))


def thumb_etag(row, width: int = THUMB_WIDTH) -> str:
    """缩略图 ETag：``<sha256前16>-<width>``。"""
    digest = (getattr(row, "sha256", "") or "")[:16] or "nohash"
    return "%s-%d" % (digest, int(width))


def _write_atomic(target: Path, image) -> None:
    """临时文件 + ``os.replace``：并发渲染时不会读到半截 JPEG。"""
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name("%s.%d.tmp" % (target.name, os.getpid()))
    try:
        image.save(tmp, "JPEG", quality=THUMB_QUALITY, optimize=False)
        os.replace(tmp, target)
    finally:
        if tmp.exists():
            try:
                tmp.unlink()
            except OSError:  # noqa: BLE001
                pass


def _thumbnail_image(source: Path, width: int, kind: str):
    """读原文件并收敛成长边 ≤ width 的 RGB 图。失败抛 ``PreviewUnavailable``。"""
    if kind == "pdf":
        frame = _render_pdf_first_page(source)
    else:
        frame = _open_image(source)
    try:
        # thumbnail 保持宽高比，只缩不放
        frame.thumbnail((int(width), int(width)), _resample())
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable("缩略图缩放失败：%s: %s" % (type(exc).__name__, exc)) from exc
    return frame


def _resample():
    Image, _ImageFile, _ImageOps = ingest.pil_modules()
    return getattr(Image, "Resampling", Image).LANCZOS


def _open_image(source: Path):
    Image, _ImageFile, ImageOps = ingest.pil_modules()
    if not source.is_file():
        raise PreviewUnavailable("附件原文件不存在")
    try:
        with Image.open(source) as raw:
            raw.load()
            frame = raw.copy()
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable("图片无法打开：%s: %s" % (type(exc).__name__, exc)) from exc
    try:
        fixed = ImageOps.exif_transpose(frame)
        if fixed is not None:
            frame = fixed
    except Exception:  # noqa: BLE001 - EXIF 缺失/损坏不影响主流程
        pass
    try:
        return ingest._flatten_to_rgb(frame)
    except Exception as exc:  # noqa: BLE001
        raise PreviewUnavailable("图片色彩转换失败：%s: %s" % (type(exc).__name__, exc)) from exc


def _render_pdf_first_page(source: Path, resolution: int = 72):
    """用 pdfplumber 渲染 PDF 首页为 PIL 图；加密/损坏给可读原因。"""
    try:
        import pdfplumber
    except ImportError as exc:  # noqa: BLE001
        raise PreviewUnavailable("需要 pdfplumber 才能预览 PDF（当前环境未安装）") from exc
    if not source.is_file():
        raise PreviewUnavailable("附件原文件不存在")
    try:
        pdf = pdfplumber.open(str(source))
    except Exception as exc:  # noqa: BLE001 - 多半是加密
        try:
            pdf = pdfplumber.open(str(source), password="")
        except Exception:  # noqa: BLE001
            raise PreviewUnavailable(
                "PDF 打开失败（可能已加密）：%s: %s" % (type(exc).__name__, exc)) from exc
    with pdf:
        if not pdf.pages:
            raise PreviewUnavailable("PDF 没有可渲染的页面")
        try:
            image = pdf.pages[0].to_image(resolution=resolution).original
        except Exception as exc:  # noqa: BLE001
            raise PreviewUnavailable("PDF 首页渲染失败：%s: %s" % (type(exc).__name__, exc)) from exc
        try:
            return image.convert("RGB")
        except Exception:  # noqa: BLE001
            return ingest._flatten_to_rgb(image)


def render_thumbnail(row, width: int = THUMB_WIDTH) -> Path:
    """返回缩略图缓存路径；未命中则渲染并落盘。失败抛 ``PreviewUnavailable``。"""
    kind = preview_kind(getattr(row, "file_type", ""))
    if kind == "none":
        raise PreviewUnavailable("该附件类型不支持预览")
    target = thumb_cache_path(row, width)
    if target.is_file() and target.stat().st_size > 0:
        return target
    source = resolve_storage_path(row)
    if not source.is_file():
        raise PreviewUnavailable("附件原文件不存在")
    image = _thumbnail_image(source, width, kind)
    try:
        _write_atomic(target, image)
    finally:
        try:
            image.close()
        except Exception:  # noqa: BLE001
            pass
    return target


__all__ = [
    "IMAGE_PREVIEW_TYPES", "THUMB_DIRNAME", "THUMB_WIDTH", "PreviewUnavailable",
    "preview_kind", "previewable", "resolve_storage_path", "thumb_cache_path",
    "thumb_etag", "render_thumbnail",
]
