"""材料包解包与图片附件放行的回归测试。

覆盖的是「用户会踩、但代码里看不出来」的那几处：
Windows 压缩的中文文件名乱码、路径穿越、加密包、配额、图片魔数与改名、
以及材料包不受「单次 5 个」限制但受包配额约束。
"""

from __future__ import annotations

import binascii
import io
import json
import struct
import zipfile
from pathlib import Path

import pytest

from app.services import material_pack


def _png(payload: bytes = b"0" * 32) -> bytes:
    return b"\x89PNG\r\n\x1a\n" + payload


def _jpeg(payload: bytes = b"0" * 32) -> bytes:
    return b"\xff\xd8\xff" + payload


def _write_gbk_zip(path, name: str, payload: bytes = b"x") -> None:
    """手写「GBK 文件名且未置 UTF-8 标志位」的 zip，模拟 Windows 自带压缩。"""
    raw = name.encode("gbk")
    crc = binascii.crc32(payload) & 0xFFFFFFFF
    local = struct.pack("<IHHHHHIIIHH", 0x04034B50, 20, 0, 0, 0, 0, crc,
                        len(payload), len(payload), len(raw), 0) + raw + payload
    central = struct.pack("<IHHHHHHIIIHHHHHII", 0x02014B50, 20, 20, 0, 0, 0, 0, crc,
                          len(payload), len(payload), len(raw), 0, 0, 0, 0, 0, 0) + raw
    end = struct.pack("<IHHHHIIH", 0x06054B50, 0, 0, 1, 1, len(central), len(local), 0)
    path.write_bytes(local + central + end)


def test_unpack_groups_by_directory(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B2026001/营业执照.png", _png())
        zf.writestr("B2026001/身份证.jpg", _jpeg())
        zf.writestr("B2026002/合同.pdf", b"%PDF-1.4\n" + b"0" * 32)
    groups = material_pack.unpack_zip(archive, tmp_path / "out")
    assert set(groups) == {"B2026001", "B2026002"}
    assert len(groups["B2026001"]) == 2
    assert sorted(p.name for p in groups["B2026001"]) == ["营业执照.png", "身份证.jpg"]


def test_unpack_falls_back_to_filename_prefix(tmp_path):
    archive = tmp_path / "flat.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B2026004_营业执照.png", _png())
        zf.writestr("扫描件.png", _png())          # 推不出业务号 → UNGROUPED
    groups = material_pack.unpack_zip(archive, tmp_path / "out")
    assert "B2026004" in groups
    assert material_pack.UNGROUPED in groups


def test_unpack_rejects_path_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("../escape.png", _png())
    with pytest.raises(material_pack.PackError) as exc:
        material_pack.unpack_zip(archive, tmp_path / "out")
    assert "不安全路径" in str(exc.value)


def test_unpack_rejects_deep_nesting(tmp_path):
    archive = tmp_path / "deep.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B1/a/b/c/营业执照.png", _png())
    with pytest.raises(material_pack.PackError):
        material_pack.unpack_zip(archive, tmp_path / "out")


def test_unpack_rejects_encrypted(tmp_path):
    archive = tmp_path / "enc.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B1/营业执照.png", _png())
    # 加密位要同时改「本地头」和「中央目录」两处：zipfile 读的是中央目录的 flag_bits
    data = bytearray(archive.read_bytes())
    for marker, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        index = data.find(marker)
        flags = int.from_bytes(data[index + offset:index + offset + 2], "little") | 0x1
        data[index + offset:index + offset + 2] = flags.to_bytes(2, "little")
    archive.write_bytes(bytes(data))
    with pytest.raises(material_pack.PackError) as exc:
        material_pack.unpack_zip(archive, tmp_path / "out")
    assert "加密" in str(exc.value)


def test_unpack_enforces_file_quota(tmp_path):
    archive = tmp_path / "many.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        for index in range(5):
            zf.writestr(f"B1/f{index}.png", _png())
    with pytest.raises(material_pack.PackError) as exc:
        material_pack.unpack_zip(archive, tmp_path / "out", max_files=3)
    assert "上限" in str(exc.value)


def test_unpack_enforces_size_quota(tmp_path):
    archive = tmp_path / "big.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B1/营业执照.png", _png(b"0" * 4096))
    with pytest.raises(material_pack.PackError) as exc:
        material_pack.unpack_zip(archive, tmp_path / "out", max_unpacked=1024)
    assert "总量超过上限" in str(exc.value)


def test_unpack_restores_gbk_filename(tmp_path):
    archive = tmp_path / "gbk.zip"
    _write_gbk_zip(archive, "B2026003/营业执照.png", _png())
    groups = material_pack.unpack_zip(archive, tmp_path / "out")
    assert [p.name for p in groups["B2026003"]] == ["营业执照.png"]


def test_unpack_rejects_empty_and_broken(tmp_path):
    with pytest.raises(material_pack.PackError):
        material_pack.unpack_zip(b"not a zip at all", tmp_path / "out")
    archive = tmp_path / "empty.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B1/readme.exe", b"MZ")       # 不在允许后缀内
    with pytest.raises(material_pack.PackError) as exc:
        material_pack.unpack_zip(archive, tmp_path / "out")
    assert "没有可识别" in str(exc.value)


def test_duplicate_names_do_not_overwrite(tmp_path):
    """同名材料必须改名落盘：zipfile 本身不允许同名条目，这里直接验改名逻辑。"""
    target = tmp_path / "营业执照.png"
    target.write_bytes(_png())
    again = material_pack._unique_path(tmp_path, "营业执照.png")
    assert again.name == "营业执照_1.png"
    again.write_bytes(_png())
    third = material_pack._unique_path(tmp_path, "营业执照.png")
    assert third.name == "营业执照_2.png"



def test_summarize(tmp_path):
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B1/营业执照.png", _png())
    summary = material_pack.summarize(material_pack.unpack_zip(archive, tmp_path / "out"))
    assert summary["business_count"] == 1 and summary["file_count"] == 1


# ---------------------------------------------------------------- 附件链路

def test_image_signature_validation_rejects_renamed_file():
    from fastapi import HTTPException

    from app.services import attachments

    # 后缀是 png、内容是文本 → 必须拦掉，不能等进了视觉链路才失败
    with pytest.raises(HTTPException) as exc:
        attachments._validate_signature("png", b"hello world, not an image", "x.png")
    assert exc.value.status_code == 422
    # 真 PNG 通过
    attachments._validate_signature("png", _png(), "x.png")
    attachments._validate_signature("jpg", _jpeg(), "x.jpg")


def test_image_types_are_supported():
    from app.services import attachments

    for suffix in ("png", "jpg", "jpeg", "webp", "bmp", "tif", "tiff", "gif"):
        assert suffix in attachments.SUPPORTED
    assert attachments.MAX_FILES_PER_MESSAGE == 5      # 零散上传仍是 5 个


def test_pack_limits_follow_env(monkeypatch):
    from app.services import attachments

    monkeypatch.setenv("DWP_ATTACHMENT_PACK_MAX_FILES", "12")
    monkeypatch.setenv("DWP_ATTACHMENT_PACK_MAX_TOTAL_MB", "7")
    files, total = attachments.pack_limits()
    assert files == 12 and total == 7 * 1024 * 1024


def _sample_fixture_dir():
    """真样例测试数据目录（由 `07_抽取评测/runner/build_sample_fixture.py` 固化）。

    容器里跑测试时用 `DWP_SAMPLE_FIXTURE_DIR` 指过去；找不到就跳过，不把「没数据」
    算成失败。
    """
    import os

    candidates = []
    env = os.environ.get("DWP_SAMPLE_FIXTURE_DIR")
    if env:
        candidates.append(Path(env))
    for base in (Path(__file__).resolve(), Path("/op"), Path("/audit"), Path("/repo")):
        for parent in [base, *base.parents]:
            candidates.append(parent / "08_落地蓝图" / "审核技能包" / "tests" / "fixtures")
            candidates.append(parent / "audit" / "operation" / "08_落地蓝图"
                              / "审核技能包" / "tests" / "fixtures")
    for candidate in candidates:
        if (candidate / "sample-files.zip").is_file():
            return candidate
    return None


def test_real_sample_pack_unpacks(tmp_path):
    """拿 13 份真样例打的包跑一遍解包：中文名、空格、括号、docx 混排都要能过。"""
    fixture = _sample_fixture_dir()
    if fixture is None:
        pytest.skip("找不到样例测试数据（先跑 runner/build_sample_fixture.py）")
    archive = fixture / "sample-files.zip"
    groups = material_pack.unpack_zip(archive, tmp_path / "out")

    names = sorted(p.name for files in groups.values() for p in files)
    assert "营业执照.png" in names
    assert "开户截图 (1).png" in names          # 空格 + 括号：Windows 压缩里最常见
    assert any(name.endswith(".docx") for name in names)   # 混排 docx
    assert len(names) == 18, names
    # 按业务号（样例里的两笔）分组
    assert len(groups) == 2, sorted(groups)
    assert all(len(files) > 0 for files in groups.values())

    # 真实魔数必须判对类型：图片、docx 都要认出来
    from app.services import ingest

    kinds = {}
    for files in groups.values():
        for path in files:
            kinds[path.name] = ingest.detect_kind_bytes(path.read_bytes(), path.name)[0]
    assert kinds["营业执照.png"] == "image"
    assert kinds["开户截图 (1).png"] == "image"
    docx = next(name for name in kinds if name.endswith(".docx"))
    assert kinds[docx] == "docx", (docx, kinds[docx])

    # 附件类型白名单必须认得这些真实后缀
    from app.services import attachments

    for name in names:
        suffix = name.rsplit(".", 1)[-1].lower()
        assert suffix in attachments.SUPPORTED, name


def test_ingest_backend_copy_is_identical_to_skill_package():
    """平台侧 ingest 是技能包的逐字副本：改一处必须同步，否则评测证明不了线上行为。"""
    from pathlib import Path

    from app.services import ingest

    backend_copy = Path(ingest.__file__)
    for parent in [backend_copy, *backend_copy.parents]:
        skill = parent / "08_落地蓝图" / "审核技能包" / "scripts" / "ingest.py"
        if skill.is_file():
            assert skill.read_text(encoding="utf-8") == backend_copy.read_text(encoding="utf-8"), (
                "backend/app/services/ingest.py 与技能包 scripts/ingest.py 已漂移，请同步")
            return
    pytest.skip("找不到技能包目录（容器内跑测试时正常）")


# ---------------------------------------------------------------- 平铺与多文件上传

def test_flat_layout_env_switch(monkeypatch):
    monkeypatch.delenv("DWP_MATERIAL_FLAT_LAYOUT", raising=False)
    assert material_pack.flat_layout() is True              # 默认平铺
    for value in ("0", "false", "no", "OFF"):
        monkeypatch.setenv("DWP_MATERIAL_FLAT_LAYOUT", value)
        assert material_pack.flat_layout() is False, value
    monkeypatch.setenv("DWP_MATERIAL_FLAT_LAYOUT", "1")
    assert material_pack.flat_layout() is True


def test_unpack_zip_flattens_by_default(tmp_path):
    """平铺是默认：提示照旧按目录名推，但文件落在同一层，重名自动加后缀。"""
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B2026001/营业执照.png", _png())
        zf.writestr("B2026002/营业执照.png", _png())
    groups = material_pack.unpack_zip(archive, tmp_path / "out")
    assert set(groups) == {"B2026001", "B2026002"}
    names = sorted(p.name for files in groups.values() for p in files)
    assert names == ["营业执照.png", "营业执照_1.png"]
    assert all(p.parent == tmp_path / "out" for files in groups.values() for p in files)


def test_unpack_zip_keeps_subdirs_when_flat_off(tmp_path):
    """回滚开关：flat=False 回到「按业务号建子目录」的旧布局。"""
    archive = tmp_path / "pack.zip"
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr("B2026001/营业执照.png", _png())
    groups = material_pack.unpack_zip(archive, tmp_path / "out", flat=False)
    assert groups["B2026001"][0].parent == tmp_path / "out" / "B2026001"


def test_unpack_files_flat_and_filters(tmp_path):
    """多文件直接上传：非白名单后缀静默跳过，其余平铺落盘。"""
    groups = material_pack.unpack_files(
        [("营业执照.png", _png()), ("说明.txt", b"hello"), ("木马.exe", b"MZ")],
        tmp_path / "out")
    assert material_pack.UNGROUPED in groups
    assert sorted(p.name for p in groups[material_pack.UNGROUPED]) == ["营业执照.png", "说明.txt"]
    assert all(p.parent == tmp_path / "out" for p in groups[material_pack.UNGROUPED])


def test_unpack_files_dedupes_same_name(tmp_path):
    groups = material_pack.unpack_files(
        [("B2026001_营业执照.png", _png()), ("B2026001_营业执照.png", _png())],
        tmp_path / "out")
    assert sorted(p.name for p in groups["B2026001"]) == [
        "B2026001_营业执照.png", "B2026001_营业执照_1.png"]


def test_unpack_files_enforces_quota(tmp_path):
    with pytest.raises(material_pack.PackError):
        material_pack.unpack_files([("a.png", _png())], tmp_path / "out", max_unpacked=8)
    with pytest.raises(material_pack.PackError):
        material_pack.unpack_files([("a.png", _png())], tmp_path / "out", max_files=0)


def test_unpack_files_rejects_unsafe_name(tmp_path):
    with pytest.raises(material_pack.PackError):
        material_pack.unpack_files([("../escape.png", _png())], tmp_path / "out")


def test_business_no_for_is_now_a_hint_alias():
    """语义降级为「提示」，但旧名必须仍然可用（老代码与既有测试按 business_no_for 调用）。"""
    assert material_pack.business_no_for(("B2026001", "a.png"), "a.png") == "B2026001"
    assert (material_pack.hint_business_no_for(("扫描件", "a.png"), "a.png")
            == material_pack.UNGROUPED)


def test_materialize_materials_flattens(tmp_path, db_session, monkeypatch):
    """物化默认平铺：不建业务号子目录、重名代号加后缀、提示保留为 hint_business_no。"""
    from types import SimpleNamespace

    from app.services import attachments, runtime_manager

    rows = [
        SimpleNamespace(id="ATT-1", filename="营业执照.png", file_type="png",
                        business_no="B2026001", pack_id="PACK-1"),
        SimpleNamespace(id="ATT-2", filename="营业执照.png", file_type="png",
                        business_no="B2026002", pack_id="PACK-1"),
        SimpleNamespace(id="ATT-3", filename="开户截图.png", file_type="png",
                        business_no=None, pack_id="PACK-1"),
    ]
    monkeypatch.setattr(attachments, "attachments_for_trace",
                        lambda db, trace_id: (None, rows))
    monkeypatch.setattr(runtime_manager, "materials_root",
                        lambda employee_id: tmp_path / "ws")
    monkeypatch.delenv("DWP_MATERIAL_FLAT_LAYOUT", raising=False)
    # 本用例只关心物化布局，把「上传即抽取」关掉保持单测封闭（该能力另有专门用例）
    monkeypatch.setenv("DWP_MATERIAL_AUTO_EXTRACT", "0")

    result = attachments.materialize_materials(db_session, employee_id="AI-AUDIT", trace_id="T1")
    root = tmp_path / "ws" / "PACK-1"
    assert result["materials"] == 3
    assert not any(p.is_dir() for p in root.iterdir())        # 平铺，没有业务号子目录
    names = sorted(p.name for p in root.glob("*.json"))
    assert names == ["materials.json", "开户截图.json", "营业执照.json", "营业执照_2.json"]

    body = json.loads((root / "营业执照.json").read_text(encoding="utf-8"))
    assert body["hint_business_no"] == "B2026001"             # 提示随材料一起往下传
    assert body["material_type"] is None                      # 类型仍由 agent 判断
    manifest = json.loads((root / "materials.json").read_text(encoding="utf-8"))
    assert manifest["layout"] == "flat"
    assert manifest["businesses"] == ["B2026001", "B2026002", "UNGROUPED"]
    assert all("hint_business_no" in item for item in manifest["materials"])

    monkeypatch.setenv("DWP_MATERIAL_FLAT_LAYOUT", "0")
    result = attachments.materialize_materials(db_session, employee_id="AI-AUDIT", trace_id="T1")
    assert result["materials"] == 3
    assert (root / "B2026001" / "营业执照.json").is_file()     # 回滚开关：按提示分目录


def _fake_skill_tree(tmp_path) -> Path:
    scripts = tmp_path / "skills" / "institution-account-material-review" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "review.py").write_text("", encoding="utf-8")
    return tmp_path / "skills"


def _materialize_one(db_session, tmp_path, monkeypatch, exec_fn):
    """跑一次单材料物化，返回 (result, 传给容器的调用记录)。"""
    from types import SimpleNamespace

    from app.services import attachments, runtime_manager

    rows = [SimpleNamespace(id="ATT-1", filename="营业执照.png", file_type="png",
                            business_no=None, pack_id="PACK-9")]
    monkeypatch.setattr(attachments, "attachments_for_trace",
                        lambda db, trace_id: (None, rows))
    monkeypatch.setattr(runtime_manager, "materials_root",
                        lambda employee_id: tmp_path / "ws")
    monkeypatch.setattr(runtime_manager, "skills_root",
                        lambda employee_id: _fake_skill_tree(tmp_path))
    monkeypatch.setattr(runtime_manager, "container_name", lambda employee_id: "dwp-harness-x")
    monkeypatch.delenv("DWP_MATERIAL_AUTO_EXTRACT", raising=False)
    seen: dict = {}

    def spy(name, command, **kwargs):
        seen["name"] = name
        seen["command"] = command
        return exec_fn()

    monkeypatch.setattr(runtime_manager, "exec_in_container", spy)
    result = attachments.materialize_materials(db_session, employee_id="AI-AUDIT", trace_id="T1")
    return result, seen


def test_materialize_runs_auto_extract(tmp_path, db_session, monkeypatch):
    """物化后自动跑技能包抽值：命令指向容器内技能与材料目录，结果回传给调用方。

    这条是「上传即抽取」的落点 —— 系统录入侧截图也是核对对象，抽不出来基准方取值，
    逐字段比对会退化成客户材料互相印证。交给模型自觉去跑，真机上就漏了 5 张截图。
    """
    from types import SimpleNamespace

    def ok():
        return SimpleNamespace(returncode=0, stderr="", stdout=json.dumps({
            "materials": 1, "extracted": 7,
            "by_material": {"营业执照": {"side": "customer", "extracted": 7}},
        }))

    result, seen = _materialize_one(db_session, tmp_path, monkeypatch, ok)
    assert seen["name"] == "dwp-harness-x"
    assert seen["command"][0] == "python3"
    assert seen["command"][1].endswith("/skills/institution-account-material-review/scripts/review.py")
    assert seen["command"][2:] == ["extract-all", "--materials", "/workspace/materials/PACK-9"]
    assert result["extraction"]["institution-account-material-review"]["extracted"] == 7


def test_materialize_auto_extract_failure_is_not_fatal(tmp_path, db_session, monkeypatch):
    """抽值失败只告警：材料已经物化好，不能因为抽不出来就阻断这一轮执行。"""
    from types import SimpleNamespace

    def boom():
        return SimpleNamespace(returncode=1, stdout="", stderr="容器不存在")

    result, seen = _materialize_one(db_session, tmp_path, monkeypatch, boom)
    assert seen["command"][2] == "extract-all"
    assert result["materials"] == 1
    assert result["extraction"] == {}


def test_materialize_auto_extract_switch(tmp_path, db_session, monkeypatch):
    """回滚开关：DWP_MATERIAL_AUTO_EXTRACT=0 时不调容器。"""
    from types import SimpleNamespace

    from app.services import attachments, runtime_manager

    rows = [SimpleNamespace(id="ATT-1", filename="营业执照.png", file_type="png",
                            business_no=None, pack_id="PACK-9")]
    monkeypatch.setattr(attachments, "attachments_for_trace",
                        lambda db, trace_id: (None, rows))
    monkeypatch.setattr(runtime_manager, "materials_root",
                        lambda employee_id: tmp_path / "ws")
    monkeypatch.setenv("DWP_MATERIAL_AUTO_EXTRACT", "0")
    monkeypatch.setattr(runtime_manager, "exec_in_container",
                        lambda *a, **k: pytest.fail("关闭开关后不应调用容器"))
    result = attachments.materialize_materials(db_session, employee_id="AI-AUDIT", trace_id="T1")
    assert result["extraction"] == {}


# ---------------------------------------------------------------- 批量并发解析

def test_process_jobs_uses_one_bounded_pool(monkeypatch):
    """一次上传 N 份只建一个线程池，workers 不超过视觉层在飞上限（唯一限流点）。"""
    from concurrent.futures import ThreadPoolExecutor as _RealPool

    from app.services import attachments

    monkeypatch.setenv("DWP_VISION_MAX_INFLIGHT", "12")
    seen: dict = {}

    class _RecordingPool:
        def __init__(self, max_workers=None):
            seen["workers"] = max_workers
            self._pool = _RealPool(max_workers=max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._pool.shutdown(wait=True)
            return False

        def submit(self, fn, *args, **kwargs):
            return self._pool.submit(fn, *args, **kwargs)

    ran: list[str] = []
    monkeypatch.setattr(attachments, "ThreadPoolExecutor", _RecordingPool)
    monkeypatch.setattr(attachments, "process_job", lambda job_id: ran.append(job_id))
    attachments.process_jobs(["J1", "J2", "J3"])
    assert seen["workers"] == 3                 # min(份数, 上限)
    assert sorted(ran) == ["J1", "J2", "J3"]


def test_process_jobs_caps_workers_at_vision_limit(monkeypatch):
    from concurrent.futures import ThreadPoolExecutor as _RealPool

    from app.services import attachments

    monkeypatch.setenv("DWP_VISION_MAX_INFLIGHT", "2")
    assert attachments.attachment_vision._max_inflight() == 2
    seen: dict = {}

    class _RecordingPool:
        def __init__(self, max_workers=None):
            seen["workers"] = max_workers
            self._pool = _RealPool(max_workers=max_workers)

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            self._pool.shutdown(wait=True)
            return False

        def submit(self, fn, *args, **kwargs):
            return self._pool.submit(fn, *args, **kwargs)

    monkeypatch.setattr(attachments, "ThreadPoolExecutor", _RecordingPool)
    monkeypatch.setattr(attachments, "process_job", lambda job_id: None)
    # 5 份但上限 2 → workers 必须是 2，不能放开成 5（放开会把整批打成 502）
    attachments.process_jobs(["J1", "J2", "J3", "J4", "J5"])
    assert seen["workers"] == 2


def test_process_jobs_single_job_skips_pool(monkeypatch):
    """单份不建池：省掉一层线程开销，行为与原来逐个 process_job 完全一致。"""
    from app.services import attachments

    def _boom(*_args, **_kwargs):
        raise AssertionError("单份不应建线程池")

    monkeypatch.setattr(attachments, "ThreadPoolExecutor", _boom)
    ran: list[str] = []
    monkeypatch.setattr(attachments, "process_job", lambda job_id: ran.append(job_id))
    attachments.process_jobs(["J1"])
    assert ran == ["J1"]


def test_process_jobs_dedupes_and_survives_one_failure(monkeypatch):
    """重复 job_id 只跑一次；单份失败只记日志，不中断其余份。"""
    from app.services import attachments

    ran: list[str] = []

    def flaky(job_id):
        ran.append(job_id)
        if job_id == "J2":
            raise RuntimeError("boom")

    monkeypatch.setattr(attachments, "process_job", flaky)
    attachments.process_jobs(["J1", "J1", "J2", "J3"])   # 不抛异常即通过
    assert sorted(ran) == ["J1", "J2", "J3"]


def test_dispatch_jobs_merges_into_one_batch_task():
    """路由层：一次上传只派发一条批量任务（BackgroundTasks 是顺序 await，逐个派发会串行）。"""
    from fastapi import BackgroundTasks

    from app.routers import attachments as router

    tasks = BackgroundTasks()
    router._dispatch_jobs(tasks, ["J1", "J2", "J3"])
    assert len(tasks.tasks) == 1
    assert tasks.tasks[0].args == (["J1", "J2", "J3"],)
    # 空清单不派发，避免后台跑一个什么都不做的任务
    router._dispatch_jobs(tasks, [])
    assert len(tasks.tasks) == 1


def test_pack_upload_dispatches_one_batch_task(client, monkeypatch, tmp_path):
    """一次多选上传：整个包只派发一次 process_jobs，且带上全部 job。"""
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    monkeypatch.setenv("DWP_ATTACHMENT_VISION_ENABLED", "0")   # 不真调视觉
    from app.routers import attachments as router

    assert client.post("/api/v1/auth/key-login",
                       json={"access_key": "test-key-e10281"}).status_code == 200
    conv = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    }).json()

    # 只观察派发口径，不真跑解析：测试库是内存 SQLite，跨线程连接互不可见，
    # 真跑并发解析会落到各自的空库上（正式环境是文件库 + WAL，没有这个问题）。
    calls: list[list[str]] = []
    monkeypatch.setattr(router.attachments, "process_jobs", lambda job_ids: calls.append(list(job_ids)))

    response = client.post(
        f"/api/v1/conversations/{conv['id']}/attachments/pack/files",
        files=[("files", ("营业执照.png", _png(), "image/png")),
               ("files", ("身份证.png", _png(), "image/png"))],
    )
    assert response.status_code == 200
    assert len(response.json()) == 2
    assert len(calls) == 1, calls
    assert len(calls[0]) == 2, calls
