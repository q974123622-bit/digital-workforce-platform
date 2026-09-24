"""技能包校验放宽后的边界回归。

用户确认的取舍：技能允许携带 ``.py`` 等脚本（数字员工在 harness 容器里执行），
但**可执行二进制与 shell 脚本仍然禁止**，secret 检查与网络声明禁用一律保留。
"""

from __future__ import annotations

import io
import zipfile

import pytest
from fastapi import HTTPException

from app.services.plugin_governance import MAX_FILES, validate_zip

SKILL_YAML = "name: 文档处理\ndescription: 生成与解析 docx\n"


def _zip(files: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return buffer.getvalue()


def _skill_zip(extra: dict[str, str | bytes] | None = None) -> bytes:
    files: dict[str, str | bytes] = {"plugin.yaml": SKILL_YAML, "SKILL.md": "# 文档处理\n"}
    files.update(extra or {})
    return _zip(files)


def test_skill_scripts_are_allowed():
    data = _skill_zip({
        "scripts/office/pack.py": "print('pack')\n",
        "scripts/lib/helper.js": "export const a = 1;\n",
        "assets/template.docx": b"PK\x03\x04binary",
    })
    manifest, digest = validate_zip(data, "skill", "instruction")
    assert manifest["instruction"] == "# 文档处理\n"
    assert len(digest) == 64


@pytest.mark.parametrize("name", ["run.sh", "tool.exe", "lib.so", "hook.ps1", "install.bat"])
def test_forbidden_suffixes_still_rejected(name: str):
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(_skill_zip({f"scripts/{name}": "x"}), "skill", "instruction")
    assert excinfo.value.status_code == 422
    assert "不允许的文件" in str(excinfo.value.detail)


def test_secret_keys_still_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(
            _zip({"plugin.yaml": "name: x\napi_key: sk-real\n", "SKILL.md": "# x\n"}),
            "skill", "instruction",
        )
    assert "凭据" in str(excinfo.value.detail)


def test_skill_network_declaration_still_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(
            _zip({"plugin.yaml": "name: x\nendpoint: https://internal\n", "SKILL.md": "# x\n"}),
            "skill", "instruction",
        )
    assert "不允许声明网络" in str(excinfo.value.detail)


def test_file_count_limit_raised_to_500():
    assert MAX_FILES == 500
    many = {f"scripts/f{index:03d}.py": "x" for index in range(300)}
    validate_zip(_skill_zip(many), "skill", "instruction")

    too_many = {f"scripts/f{index:03d}.py": "x" for index in range(MAX_FILES + 1)}
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(_skill_zip(too_many), "skill", "instruction")
    assert "超限" in str(excinfo.value.detail)


def test_mcp_branch_unchanged():
    """MCP 分支不受放宽影响：仍要求 tools，且不允许 hosted 任意安装命令。"""
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(_zip({"plugin.yaml": "name: x\n"}), "mcp", "external")
    assert "工具 Schema" in str(excinfo.value.detail)

    with pytest.raises(HTTPException) as excinfo:
        validate_zip(
            _zip({"plugin.yaml": "name: x\ntools: [{name: t}]\ninstall_command: pip install x\n"}),
            "mcp", "hosted",
        )
    assert "依赖安装命令" in str(excinfo.value.detail)


def test_unsafe_path_and_symlink_still_rejected():
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(_skill_zip({"../escape.py": "x"}), "skill", "instruction")
    assert "不安全路径" in str(excinfo.value.detail)

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("plugin.yaml", SKILL_YAML)
        archive.writestr("SKILL.md", "# x\n")
        link = zipfile.ZipInfo("scripts/link.py")
        link.external_attr = (0o120777 << 16)
        archive.writestr(link, "/etc/passwd")
    with pytest.raises(HTTPException) as excinfo:
        validate_zip(buffer.getvalue(), "skill", "instruction")
    assert "符号链接" in str(excinfo.value.detail)
