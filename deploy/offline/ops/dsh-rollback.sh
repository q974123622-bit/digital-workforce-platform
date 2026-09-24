#!/bin/bash
# dwp AI员工平台 —— dsh 框架版本秒级回滚
#
# 把 versions.env 恢复到上一次 dsh-switch.sh 之前的状态，只重建当时受影响的实例。
# 旧版本 runtime 目录长期保留在 runtime/ 下，因此回滚不需要任何镜像构建或网络。
#
# 用法:
#   ./ops/dsh-rollback.sh            # 回滚到上一次切换前的版本
#   ./ops/dsh-rollback.sh --status   # 只查看回退指针与上一次切换记录
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

SHOW_STATUS=0
while [ $# -gt 0 ]; do
  case "$1" in
    --status) SHOW_STATUS=1; shift ;;
    -h|--help) sed -n '2,14p' "$0"; exit 0 ;;
    *) bad "未知参数: $1" ;;
  esac
done

STATE_FILE="$OPS_DIR/state/last-switch.env"
PREV_VERSIONS="$OPS_DIR/state/versions.env.previous"

state_value() { awk -F= -v k="$1" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$STATE_FILE" 2>/dev/null || true; }

if [ "$SHOW_STATUS" -eq 1 ]; then
  hdr "回退指针"
  if [ -f "$STATE_FILE" ]; then
    sed 's/^/  /' "$STATE_FILE"
  else
    info "尚无切换记录（$STATE_FILE 不存在）"
  fi
  echo
  info "当前 versions.env 的 runtime：$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
  info "备份文件存在：$([ -f "$PREV_VERSIONS" ] && echo 是 || echo 否)"
  exit 0
fi

require_docker
require_compose_file
umask 077
mkdir -p -m 0700 "$OPS_LOG_DIR"
LOG="$(new_log rollback)"
exec > >(tee -a "$LOG") 2>&1

[ -f "$STATE_FILE" ] || bad "没有可回滚的记录（$STATE_FILE 不存在）；请先执行过一次 ./ops/dsh-switch.sh"
[ -f "$PREV_VERSIONS" ] || bad "缺少回退快照 $PREV_VERSIONS"

CUR_RUNTIME="$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
PREV_RUNTIME="$(awk -F= '$1=="DWP_DSH_RUNTIME"{sub(/^[^=]*=/,""); print; exit}' "$PREV_VERSIONS")"
SERVICES="$(state_value SWITCH_SERVICES)"
EMPLOYEES="$(state_value SWITCH_EMPLOYEES)"
EXPECTED_MODEL="$(awk -F= '$1=="DEEPSEEK_MODEL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  "$OFFLINE_DIR/config.env" 2>/dev/null || true)"
EXPECTED_MODEL="${EXPECTED_MODEL:-ascend-qwen3.6-27b}"

# 读取某个版本快照文件里该AI员工的生效 runtime（空 = 沿用全局）
effective_runtime_of() {
  local file="$1" id="$2" key val
  key="$(runtime_key_for "$id")"
  val="$(awk -F= -v k="$key" '$1==k{sub(/^[^=]*=/,""); print; exit}' "$file" 2>/dev/null || true)"
  if [ -n "$val" ]; then printf '%s' "$val"; else
    awk -F= '$1=="DWP_DSH_RUNTIME"{sub(/^[^=]*=/,""); print; exit}' "$file" 2>/dev/null
  fi
}

hdr "0/4 回滚计划"
info "切换时间  : $(state_value SWITCH_AT)"
info "切换模式  : $(state_value SWITCH_MODE)"
info "回滚实例  : ${EMPLOYEES:-<空>}"
info "全局 runtime：$CUR_RUNTIME -> $PREV_RUNTIME"
for id in $EMPLOYEES; do
  info "  $id 生效 runtime：$(effective_runtime_of "$VERSIONS_FILE" "$id") -> $(effective_runtime_of "$PREV_VERSIONS" "$id")"
done
info "日志      : $LOG"
[ -n "$SERVICES" ] || bad "回退记录里没有受影响的服务列表，无法回滚"
[ -d "$(runtime_dir_of "$PREV_RUNTIME")" ] || bad "旧版本目录已不存在：$PREV_RUNTIME（无法回滚）"

hdr "1/4 恢复 versions.env"
cp -f "$VERSIONS_FILE" "$OPS_DIR/state/versions.env.rolledback"
cp -f "$PREV_VERSIONS" "$VERSIONS_FILE"
# 恢复 runtime/current 软链，保持指针与人可读状态一致
ln -sfn "$(basename "$(runtime_dir_of "$PREV_RUNTIME")")" "$RUNTIME_ROOT/current"
sync_env_from_versions
ok "versions.env 已恢复；runtime/current -> $(readlink "$RUNTIME_ROOT/current")"

hdr "2/4 重建受影响的 harness 容器"
for id in $EMPLOYEES; do purge_bundle_farm "$id"; done
# shellcheck disable=SC2086
docker compose up -d --no-deps --force-recreate $SERVICES
ok "已重建：$SERVICES"

hdr "3/4 冒烟校验"
failures=0
for id in $EMPLOYEES; do
  smoke_harness "$id" "$PREV_RUNTIME" "$EXPECTED_MODEL" || failures=$((failures + 1))
done

hdr "4/4 结论"
if [ "$failures" -ne 0 ]; then
  bad "回滚后仍有 $failures 个实例冒烟不通过，请人工检查（$LOG）"
fi
for id in $EMPLOYEES; do
  ok "回滚完成：$id 生效 runtime -> $(effective_runtime_of "$VERSIONS_FILE" "$id")"
done
echo "日志：$LOG"
