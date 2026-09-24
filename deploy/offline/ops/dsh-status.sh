#!/bin/bash
# dwp AI员工平台 —— 查看每个 harness 实例实际生效的 dsh 框架版本与契约摘要
#
# 用法:
#   ./ops/dsh-status.sh            # 表格 + 全局版本真相
#   ./ops/dsh-status.sh --verbose  # 额外打印每个实例的容器内契约校验结果
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

VERBOSE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --verbose|-v) VERBOSE=1; shift ;;
    -h|--help)    sed -n '2,8p' "$0"; exit 0 ;;
    *)            bad "未知参数: $1" ;;
  esac
done

require_docker
EXPECTED_MODEL="$(awk -F= '$1=="DEEPSEEK_MODEL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  "$OFFLINE_DIR/config.env" 2>/dev/null || true)"
EXPECTED_MODEL="${EXPECTED_MODEL:-ascend-qwen3.6-27b}"

hdr "全局版本真相（versions.env）"
printf '  %-28s %s\n' "底座镜像 DWP_DSH_IMAGE" "$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
printf '  %-28s %s\n' "全局 runtime" "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
printf '  %-28s %s\n' "全局版本" "$(versions_value DWP_DSH_RUNTIME_VERSION '-')"
printf '  %-28s %s\n' "runtime/current 指针" "$(readlink "$RUNTIME_ROOT/current" 2>/dev/null || echo '<未设置>')"
printf '  %-28s %s\n' "Profile 指针" "$(readlink "$PROFILE_ROOT/current" 2>/dev/null || echo '<未设置>')"
printf '  %-28s %s\n' "遥测模式" "$(versions_value DWP_DSH_TELEMETRY_MODE 'DISABLED')"
printf '  %-28s %s\n' "期望模型" "$EXPECTED_MODEL"

hdr "可用 runtime 版本"
for d in "$RUNTIME_ROOT"/*/; do
  [ -d "$d" ] || continue
  [ -L "${d%/}" ] && continue   # 跳过 current 指针软链
  name="$(basename "$d")"
  [ -x "$d/bin/dsh" ] || continue
  gate="未门禁"
  if [ -f "$d/GATE.json" ] && grep -q '"ok"[[:space:]]*:[[:space:]]*true' "$d/GATE.json"; then gate="门禁通过"; fi
  size="$(du -sh "$d" 2>/dev/null | awk '{print $1}')"
  printf '  %-24s %-10s %-8s %s\n' "$name" "$size" "$gate" "$(sed -n 's/.*"installed_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$d/MANIFEST.json" | head -1)"
done

hdr "各 harness 实例实际生效状态"
printf '  %-26s %-9s %-13s %-22s %s\n' "容器" "状态" "dsh 版本" "runtime 挂载源" "bundle 软链"
failures=0
for id in "${DWP_EMPLOYEE_IDS[@]}"; do
  c="$(container_for "$id")"
  state="$(docker inspect --format '{{if .State.Running}}{{.State.Status}}{{else}}stopped{{end}}' "$c" 2>/dev/null || echo missing)"
  if [ "$state" != "running" ]; then
    printf '  %-26s %-9s %s\n' "$c" "$state" "-"
    failures=$((failures + 1))
    continue
  fi
  version="$(harness_runtime_version "$c")"
  mount_src="$(harness_mount_source "$c")"
  link="$(harness_bundle_link "$c")"
  if [ -z "$link" ]; then
    link_disp="未生成（该版本不维护）"
  else
    case "$link" in
      "$DSH_RUNTIME_MOUNT"/*) link_disp="$(printf '%s' "$link" | sed "s|$DSH_RUNTIME_MOUNT/||")" ;;
      *) link_disp="历史残留（不影响合成树）" ;;
    esac
  fi
  printf '  %-26s %-9s %-13s %-18s %s\n' "$c" "$state" "${version:-?}" "${mount_src##*/}" "$link_disp"

  expected_dir="$(runtime_dir_of "$(env_value "$(runtime_key_for "$id")" "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')")")"
  if [ "$mount_src" != "$expected_dir" ]; then
    warn "$c 挂载源与 .env 期望不一致（期望 $expected_dir）"
    failures=$((failures + 1))
  fi
  if [ "$VERBOSE" -eq 1 ]; then
    if harness_contract_check "$c" "$EXPECTED_MODEL" >/dev/null 2>&1; then
      printf '  %-26s %s\n' "" "  └ 容器内契约校验：通过"
    else
      printf '  %-26s %s\n' "" "  └ 容器内契约校验：不通过"
      failures=$((failures + 1))
    fi
  fi
done

hdr "最近一次切换/回滚记录"
if [ -f "$OPS_DIR/state/last-switch.env" ]; then
  sed 's/^/  /' "$OPS_DIR/state/last-switch.env"
else
  info "无切换记录"
fi

hdr "结论"
if [ "$failures" -ne 0 ]; then
  warn "有 $failures 项异常，请检查上面的 [WARN]"
  exit 1
fi
ok "全部 harness 实例的 runtime 挂载与版本一致"
