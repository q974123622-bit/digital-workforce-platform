#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
docker compose down
echo "平台与四个 Harness 实例已停止；data、backups 和员工工作区均保留。"
