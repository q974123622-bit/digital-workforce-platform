#!/bin/bash
# dwp AI员工平台 —— 一键部署（离线 / 无外网）
#
# 上传到堡垒机后，一条命令跑完「解包 → 预检 → 导入镜像 → 启动 → 用源码重建 → 自检」。
#
# 用法:
#   bash install.sh <dwp-ai-employee-platform-bastion-*.tar.gz> [安装目录] [deploy.sh 额外参数...]
#
# 例:
#   bash install.sh /tmp/dwp-ai-employee-platform-bastion-20260922-1700.tar.gz
#   bash install.sh ./pkg.tar.gz /app/dwp --port 18081
#
# 安装目录默认 /app/dwp-ai-employee-platform（/app 不可写时回落 $HOME/dwp-ai-employee-platform）。
# 目标机 /home 通常只有 10G 左右，优先落在 /app 这类大盘上更稳妥。
#
# 可选环境变量:
#   DWP_HTTP_PORT   入口端口，默认 18080（也可用 deploy.sh 的 --port 覆盖）
#
set -Eeuo pipefail

ARCHIVE=""
INSTALL_DIR=""
if [ $# -gt 0 ]; then ARCHIVE="$1"; shift; fi
if [ $# -gt 0 ]; then INSTALL_DIR="$1"; shift; fi

if [ -z "$ARCHIVE" ]; then
  sed -n '2,17p' "$0"
  exit 2
fi
[ -f "$ARCHIVE" ] || { echo "ERROR: 找不到安装包 $ARCHIVE" >&2; exit 1; }
ARCHIVE="$(cd "$(dirname "$ARCHIVE")" && pwd)/$(basename "$ARCHIVE")"

command -v docker >/dev/null 2>&1 || { echo "ERROR: 未安装 Docker" >&2; exit 1; }
docker info >/dev/null 2>&1 || { echo "ERROR: 当前用户无法访问 Docker（未启动或不在 docker 组）" >&2; exit 1; }
docker compose version >/dev/null 2>&1 || { echo "ERROR: 未安装 Docker Compose v2" >&2; exit 1; }
[ "$(uname -m)" = "x86_64" ] || { echo "ERROR: 架构 $(uname -m)，离线包是 linux/amd64" >&2; exit 1; }

if [ -z "$INSTALL_DIR" ]; then
  if mkdir -p /app/dwp-ai-employee-platform 2>/dev/null && [ -w /app/dwp-ai-employee-platform ]; then
    INSTALL_DIR=/app/dwp-ai-employee-platform
  else
    INSTALL_DIR="$HOME/dwp-ai-employee-platform"
  fi
fi
mkdir -p "$INSTALL_DIR"
INSTALL_DIR="$(cd "$INSTALL_DIR" && pwd)"

# 解包后约 1.5G，压缩包约 500M；容器镜像另有 6G 以上，落在 Docker 数据盘（/app/docker）。
avail_kb="$(df -Pk "$INSTALL_DIR" | awk 'NR==2{print $4}')"
if [ "${avail_kb:-0}" -lt 3145728 ]; then
  echo "ERROR: $INSTALL_DIR 可用空间不足 3G（当前 $((avail_kb/1024))M）" >&2
  exit 1
fi

echo "== 安装目录 : $INSTALL_DIR =="
echo "== 安装包   : $ARCHIVE =="
echo "== 解包中（约 1.5G，需要一会儿）=="
tar -xzf "$ARCHIVE" -C "$INSTALL_DIR" --strip-components=1
[ -f "$INSTALL_DIR/deploy/offline/deploy.sh" ] || { echo "ERROR: 包结构不对，缺少 deploy/offline/deploy.sh" >&2; exit 1; }
chmod +x "$INSTALL_DIR"/deploy/offline/*.sh "$INSTALL_DIR"/deploy/offline/ops/*.sh 2>/dev/null || true

cd "$INSTALL_DIR/deploy/offline"
echo
echo "== 开始一键部署（首次约 10~20 分钟：导入镜像 + 用源码重建 backend/frontend）=="
exec ./deploy.sh "$@"
