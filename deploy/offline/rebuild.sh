#!/bin/bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

component="${1:-}"
image_tag="${DWP_IMAGE_TAG:-0.3.0-offline}"
source_root="${DWP_SOURCE_ROOT:-$PWD/source}"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

[ -d "$source_root/backend" ] || fail "找不到源码目录：$source_root"
docker info >/dev/null 2>&1 || fail "当前用户无法访问 Docker"

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

rebuild_dsh_profile() {
  backup_tag "dwp-dsh:rc6" "dwp-dsh"
  docker build --network=none \
    -f "$source_root/deploy/offline/rebuild/Dockerfile.dsh-profile-hotfix" \
    -t dwp-dsh:rc6 "$source_root"
  docker compose up -d --no-deps --force-recreate \
    harness-ai-general harness-ai-investment harness-ai-research harness-ai-fund harness-ai-xyzq harness-dt-e10281 harness-dt-e20999
}

case "$component" in
  backend) rebuild_backend ;;
  frontend) rebuild_frontend ;;
  harness-profile) rebuild_dsh_profile ;;
  all)
    rebuild_backend
    rebuild_frontend
    rebuild_dsh_profile
    ;;
  *)
    echo "用法: ./rebuild.sh backend|frontend|harness-profile|all" >&2
    exit 2
    ;;
esac

echo "离线重建完成：$component"
docker compose ps
