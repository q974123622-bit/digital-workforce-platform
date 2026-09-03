#!/bin/bash
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"

failures=0
check() {
  if "$@"; then
    printf 'OK   %s\n' "$*"
  else
    printf 'FAIL %s\n' "$*" >&2
    failures=$((failures + 1))
  fi
}

check docker compose config --quiet
check docker compose exec -T backend python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
check docker compose exec -T frontend wget -q -O /dev/null http://127.0.0.1:8080/health
for container in dwp-harness-ai-general dwp-harness-ai-investment dwp-harness-dt-e10281 dwp-harness-dt-e20999; do
  check docker exec "$container" sh -c 'command -v dsh >/dev/null && test -f /dsh-home/profiles/dwp-knowledge-agent-v2/cordis.yml'
done

if [ "$failures" -ne 0 ]; then
  echo "自检失败项: $failures" >&2
  exit 1
fi
echo "全部基础自检通过。请登录页面分别向两个数字员工发起一次知识问答。"
