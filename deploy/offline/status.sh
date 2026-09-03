#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

docker compose ps
echo
echo "Harness 实例："
docker ps --filter 'label=dwp.managed=true' --format 'table {{.Names}}\t{{.Status}}\t{{.Label "dwp.employee_id"}}'
echo
docker compose logs --tail=50 backend frontend
