#!/bin/bash
# dwp AI员工平台 —— 用当前源码重建组件
#
# 用法:
#   ./rebuild.sh backend            重建后端镜像并重建 backend 容器
#   ./rebuild.sh frontend           重建前端镜像并重建 frontend 容器
#   ./rebuild.sh harness-profile    切到当前 Profile 版本（不再重建镜像，委托 ops/profile-switch.sh）
#   ./rebuild.sh runtime [version]  生成/更新 dsh runtime 版本目录（委托 ops/dsh-install-runtime.sh）
#   ./rebuild.sh all                重建 backend + frontend（不再触碰 harness，避免把框架悄悄打回旧版本）
#   ./rebuild.sh dsh-image          已废弃：重建 dwp-dsh 底座镜像（仅历史兼容）
#
# 环境变量:
#   DWP_IMAGE_TAG    镜像 tag（本脚本不读 .env，必须显式 export 或依赖调用方导出）
#   DWP_SOURCE_ROOT  源码根目录（本脚本已 cd 到 deploy/offline，因此必须传 ../..）
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=ops/lib.sh
source ./ops/lib.sh

component="${1:-}"
image_tag="${DWP_IMAGE_TAG:-0.3.0-offline}"
source_root="${DWP_SOURCE_ROOT:-$PWD/source}"

[ -d "$source_root/backend" ] || { echo "ERROR: 找不到源码目录：$source_root（提示：DWP_SOURCE_ROOT=../..）" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: 当前用户无法访问 Docker" >&2; exit 1; }

backup_tag() {
  local image="$1" repository="$2"
  if docker image inspect "$image" >/dev/null 2>&1; then
    docker tag "$image" "${repository}:rollback-$(date +%Y%m%d%H%M%S)"
  fi
}

rebuild_backend() {
  backup_tag "dwp-backend:${image_tag}" "dwp-backend"
  docker build --network=none \
    --build-arg "DWP_IMAGE_TAG=${image_tag}" \
    -f "$source_root/deploy/offline/rebuild/Dockerfile.backend-hotfix" \
    -t "dwp-backend:${image_tag}" "$source_root"
  docker compose up -d --no-deps --force-recreate backend
}

rebuild_frontend() {
  backup_tag "dwp-frontend:${image_tag}" "dwp-frontend"
  docker build --network=none \
    --build-arg "DWP_IMAGE_TAG=${image_tag}" \
    -f "$source_root/deploy/offline/rebuild/Dockerfile.frontend-hotfix" \
    -t "dwp-frontend:${image_tag}" "$source_root"
  docker compose up -d --no-deps --force-recreate frontend
}

# Profile 层切换（替代旧的「重建 dwp-dsh 镜像」路径）
rebuild_dsh_profile() {
  local rev legacy_diff
  rev="$(versions_value DWP_PROFILE_REV 'v1-20260910')"
  echo "提示：harness-profile 已改为 Profile 层切换，不再重建 dwp-dsh 镜像。"
  echo "      真源目录：deploy/offline/profile/$rev（docker/dsh-profile 已标记为历史备份）"
  if [ -d "$source_root/docker/dsh-profile" ] && [ -d "$PROFILE_ROOT/$rev" ]; then
    legacy_diff="$(diff -rq --exclude='README*' --exclude='DEPRECATED*' --exclude='MANIFEST.json' \
      "$source_root/docker/dsh-profile" "$PROFILE_ROOT/$rev" 2>/dev/null || true)"
    if [ -n "$legacy_diff" ]; then
      warn "docker/dsh-profile 与 profile/$rev 已不一致："
      printf '        %s\n' "$legacy_diff"
      warn "如需以旧目录为准，请执行：./ops/profile-switch.sh --new-rev <新版本名> --from docker/dsh-profile"
    fi
  fi
  "$OPS_DIR/profile-switch.sh" "$rev" --all
}

# 生成/更新 dsh runtime 版本目录
rebuild_runtime() {
  local version="${1:-$(versions_value DWP_DSH_RUNTIME_VERSION '')}"
  [ -n "$version" ] || { echo "ERROR: 请指定版本，例如 ./rebuild.sh runtime 0.1.5-rc.1" >&2; exit 2; }
  "$OPS_DIR/dsh-install-runtime.sh" "$version"
}

# 已废弃：底座镜像重建（保留仅为不打断 run_demo.ps1 / package-offline.ps1 等历史路径）
rebuild_dsh_image() {
  warn "dsh-image 已废弃：底座镜像应冻结复用，dsh 版本改由 runtime/ 目录管理。"
  warn "如需新增版本：./ops/dsh-install-runtime.sh <version>"
  echo "若确实要重建底座镜像，请手工执行：" >&2
  echo "  docker build -f $source_root/docker/Dockerfile.dsh -t dwp-dsh:rc6 $source_root" >&2
  exit 2
}

case "$component" in
  backend) rebuild_backend ;;
  frontend) rebuild_frontend ;;
  harness-profile) rebuild_dsh_profile ;;
  runtime) shift; rebuild_runtime "${1:-}" ;;
  all)
    rebuild_backend
    rebuild_frontend
    ;;
  dsh-image) rebuild_dsh_image ;;
  *)
    echo "用法: ./rebuild.sh backend|frontend|harness-profile|runtime [version]|all|dsh-image" >&2
    exit 2
    ;;
esac

echo "重建完成：$component"
docker compose ps
