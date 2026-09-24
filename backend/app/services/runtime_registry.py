"""Harness / 平台镜像与版本的只读查询。

页面要回答两个问题：
  1. 这个AI员工跑在哪个底座镜像、哪个 dsh 框架版本？
  2. 这套平台自己是由哪几个镜像、哪个交付版本组成的？

数据源优先级：
  1. **Docker Engine 的容器标签**（`dwp.managed=true`）。compose.yaml 把 `dwp.dsh.runtime`
     （per-employee，灰度切换 `ops/dsh-switch.sh <ID> <runtime>` 只改这一个变量、不动全局
     `DWP_DSH_RUNTIME_VERSION`）与 `dwp.dsh.profile` 打成标签，容器里实际挂载的就是它 ——
     这是唯一能反映「按AI员工灰度」的真相，镜像名也直接取容器自己的 `Image` 字段。
  2. **环境变量兜底**。backend 容器由 compose 注入 `DWP_HARNESS_IMAGE` /
     `DWP_DSH_RUNTIME_VERSION` / `DWP_PROFILE_REV` / `DWP_IMAGE_TAG`；Docker socket 不可达
     （例如未配置 `group_add: DWP_DOCKER_GID`）时仍能显示「部署声明的版本」。

环境信息是只读增强，任何查询失败都必须降级、绝不能让 /agents 列表接口报错。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from .docker_engine import DockerEngineClient

MANAGED_LABEL = "dwp.managed=true"
# 查环境信息不能拖慢列表页：socket 无响应时 3 秒内放弃并走兜底。
PROBE_TIMEOUT = 3

# runtime 目录名不是版本号的情况（软链指针 / CLI 分支创建的容器）
_NON_VERSION_NAMES = {"", ".", "current", "image-baked"}
_RUNTIME_PREFIX = "dsh-"


@dataclass(frozen=True)
class HarnessEnvironment:
    """某个AI员工 Harness 容器的镜像与框架版本。"""

    image: str
    runtime_version: str
    profile_rev: str


def harness_image() -> str:
    return os.getenv("DWP_HARNESS_IMAGE", "").strip() or "dwp-dsh:rc6"


def declared_version() -> str:
    """部署声明的全局 dsh 版本（`versions.env` 的 `DWP_DSH_RUNTIME_VERSION`）。"""
    return os.getenv("DWP_DSH_RUNTIME_VERSION", "").strip()


def declared_profile_rev() -> str:
    return os.getenv("DWP_PROFILE_REV", "").strip()


def declared_delivery_tag() -> str:
    return os.getenv("DWP_IMAGE_TAG", "").strip()


def version_of_runtime(runtime_path: str) -> str:
    """把 runtime 目录还原成 dsh 版本号。

    目录命名约定见 `ops/lib.sh::set_runtime_pointer`（`dsh-<version>`），例如
    `./runtime/dsh-0.1.5-rc.1` -> `0.1.5-rc.1`；指向 `current` 软链或取不到目录名时，
    退回「部署声明的版本」。
    """
    name = Path(runtime_path.strip()).name
    if name in _NON_VERSION_NAMES:
        return declared_version()
    return name[len(_RUNTIME_PREFIX):] if name.startswith(_RUNTIME_PREFIX) else name


def fallback_environment() -> HarnessEnvironment:
    """Docker 不可达时的兜底：全部取部署声明的值。"""
    return HarnessEnvironment(harness_image(), declared_version(), declared_profile_rev())


def harness_environments() -> dict[str, HarnessEnvironment]:
    """按 employee_id 返回 Harness 容器的镜像与版本；Docker 不可达时返回空字典。

    只做一次容器列表查询（不逐容器 inspect），7 个 Harness 也只是一次调用。
    """
    try:
        engine = DockerEngineClient(timeout=PROBE_TIMEOUT)
        try:
            containers = engine.list_containers(MANAGED_LABEL)
        finally:
            engine.close()
    except Exception:  # noqa: BLE001 - 只读增强，查不到就降级为声明值
        return {}

    environments: dict[str, HarnessEnvironment] = {}
    for item in containers:
        labels = item.get("Labels") or {}
        employee_id = str(labels.get("dwp.employee_id") or "").strip()
        if not employee_id:
            continue
        environments[employee_id] = HarnessEnvironment(
            image=str(item.get("Image") or harness_image()),
            runtime_version=version_of_runtime(str(labels.get("dwp.dsh.runtime") or "")),
            profile_rev=str(labels.get("dwp.dsh.profile") or declared_profile_rev()),
        )
    return environments


def environment_for(employee_id: str, environments: dict[str, HarnessEnvironment]) -> HarnessEnvironment:
    return environments.get(employee_id) or fallback_environment()


def platform_images() -> list[dict[str, str]]:
    """顶部汇总用的镜像清单：平台两件套 + Harness 底座。"""
    tag = declared_delivery_tag()
    harness_note = " · ".join(
        part for part in (
            f"dsh {declared_version()}" if declared_version() else "",
            f"profile {declared_profile_rev()}" if declared_profile_rev() else "",
        ) if part
    )
    return [
        {"role": "后端服务", "name": f"dwp-backend:{tag}" if tag else "dwp-backend", "note": ""},
        {"role": "前端控制台", "name": f"dwp-frontend:{tag}" if tag else "dwp-frontend", "note": ""},
        {"role": "Harness 底座", "name": harness_image(), "note": harness_note},
    ]
