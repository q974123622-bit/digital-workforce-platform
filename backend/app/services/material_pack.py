"""材料包解包与落盘：一次上传一个 zip 或一批文件，安全摊开后逐份处理。

业务上「一笔业务 = 一个材料集」，但用户手里往往是一个目录、一个压缩包，
而且**不该要求用户为「分笔」去规范命名**：谁和谁属于同一笔业务，由 AI 员工
看材料内容判断。所以这里只负责「安全摊开」，包内目录名/文件名前缀最多算一个
**提示**（`hint_business_no_for`），不再决定物化布局。

安全与鲁棒性口径（都是踩过的坑）：
- **路径安全**：拒绝绝对路径、`..`、符号链接；嵌套层级上限 2 层（业务号/材料），
  再深的目录结构对「一笔业务」没有意义，只会把解压炸弹包装得更隐蔽；
- **配额**：文件数 2000、解压总量 500 MiB（zip 里的 `file_size` 是声明值，
  仍按实际写出字节数二次累计，防止伪造 header）；
- **加密包直接报错**，不尝试爆破，也不静默跳过；
- **Windows 压缩的中文文件名**：自带压缩不置 UTF-8 标志位（bit 11），文件名是
  GBK 字节被 cp437 解出来的乱码，必须 `name.encode('cp437').decode('gbk')` 还原；
- **平铺 vs 分目录**：默认平铺（`DWP_MATERIAL_FLAT_LAYOUT=1`），所有材料落在同一层，
  提示随材料一起往下传，分笔交给模型；置 `0` 回到「按业务号建子目录」的旧行为（回滚开关）。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from pathlib import Path, PurePosixPath

PACK_MAX_FILES = 2000
PACK_MAX_UNPACKED = 500 * 1024 * 1024
PACK_MAX_DEPTH = 2
UNGROUPED = "UNGROUPED"

# 材料包内允许出现的材料类型：图片与文档混装
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".tif", ".tiff", ".gif"}
DOC_SUFFIXES = {".pdf", ".docx", ".xlsx", ".csv", ".tsv", ".txt", ".md", ".markdown"}
PACK_SUFFIXES = IMAGE_SUFFIXES | DOC_SUFFIXES

# 业务号：字母数字加连字符/下划线，长度 4~32，至少含一个数字（避免把「扫描件」当业务号）
_BUSINESS_NO = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_\-]{2,31}$")


class PackError(Exception):
    """材料包不合法。调用方转 422 并把 message 原样给用户。"""


def flat_layout() -> bool:
    """物化是否平铺。默认开；置 0/false/no/off 回到「按业务号建子目录」的旧行为。"""
    raw = (os.getenv("DWP_MATERIAL_FLAT_LAYOUT", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def auto_extract() -> bool:
    """材料物化后是否自动跑一次字段抽值。默认开；置 0/false/no/off 关闭（回滚开关）。

    为什么放在平台侧触发：系统录入侧截图也是核对对象，基准方取值抽不出来，逐字段比对
    会退化成「客户材料互相印证」并给出假一致。真机上交给模型自觉去跑，5 张系统截图
    就漏了。规则与实现都在技能包里，平台只按次调用，不复制一份抽取逻辑。
    """
    raw = (os.getenv("DWP_MATERIAL_AUTO_EXTRACT", "1") or "1").strip().lower()
    return raw not in {"0", "false", "no", "off"}


def _decode_name(raw_name: str, flag_bits: int) -> str:
    """还原压缩包里的文件名。未置 UTF-8 标志位时按 GBK 解码（Windows 自带压缩）。"""
    if flag_bits & 0x800:
        return raw_name
    try:
        return raw_name.encode("cp437").decode("gbk")
    except (UnicodeEncodeError, UnicodeDecodeError):
        return raw_name


def _safe_member(name: str) -> PurePosixPath:
    posix = PurePosixPath(name.replace("\\", "/"))
    if posix.is_absolute() or ".." in posix.parts or not posix.parts:
        raise PackError("压缩包包含不安全路径：%s" % name)
    if len(posix.parts) > PACK_MAX_DEPTH + 1:
        raise PackError("压缩包目录层级过深（最多 %d 层）：%s" % (PACK_MAX_DEPTH, name))
    return posix


def looks_like_business_no(token: str) -> bool:
    return bool(token) and bool(_BUSINESS_NO.match(token)) and any(ch.isdigit() for ch in token)


def hint_business_no_for(parts: tuple[str, ...], filename: str) -> str:
    """推一个**仅供参考**的业务号提示：包内一级目录 > 文件名前缀 > UNGROUPED。

    名字里带业务号不代表材料内容真的属于那笔业务 —— 最终分笔由 AI 员工按材料内容判断。
    """
    if len(parts) >= 2 and looks_like_business_no(parts[0]):
        return parts[0]
    stem = Path(filename).stem
    for sep in ("_", "-"):
        head = stem.split(sep, 1)[0]
        if looks_like_business_no(head):
            return head
    return UNGROUPED


# 兼容旧名：老代码与测试按 business_no_for 调用，语义已降级为「提示」
business_no_for = hint_business_no_for


def _unique_path(dest: Path, name: str) -> Path:
    target = dest / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(1, 1000):
        candidate = dest / ("%s_%d%s" % (stem, index, suffix))
        if not candidate.exists():
            return candidate
    raise PackError("同名文件过多：%s" % name)


def unpack_zip(source, dest_dir, *, max_files: int = PACK_MAX_FILES,
               max_unpacked: int = PACK_MAX_UNPACKED,
               flat: bool | None = None) -> dict[str, list[Path]]:
    """把 zip 摊到 dest_dir，返回 {业务号提示: [文件路径]}。

    flat=True（默认）时所有文件落在 dest_dir 同一层，key 只是提示；
    flat=False 时按提示建子目录（旧行为，回滚用）。
    source 可以是磁盘路径，也可以是 bytes（上传流直接给内存时）。
    """
    flat = flat_layout() if flat is None else flat
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    try:
        archive = (zipfile.ZipFile(source) if isinstance(source, (str, Path))
                   else zipfile.ZipFile(io.BytesIO(source)))
    except zipfile.BadZipFile as exc:
        raise PackError("上传的文件不是有效 ZIP") from exc

    with archive:
        infos = [row for row in archive.infolist() if not row.is_dir()]
        if len(infos) > max_files:
            raise PackError("压缩包内文件数 %d 超过上限 %d" % (len(infos), max_files))
        groups: dict[str, list[Path]] = {}
        written = 0
        for row in infos:
            if row.flag_bits & 0x1:
                raise PackError("压缩包已加密，请解密后再上传")
            name = _decode_name(row.filename, row.flag_bits)
            posix = _safe_member(name)
            if posix.suffix.lower() not in PACK_SUFFIXES:
                continue
            hint = hint_business_no_for(posix.parts, posix.name)
            target_dir = dest if flat else dest / hint
            target_dir.mkdir(parents=True, exist_ok=True)
            target = _unique_path(target_dir, posix.name)
            with archive.open(row) as src, open(target, "wb") as out:
                written += _copy_capped(src, out, max_unpacked - written)
            groups.setdefault(hint, []).append(target)
        if not groups:
            raise PackError("压缩包里没有可识别的材料文件（支持图片与常见文档）")
    return groups


def unpack_files(items, dest_dir, *, max_files: int = PACK_MAX_FILES,
                 max_unpacked: int = PACK_MAX_UNPACKED,
                 flat: bool | None = None) -> dict[str, list[Path]]:
    """多文件直接上传（没有 zip）时落盘：items 是 ``[(filename, bytes), ...]``。

    与 `unpack_zip` 同口径：路径安全、后缀白名单、总量配额、重名自动加后缀。
    单份文件大小由调用方（附件服务）把关，这里只累计总量。
    """
    flat = flat_layout() if flat is None else flat
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    entries = list(items or [])
    if len(entries) > max_files:
        raise PackError("文件数 %d 超过上限 %d" % (len(entries), max_files))
    groups: dict[str, list[Path]] = {}
    written = 0
    for name, payload in entries:
        posix = _safe_member(name)
        if posix.suffix.lower() not in PACK_SUFFIXES:
            continue
        if written + len(payload) > max_unpacked:
            raise PackError("材料总量超过上限 %.0f MiB"
                            % (PACK_MAX_UNPACKED / 1024 / 1024))
        hint = hint_business_no_for(posix.parts, posix.name)
        target_dir = dest if flat else dest / hint
        target_dir.mkdir(parents=True, exist_ok=True)
        target = _unique_path(target_dir, posix.name)
        target.write_bytes(payload)
        written += len(payload)
        groups.setdefault(hint, []).append(target)
    if not groups:
        raise PackError("没有可识别的材料文件（支持图片与常见文档）")
    return groups


def _copy_capped(src, out, remaining: int) -> int:
    """边写边累计，防止 zip header 伪造 file_size 绕过配额。"""
    total = 0
    while True:
        block = src.read(1024 * 1024)
        if not block:
            break
        total += len(block)
        if total > remaining:
            raise PackError("压缩包解压后总量超过上限 %.0f MiB"
                            % (PACK_MAX_UNPACKED / 1024 / 1024))
        out.write(block)
    return total


def summarize(groups: dict[str, list[Path]]) -> dict:
    return {
        "business_count": len(groups),
        "file_count": sum(len(v) for v in groups.values()),
        "businesses": [{"business_no": key, "files": [p.name for p in value]}
                       for key, value in sorted(groups.items())],
        "note": "业务号只是按包内目录名/文件名推测的提示，可能与材料内容不符；"
                "分笔由 AI 员工按材料内容判断。",
    }
