#!/bin/bash
# dwp AI员工平台 —— dsh 框架版本切换（支持按AI员工灰度）
#
# 流程（任一步失败自动回滚到切换前状态）：
#   1) 门禁：优先复用 runtime/<name>/GATE.json，缺失或 --force 时现场跑 dsh-preflight.sh
#   2) 预演：dsh-canary.sh 真实跑两轮（纯文本 + 真跑本地工具），校验 stdout 契约
#   3) 记录回退指针：快照 versions.env + 记录本次影响的实例
#   4) 改 versions.env（全局键或单个AI员工的覆盖键）并重新展开 .env
#   5) 仅重建目标 harness 容器
#   6) 冒烟：挂载源 / dsh 版本 / bundle 软链 / 容器内合成树契约
#
# 用法:
#   ./ops/dsh-switch.sh AI-GENERAL dsh-0.1.5-rc.1     # 灰度：只切 AI-GENERAL
#   ./ops/dsh-switch.sh --all dsh-0.1.5-rc.1          # 全量切换
#   ./ops/dsh-switch.sh --all dsh-0.1.5-rc.1 --skip-canary
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

TARGET_ARG=""; RUNTIME_ARG=""; SKIP_PREFLIGHT=0; SKIP_CANARY=0; FORCE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --all)            TARGET_ARG="--all"; shift ;;
    --skip-preflight) SKIP_PREFLIGHT=1; shift ;;
    --skip-canary)    SKIP_CANARY=1; shift ;;
    --force)          FORCE=1; shift ;;
    -h|--help)        sed -n '2,20p' "$0"; exit 0 ;;
    -*)               bad "未知参数: $1" ;;
    *) if [ -z "$TARGET_ARG" ]; then TARGET_ARG="$1"; else RUNTIME_ARG="$1"; fi; shift ;;
  esac
done

[ -n "$TARGET_ARG" ] && [ -n "$RUNTIME_ARG" ] || \
  bad "用法: ./ops/dsh-switch.sh <EMPLOYEE_ID|--all> <runtime-dir|version> [--skip-preflight] [--skip-canary] [--force]"

RUNTIME_DIR="$(runtime_dir_of "$RUNTIME_ARG")"
RUNTIME_NAME="$(basename "$RUNTIME_DIR")"
RUNTIME_VERSION="$(runtime_version_of "$RUNTIME_DIR")"
PROFILE_REL="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
EXPECTED_MODEL="$(awk -F= '$1=="DEEPSEEK_MODEL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  "$OFFLINE_DIR/config.env" 2>/dev/null || true)"
EXPECTED_MODEL="${EXPECTED_MODEL:-ascend-qwen3.6-27b}"

if [ "$TARGET_ARG" = "--all" ]; then
  MODE=all
  TARGETS=("${DWP_EMPLOYEE_IDS[@]}")
else
  MODE=single
  TARGETS=("$(normalize_employee_id "$TARGET_ARG")")
  is_employee_id "${TARGETS[0]}" || bad "未知AI员工：$TARGET_ARG（可选：${DWP_EMPLOYEE_IDS[*]}）"
fi

require_docker
require_compose_file
umask 077
mkdir -p -m 0700 "$OPS_LOG_DIR" "$OPS_DIR/state"
LOG="$(new_log switch)"
exec > >(tee -a "$LOG") 2>&1

hdr "0/6 切换计划"
info "模式      : $([ "$MODE" = all ] && echo 全量 || echo 灰度)"
info "目标实例  : ${TARGETS[*]}"
info "目标版本  : $RUNTIME_NAME ($RUNTIME_VERSION)"
info "当前全局  : $(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
info "日志      : $LOG"

hdr "1/6 门禁"
if [ "$SKIP_PREFLIGHT" -eq 1 ]; then
  warn "--skip-preflight：跳过静态契约门禁（不推荐）"
else
  gate_ok=0
  if [ "$FORCE" -eq 0 ] && [ -f "$RUNTIME_DIR/GATE.json" ] && grep -q '"ok"[[:space:]]*:[[:space:]]*true' "$RUNTIME_DIR/GATE.json"; then
    gate_ok=1
    ok "复用已有 GATE.json 通过记录"
  fi
  if [ "$gate_ok" -eq 0 ]; then
    info "现场执行 dsh-preflight.sh（--force 或缺少 GATE.json）"
    "$OPS_DIR/dsh-preflight.sh" "$RUNTIME_NAME" || bad "门禁不通过，已终止切换"
  fi
fi

hdr "2/6 预演（真实跑一轮）"
if [ "$SKIP_CANARY" -eq 1 ]; then
  warn "--skip-canary：跳过真实执行预演（不推荐）"
else
  "$OPS_DIR/dsh-canary.sh" "$RUNTIME_NAME" || bad "预演不通过，已终止切换"
fi

hdr "3/6 记录回退指针"
cp -f "$VERSIONS_FILE" "$OPS_DIR/state/versions.env.previous"
chmod 600 "$OPS_DIR/state/versions.env.previous"
{
  printf 'SWITCH_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  printf 'SWITCH_MODE=%s\n' "$MODE"
  printf 'SWITCH_FROM_RUNTIME=%s\n' "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
  printf 'SWITCH_TO_RUNTIME=./runtime/%s\n' "$RUNTIME_NAME"
  printf 'SWITCH_EMPLOYEES=%s\n' "${TARGETS[*]}"
  printf 'SWITCH_SERVICES=%s\n' "$(for id in "${TARGETS[@]}"; do printf '%s ' "$(service_for "$id")"; done)"
} > "$OPS_DIR/state/last-switch.env"
chmod 600 "$OPS_DIR/state/last-switch.env"
ok "已快照 versions.env 到 ops/state/versions.env.previous"

services=()
for id in "${TARGETS[@]}"; do services+=("$(service_for "$id")"); done

ROLLBACK_ARMED=0
rollback_now() {
  local reason="$1"
  ROLLBACK_ARMED=0
  warn "切换失败（$reason），执行自动回滚……"
  cp -f "$OPS_DIR/state/versions.env.previous" "$VERSIONS_FILE"
  sync_env_from_versions
  docker compose up -d --no-deps --force-recreate "${services[@]}" || warn "回滚重建本身也失败，请人工介入"
  for id in "${TARGETS[@]}"; do smoke_harness "$id" "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')" || true; done
  echo "回滚日志：$LOG" >&2
  exit 1
}

# versions.env 一旦被改写，任何意外失败都必须回到切换前状态，否则会留下
# 「.env 指向新版本、容器仍跑旧版本」的不一致状态。
on_unexpected_error() {
  local rc=$?
  if [ "$ROLLBACK_ARMED" -eq 1 ]; then
    rollback_now "意外错误（exit=$rc）"
  fi
  exit "$rc"
}
trap on_unexpected_error ERR

hdr "4/6 更新 versions.env 与 .env"
ROLLBACK_ARMED=1
if [ "$MODE" = all ]; then
  set_runtime_pointer "$RUNTIME_NAME" "$RUNTIME_VERSION"
  for id in "${DWP_EMPLOYEE_IDS[@]}"; do set_versions_value "$(runtime_key_for "$id")" ""; done
  ok "全局 runtime -> ./runtime/$RUNTIME_NAME；已清空全部 per-employee 覆盖"
else
  for id in "${TARGETS[@]}"; do
    set_versions_value "$(runtime_key_for "$id")" "./runtime/$RUNTIME_NAME"
  done
  ok "灰度覆盖：$(for id in "${TARGETS[@]}"; do printf '%s -> ./runtime/%s  ' "$id" "$RUNTIME_NAME"; done)"
fi
sync_env_from_versions
ok ".env 已按 versions.env 重新展开"

hdr "5/6 重建目标 harness 容器"
# 清理旧版本 dsh 留下的 bundle 软链农场（派生状态，dsh 会按新 runtime 重建）
for id in "${TARGETS[@]}"; do purge_bundle_farm "$id"; done
docker compose up -d --no-deps --force-recreate "${services[@]}" || rollback_now "容器重建失败"
ok "已重建：${services[*]}"

hdr "6/6 冒烟校验（实际生效）"
failures=0
for id in "${TARGETS[@]}"; do
  if smoke_harness "$id" "$RUNTIME_DIR" "$EXPECTED_MODEL"; then
    :
  else
    failures=$((failures + 1))
  fi
done
if [ "$failures" -ne 0 ]; then
  rollback_now "$failures 个实例冒烟不通过"
fi

ok "切换完成：$([ "$MODE" = all ] && echo 全量 || echo 灰度 ${TARGETS[*]}) -> $RUNTIME_VERSION"
echo
echo "查看状态：./ops/dsh-status.sh"
echo "回滚：    ./ops/dsh-rollback.sh"
echo "日志：    $LOG"
