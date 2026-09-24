#!/bin/bash
# dwp AI员工平台 —— 基础自检
#
# 用法:
#   ./verify.sh            基础自检（compose 配置 / 后端 / 前端 / 7 个 harness 的 runtime+profile
#                          + harness 的 python3 与 /workspace 可写 + SkillHub 目录可达）
#   ./verify.sh --deep     额外在每个 harness 容器内合成配置树并跑契约校验（较慢）
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=ops/lib.sh
source ./ops/lib.sh

DEEP=0
while [ $# -gt 0 ]; do
  case "$1" in
    --deep) DEEP=1; shift ;;
    -h|--help) sed -n '2,8p' "$0"; exit 0 ;;
    *) bad "未知参数: $1" ;;
  esac
done

failures=0
ok_line()  { printf 'OK   %s\n' "$*"; }
bad_line() { printf 'FAIL %s\n' "$*" >&2; failures=$((failures + 1)); }

check() {
  local label="$1"; shift
  if "$@" >/dev/null 2>&1; then ok_line "$label"; else bad_line "$label"; fi
}

EXPECTED_MODEL="$(awk -F= '$1=="DEEPSEEK_MODEL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  config.env 2>/dev/null || true)"
EXPECTED_MODEL="${EXPECTED_MODEL:-ascend-qwen3.6-27b}"

check "docker compose config" docker compose config --quiet
check "backend /health" docker compose exec -T backend python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3)"
check "frontend /health" docker compose exec -T frontend wget -q -O /dev/null http://127.0.0.1:8080/health

for id in "${DWP_EMPLOYEE_IDS[@]}"; do
  container="$(container_for "$id")"
  check "$container 运行中" test "$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || echo false)" = "true"

  check "$container runtime 挂载（$DSH_RUNTIME_MOUNT/bin/dsh）" \
    docker exec "$container" test -x "$DSH_RUNTIME_MOUNT/bin/dsh"
  check "$container dsh 可解析" \
    docker exec "$container" sh -c 'command -v dsh >/dev/null'
  check "$container profile 已同步" \
    docker exec "$container" test -f "/dsh-home/profiles/$DSH_PROFILE_NAME/cordis.yml"
  # 技能脚本（SkillHub 导入的 `python scripts/...`）要能真跑：底座镜像必须带 python3，
  # 且 /workspace 可写（沙箱根 = working_dir = /workspace）。
  check "$container python3 可用（技能脚本）" \
    docker exec "$container" sh -c 'command -v python3 >/dev/null'
  check "$container /workspace 可写" \
    docker exec "$container" sh -c 'cd /workspace && touch .dwp-write-probe && rm -f .dwp-write-probe'

  # 实际生效的 dsh 版本必须与 .env 里该员工期望的 runtime 一致
  expected_dir="$(runtime_dir_of "$(env_value "$(runtime_key_for "$id")" "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')")")"
  expected_version="$(runtime_version_of "$expected_dir")"
  actual_version="$(harness_runtime_version "$container")"
  if [ "$actual_version" = "$expected_version" ]; then
    ok_line "$container dsh 版本 $actual_version"
  else
    bad_line "$container dsh 版本 ${actual_version:-<空>}，期望 $expected_version"
  fi

  mount_src="$(harness_mount_source "$container")"
  if [ "$mount_src" = "$expected_dir" ]; then
    ok_line "$container runtime 挂载源 ${mount_src##*/}"
  else
    bad_line "$container runtime 挂载源 ${mount_src:-<空>}，期望 $expected_dir"
  fi

  if [ "$DEEP" -eq 1 ]; then
    if harness_contract_check "$container" "$EXPECTED_MODEL"; then
      ok_line "$container 容器内合成树契约校验"
    else
      bad_line "$container 容器内合成树契约校验（docker exec $container cat /tmp/dsh-gate/dump.err）"
    fi
  fi
done

# 知识源可达性：REST（测试）与 VeRAG MCP（生产）至少一条通道可用，
# 结果会写入 knowledge_source_health，管理端「数据源状态」与AI员工工具清单同步生效。
echo
echo "== 知识源可达性（内部知识引擎 REST / VeRAG MCP）=="
if docker compose exec -T backend python -m tools.check_knowledge_sources; then
  ok_line "知识通道可达"
else
  bad_line "两条知识通道都不可达（检查 config.env 的 DWP_INTERNAL_KB_* / DWP_VERAG_MCP_* 与 secrets/verag_mcp_token）"
fi

# SkillHub 技能市场：目录可达即可（导入按需在管理端「技能市场」页签操作）。
# 未接入（DWP_SKILLHUB_BASE_URL 为空，或 DWP_SKILLHUB_ENABLED=0）不算失败：
# 管理端「技能市场」页签根本不渲染，平台其余功能与它无关。
echo
echo "== SkillHub 技能市场（内网技能货架）=="
SKILLHUB_URL_VALUE="$(awk -F= '$1=="DWP_SKILLHUB_BASE_URL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  config.env 2>/dev/null || true)"
SKILLHUB_FLAG_VALUE="$(awk -F= '$1=="DWP_SKILLHUB_ENABLED"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  config.env 2>/dev/null || true)"
if [ -z "$SKILLHUB_URL_VALUE" ] || [ "$SKILLHUB_FLAG_VALUE" = "0" ]; then
  ok_line "未接入 SkillHub（已关闭），管理端不显示「技能市场」页签"
elif docker compose exec -T backend python -m tools.skillhub_sync --dry-run --limit 1 >/dev/null 2>&1; then
  ok_line "SkillHub 目录可达（DWP_SKILLHUB_BASE_URL 已配置且可访问）"
else
  bad_line "SkillHub 目录不可达：检查 config.env 的 DWP_SKILLHUB_BASE_URL（离线环境不通时置空即可）"
fi

# 兴证e家员工服务 MCP：只校验应用级鉴权（tools/list 可达）；按人的 X-MCP-JWT
# 由员工在会话内的「信息补全」气泡里粘贴，运维侧无从代填，因此不在这里检查。
# 未配置 DWP_EHOME_MCP_URL 不算失败：平台不会有该插件，其它功能不受影响。
echo
echo "== 兴证e家员工服务 MCP（内网员工服务）=="
EHOME_URL_VALUE="$(awk -F= '$1=="DWP_EHOME_MCP_URL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  config.env 2>/dev/null || true)"
if [ -z "$EHOME_URL_VALUE" ]; then
  ok_line "未配置 DWP_EHOME_MCP_URL，跳过（不影响其它功能）"
elif docker compose exec -T backend python -m tools.check_ehome_mcp >/dev/null 2>&1; then
  ok_line "兴证e家 MCP 可达（应用级鉴权头有效，工具清单可获取）"
else
  bad_line "兴证e家 MCP 不可达：检查 config.env 的 DWP_EHOME_MCP_URL 与 secrets/ehome_mcp_api_key（或防火墙到 10.32.253.43:80）"
fi

if [ "$failures" -ne 0 ]; then
  echo "自检失败项: $failures" >&2
  exit 1
fi
echo "全部基础自检通过（dsh runtime/profile 三层挂载与版本一致，harness 具备技能脚本执行能力）。请登录页面分别向两个AI员工发起一次知识问答。"
