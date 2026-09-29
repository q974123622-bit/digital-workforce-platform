"""harness 员工白名单的「单一来源」守卫。

这份名单原先散在 6 处，每加一个岗位都要逐处补，漏一处就是静默故障。现在 Python 侧
只读 `employee_registry`，但 compose.yaml / ops/lib.sh / versions.env 是声明式的、
不能 import Python —— 所以用这个测试把它们钉在一起：改名单不改声明式文件，测试立刻红。
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.services import employee_registry as registry
from app.services import knowledge_catalog
from app import seed


def _find_offline_dir() -> Path | None:
    """测试可能跑在 backend 容器里（没有 deploy/），找不到就跳过。

    容器里跑时把仓库根挂到 /repo，声明式文件就从那里读。
    """
    for base in (Path(__file__).resolve(), Path("/app"), Path("/repo"), Path("/workspace")):
        for parent in [base, *base.parents]:
            candidate = parent / "deploy" / "offline"
            if (candidate / "compose.yaml").is_file():
                return candidate
    return None


OFFLINE = _find_offline_dir()
requires_offline = pytest.mark.skipif(OFFLINE is None, reason="找不到 deploy/offline（容器内跑测试时正常）")


def test_harness_employees_include_audit():
    assert "AI-AUDIT" in registry.HARNESS_EMPLOYEE_IDS
    assert registry.is_harness_employee("AI-AUDIT")
    assert registry.is_harness_employee("DT-E10281")
    assert not registry.is_harness_employee("AI-NOPE")
    # 岗位员工与分身不重名
    assert not set(registry.ROLE_EMPLOYEE_IDS) & set(registry.TWIN_EMPLOYEE_IDS)


def test_seed_covers_every_harness_role_employee():
    """岗位员工必须在 seed 的 employee_specs 里，否则库里没有这个人。"""
    source = Path(seed.__file__).read_text(encoding="utf-8")
    for employee_id in registry.ROLE_EMPLOYEE_IDS:
        assert f'"{employee_id}":' in source, f"{employee_id} 未在 seed 的 employee_specs 中定义"
        assert f'"/avatars/{employee_id.lower()}.webp"' in source, f"{employee_id} 缺少头像配置"


def test_grant_specs_cover_all_and_stay_disjoint():
    specs = knowledge_catalog.GRANT_SPECS
    assert set(registry.HARNESS_EMPLOYEE_IDS) <= set(specs), "有员工没有知识库授权条目"
    ids = sorted(specs)
    for i, left in enumerate(ids):
        for right in ids[i + 1:]:
            overlap = set(specs[left]) & set(specs[right])
            assert not overlap, f"{left} 与 {right} 的知识库授权重叠：{sorted(overlap)}"


@requires_offline
def test_compose_services_match_registry():
    text = (OFFLINE / "compose.yaml").read_text(encoding="utf-8")
    services = set(re.findall(r"^  (harness-[a-z0-9-]+):", text, re.M))
    assert services == set(registry.harness_service_names()), (
        "compose.yaml 的 harness 服务与 employee_registry 不一致："
        f"仅 compose 有 {sorted(services - set(registry.harness_service_names()))}，"
        f"仅 registry 有 {sorted(set(registry.harness_service_names()) - services)}")


@requires_offline
def test_compose_harness_services_have_all_mounts():
    """`<<:` 是浅合并：每个 harness 服务必须显式列全 4 条挂载。"""
    text = (OFFLINE / "compose.yaml").read_text(encoding="utf-8")
    blocks = re.split(r"^  harness-", text, flags=re.M)[1:]
    for block in blocks:
        name = block.split(":", 1)[0]
        for mount in ("/opt/dsh-runtime:ro", "/opt/dwp/profile:ro", "/dsh-home", "/workspace"):
            assert mount in block, f"harness-{name} 缺少挂载 {mount}"


@requires_offline
def test_ops_lib_and_versions_match_registry():
    lib = (OFFLINE / "ops" / "lib.sh").read_text(encoding="utf-8")
    match = re.search(r"^DWP_EMPLOYEE_IDS=\(([^)]*)\)", lib, re.M)
    assert match, "ops/lib.sh 里找不到 DWP_EMPLOYEE_IDS"
    listed = match.group(1).split()
    assert listed == list(registry.ROLE_EMPLOYEE_IDS) + list(registry.TWIN_EMPLOYEE_IDS), (
        f"ops/lib.sh 的员工名单与 employee_registry 不一致：{listed}")

    versions = (OFFLINE / "versions.env").read_text(encoding="utf-8")
    for employee_id in listed:
        key = "DWP_DSH_RUNTIME_" + employee_id.upper().replace("-", "_")
        assert re.search(rf"^{key}=", versions, re.M), f"versions.env 缺少 {key}"


@requires_offline
def test_start_sh_has_no_hardcoded_employee_list():
    """start.sh 历史上漏过 AI-XYZQ：名单必须读数组，不能硬编码。"""
    text = (OFFLINE / "start.sh").read_text(encoding="utf-8")
    assert "for employee_id in \"${DWP_EMPLOYEE_IDS[@]}\"; do" in text
    for employee_id in registry.HARNESS_EMPLOYEE_IDS:
        assert f"harness/{employee_id}/" not in text, f"start.sh 仍在硬编码 {employee_id}"
