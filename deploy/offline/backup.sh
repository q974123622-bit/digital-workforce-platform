#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
mkdir -p backups
stamp="$(date +%Y%m%d_%H%M%S)"
target="/backups/dwp_${stamp}.db"
docker compose exec -T backend python - "$target" <<'PY'
import sqlite3
import sys

source = sqlite3.connect('/data/dwp.db')
target = sqlite3.connect(sys.argv[1])
with target:
    source.backup(target)
target.close()
source.close()
print(sys.argv[1])
PY
chmod 600 "backups/dwp_${stamp}.db"
echo "备份完成: backups/dwp_${stamp}.db"
