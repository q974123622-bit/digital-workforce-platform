#!/bin/bash
# dwp AI员工平台 —— dsh 升级门禁（静态契约核对）
#
# 做四件事，全部离线、不联网、不调模型：
#   1) 校验候选 runtime 目录结构与 MANIFEST（bin/dsh 可执行、版本自洽）
#   2) 用「可写 profile 副本 + 空 dsh-home」合成配置树（dsh --dump-config 会改写 profile 目录）
#   3) 用 ops/dsh-contract-check.js 与 baseline/contract.json 逐条比对
#       —— 必须禁用的 10 个工具 / 必须启用的关键通道 / 启用的 tool-* 白名单（6 项）/
#          模型绑定 / MCP 通道 / 超时链不变量（profile 工具调用超时容得下后端委派链路）
#   4) 与 baseline/dump-config-<version>.txt 快照做 diff（同一版本必须逐字节可复现）
#
# 用法:
#   ./ops/dsh-preflight.sh                          # 校验 versions.env 当前 runtime
#   ./ops/dsh-preflight.sh dsh-0.1.5-rc.1           # 校验指定版本目录
#   ./ops/dsh-preflight.sh dsh-0.1.5-rc.1 --save-baseline   # 通过后登记基线快照
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

RUNTIME_ARG=""; SAVE_BASELINE=0
while [ $# -gt 0 ]; do
  case "$1" in
    --save-baseline) SAVE_BASELINE=1; shift ;;
    -h|--help)       sed -n '2,20p' "$0"; exit 0 ;;
    -*)              bad "未知参数: $1" ;;
    *)               RUNTIME_ARG="$1"; shift ;;
  esac
done

RUNTIME_SPEC="${RUNTIME_ARG:-$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')}"
RUNTIME_DIR="$(runtime_dir_of "$RUNTIME_SPEC")"
BASE_IMAGE="$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
PROFILE_REL="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
PROFILE_DIR="$(abs_from_offline "$PROFILE_REL")"
EXPECTED_MODEL="$(awk -F= '$1=="DEEPSEEK_MODEL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' \
  "$OFFLINE_DIR/config.env" 2>/dev/null || true)"
[ -n "$EXPECTED_MODEL" ] || EXPECTED_MODEL="$(sed -n 's/.*"fallback"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$OPS_BASELINE_DIR/contract.json" | head -1)"
EXPECTED_MODEL="${EXPECTED_MODEL:-ascend-qwen3.6-27b}"

require_docker
umask 077
mkdir -p -m 0700 "$OPS_LOG_DIR"
LOG="$(new_log preflight)"
exec > >(tee -a "$LOG") 2>&1

hdr "0/5 候选版本"
info "runtime 目录 : $RUNTIME_SPEC -> $RUNTIME_DIR"
info "profile 目录 : $PROFILE_REL"
info "底座镜像     : $BASE_IMAGE"
info "期望模型     : $EXPECTED_MODEL"

hdr "1/5 结构校验"
[ -d "$RUNTIME_DIR" ] || bad "runtime 目录不存在：$RUNTIME_DIR"
[ -x "$RUNTIME_DIR/bin/dsh" ] || bad "缺少可执行入口 $RUNTIME_DIR/bin/dsh"
[ -f "$RUNTIME_DIR/lib/node_modules/@deepseek-ai/dsh/package.json" ] || bad "缺少 dsh 包目录"
RUNTIME_VERSION="$(runtime_version_of "$RUNTIME_DIR")"
MANIFEST_VERSION="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$RUNTIME_DIR/MANIFEST.json" | head -1)"
[ "$MANIFEST_VERSION" = "$RUNTIME_VERSION" ] || bad "MANIFEST 版本 $MANIFEST_VERSION 与目录版本 $RUNTIME_VERSION 不一致"
[ -d "$PROFILE_DIR" ] || bad "profile 目录不存在：$PROFILE_DIR"
for f in cordis.yml cordis.patch.yml package.json; do
  [ -f "$PROFILE_DIR/$f" ] || bad "profile 缺少 $f"
done
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || bad "底座镜像不存在：$BASE_IMAGE"
ok "runtime $RUNTIME_VERSION / profile $(basename "$PROFILE_DIR") / 镜像 $BASE_IMAGE"

hdr "2/5 合成配置树（一次性容器，可写 profile 副本）"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/dsh-preflight.XXXXXX")"
cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT
mkdir -p "$SANDBOX/profile" "$SANDBOX/dsh-home/profiles/$DSH_PROFILE_NAME" "$SANDBOX/workspace"
cp "$PROFILE_DIR/cordis.yml" "$PROFILE_DIR/cordis.patch.yml" "$PROFILE_DIR/package.json" "$SANDBOX/profile/"
cp "$PROFILE_DIR/cordis.yml" "$PROFILE_DIR/cordis.patch.yml" "$PROFILE_DIR/package.json" \
   "$SANDBOX/dsh-home/profiles/$DSH_PROFILE_NAME/"
chmod -R u+rwX "$SANDBOX"

set +e
CONTAINER_OUT="$(docker run --rm \
  --user "$(id -u):$(id -g)" \
  -e HOME=/tmp \
  -v "$RUNTIME_DIR:$DSH_RUNTIME_MOUNT:ro" \
  -v "$SANDBOX/profile:$PROFILE_MOUNT" \
  -v "$SANDBOX/dsh-home:/dsh-home" \
  -v "$SANDBOX/workspace:/workspace" \
  -v "$OPS_DIR:/ops:ro" \
  -e "PATH=$CONTAINER_PATH" \
  -e "DSH_HOME=/dsh-home" \
  -e "DSH_PERMISSION_MODE=workspace-write" \
  -e "DSH_TELEMETRY_MODE=$(versions_value DWP_DSH_TELEMETRY_MODE 'DISABLED')" \
  -e "DEEPSEEK_MODEL=$EXPECTED_MODEL" \
  -e "DWP_PLATFORM_TOOL_URL=http://127.0.0.1:9/mcp" \
  -e "DWP_AGENT_TOOL_TOKEN=preflight-dummy" \
  --entrypoint sh "$BASE_IMAGE" -c '
    set -u
    dsh --profile dwp-knowledge-agent-v2 --dump-config \
        > /dsh-home/dump-config.txt 2> /dsh-home/dump-config.err
    dump_exit=$?
    node /ops/dsh-dump-parse.js /dsh-home/dump-config.txt > /dsh-home/parsed.json 2> /dsh-home/parse.err
    parse_exit=$?
    node /ops/dsh-contract-check.js /dsh-home/parsed.json /ops/baseline/contract.json \
        --expected-model "$DEEPSEEK_MODEL" \
        --stderr-file /dsh-home/dump-config.err \
        --report /dsh-home/report.json > /dsh-home/contract.txt 2>&1
    check_exit=$?
    echo "dump_exit=$dump_exit parse_exit=$parse_exit check_exit=$check_exit"
    exit 0
  ')"
run_status=$?
set -e
echo "  $CONTAINER_OUT"
[ "$run_status" -eq 0 ] || bad "一次性容器执行失败（docker run 返回 $run_status）"

dump_exit="$(printf '%s' "$CONTAINER_OUT" | sed -n 's/.*dump_exit=\([0-9]*\).*/\1/p' | tail -1)"
parse_exit="$(printf '%s' "$CONTAINER_OUT" | sed -n 's/.*parse_exit=\([0-9]*\).*/\1/p' | tail -1)"
check_exit="$(printf '%s' "$CONTAINER_OUT" | sed -n 's/.*check_exit=\([0-9]*\).*/\1/p' | tail -1)"
info "dump_exit=$dump_exit parse_exit=$parse_exit check_exit=$check_exit"

DUMP="$SANDBOX/dsh-home/dump-config.txt"
STDERR_FILE="$SANDBOX/dsh-home/dump-config.err"
CONTRACT_TXT="$SANDBOX/dsh-home/contract.txt"
REPORT="$SANDBOX/dsh-home/report.json"
[ -s "$DUMP" ] || bad "--dump-config 没有产出（dump_exit=$dump_exit）；stderr：$(head -5 "$STDERR_FILE" 2>/dev/null)"
[ "$dump_exit" = "0" ] || bad "--dump-config 退出码 $dump_exit（stderr：$(head -5 "$STDERR_FILE" 2>/dev/null)）"
[ "$parse_exit" = "0" ] || bad "合成树解析失败（parse_exit=$parse_exit）：$(head -5 "$SANDBOX/dsh-home/parse.err" 2>/dev/null)"
[ -f "$CONTRACT_TXT" ] || bad "契约校验未产出结果"
echo
sed 's/^/  /' "$CONTRACT_TXT"

hdr "3/5 与基线快照比对"
BASELINE_DUMP="$OPS_BASELINE_DIR/dump-config-$RUNTIME_VERSION.txt"
if [ -f "$BASELINE_DUMP" ]; then
  if diff -q "$BASELINE_DUMP" "$DUMP" >/dev/null 2>&1; then
    ok "与 baseline/dump-config-$RUNTIME_VERSION.txt 逐字节一致（合成可复现）"
  else
    warn "与基线快照存在差异（同版本应可复现，请人工确认）："
    # 这里必须吞掉 diff 的非 0 退出码：`set -e -o pipefail` 下 `diff | head` 会因
    # 「有差异」而中止整个脚本，导致后面的登记产物与结论从不执行（历史上真实踩过）。
    diff -u "$BASELINE_DUMP" "$DUMP" | head -60 | sed 's/^/    /' || true
    warn "确认是有意的 profile 变更后：cp baseline/dump-config-$RUNTIME_VERSION.latest.txt baseline/dump-config-$RUNTIME_VERSION.txt，再重跑本脚本"
    CONTRACT_FAILED=1
  fi
else
  info "无 $RUNTIME_VERSION 的基线快照（首次引入该版本）"
fi

hdr "4/5 登记产物"
cp -f "$DUMP" "$OPS_BASELINE_DIR/dump-config-$RUNTIME_VERSION.latest.txt"
cp -f "$STDERR_FILE" "$OPS_BASELINE_DIR/dump-config-$RUNTIME_VERSION.stderr.txt"
# 用 if 而不是 `[ -f x ] && cp`：后者在文件不存在时返回 1，`set -e` 会中止脚本。
if [ -f "$REPORT" ]; then
  cp -f "$REPORT" "$RUNTIME_DIR/GATE.json"
  cp -f "$REPORT" "$OPS_BASELINE_DIR/gate-$RUNTIME_VERSION.json"
fi
if [ "$SAVE_BASELINE" -eq 1 ] && [ "${CONTRACT_FAILED:-0}" -eq 0 ] && [ "$check_exit" = "0" ]; then
  cp -f "$DUMP" "$BASELINE_DUMP"
  ok "已登记基线快照 baseline/dump-config-$RUNTIME_VERSION.txt"
fi
ok "报告：$RUNTIME_DIR/GATE.json ；日志：$LOG"

hdr "5/5 结论"
if [ "${CONTRACT_FAILED:-0}" -ne 0 ] || [ "$check_exit" != "0" ]; then
  bad "门禁不通过：禁止切换（详见 $LOG）"
fi
ok "门禁通过：runtime $RUNTIME_VERSION 可安全切换"
echo
echo "下一步：./ops/dsh-switch.sh AI-GENERAL $RUNTIME_SPEC   # 灰度"
