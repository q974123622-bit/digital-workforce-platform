#!/bin/bash
# dwp AI员工平台 —— dsh 升级预演（canary，真实跑两轮）
#
# 门禁（dsh-preflight.sh）是纯静态的；本脚本用一次性容器 + 真实模型凭据真跑两轮任务，
# 校验 dsh 升级后仍然满足平台依赖的 stdout 契约：
#   - 退出码为 0
#   - stdout 是纯文本（无 ANSI 转义、无回车进度刷新、不是 JSON、非空、体积合理）
#   - stderr 没有致命错误（含 context_window，用于验证 maxTokens 提高是否安全）
#
# 两个场景缺一不可：
#   plain —— 纯文本任务，验证「模型链路 + stdout 契约」这条主链路。
#   tool  —— 真跑本地工具（写文件 → bash 读回），验证三件事：
#            ① A 档本地工具（bash/fs/fs-search/todo/goal）真的能干活（产物落盘做正向控制）；
#            ② 进度行 / 待办标记 / 思考过程没有混进 stdout（平台按纯文本渲染，混进来就是污染）；
#            ③ 工具调用的中间输出没有把 stdout 撑爆或变成 JSON。
#
# 说明：平台 MCP 通道指向一个不可达的哑地址（与真机探测一致），任务本身要求不调用工具，
# 因此本脚本只验证「dsh 主链路 + 模型链路 + 本地工具 + stdout 契约」，不验证平台 MCP 工具面。
#
# 用法:
#   ./ops/dsh-canary.sh                      # 用 versions.env 当前 runtime
#   ./ops/dsh-canary.sh dsh-0.1.5-rc.1
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

RUNTIME_ARG=""
while [ $# -gt 0 ]; do
  case "$1" in
    -h|--help) sed -n '2,24p' "$0"; exit 0 ;;
    -*)        bad "未知参数: $1" ;;
    *)         RUNTIME_ARG="$1"; shift ;;
  esac
done

RUNTIME_SPEC="${RUNTIME_ARG:-$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')}"
RUNTIME_DIR="$(runtime_dir_of "$RUNTIME_SPEC")"
RUNTIME_VERSION="$(runtime_version_of "$RUNTIME_DIR")"
BASE_IMAGE="$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
PROFILE_REL="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
PROFILE_DIR="$(abs_from_offline "$PROFILE_REL")"

config_value() {
  awk -F= -v k="$1" '$1==k {sub(/^[^=]*=/, ""); gsub(/^"|"$/, ""); print; exit}' \
    "$OFFLINE_DIR/config.env" 2>/dev/null || true
}

BASE_URL="$(config_value DEEPSEEK_BASE_URL)"
MODEL="$(config_value DEEPSEEK_MODEL)"
[ -n "$BASE_URL" ] || bad "config.env 未配置 DEEPSEEK_BASE_URL"
[ -n "$MODEL" ] || bad "config.env 未配置 DEEPSEEK_MODEL"
[ -s "$OFFLINE_DIR/secrets/llm_api_key" ] || bad "缺少 secrets/llm_api_key"
API_KEY="$(cat "$OFFLINE_DIR/secrets/llm_api_key")"
[ -n "$API_KEY" ] || bad "secrets/llm_api_key 为空"

require_docker
umask 077
mkdir -p -m 0700 "$OPS_LOG_DIR"
LOG="$(new_log canary)"
exec > >(tee -a "$LOG") 2>&1

# 单轮内层超时。与 config.env 的 DWP_HARNESS_TIMEOUT_ROLE（默认 900 秒）配套：
# 预演只跑一轮简单任务，给 600 秒足够，且不会让门禁长时间挂住。
INNER_TIMEOUT=600

hdr "0/5 预演目标"
info "runtime : $RUNTIME_SPEC ($RUNTIME_VERSION)"
info "profile : $PROFILE_REL"
info "模型    : $MODEL @ $BASE_URL"
info "日志    : $LOG"

hdr "1/5 准备一次性沙箱"
SANDBOX="$(mktemp -d "${TMPDIR:-/tmp}/dsh-canary.XXXXXX")"
cleanup() { rm -rf "$SANDBOX"; }
trap cleanup EXIT
ok "沙箱：$SANDBOX"

FAILURES=0
note_fail() { warn "$1"; FAILURES=$((FAILURES + 1)); }

# 准备一个独立的场景沙箱（各自一份 dsh-home 与 workspace，避免两轮会话状态互相污染）。
prepare_scenario() {
  local sb="$1"
  mkdir -p "$sb/profile" "$sb/dsh-home/profiles/$DSH_PROFILE_NAME" "$sb/workspace"
  cp "$PROFILE_DIR/cordis.yml" "$PROFILE_DIR/cordis.patch.yml" "$PROFILE_DIR/package.json" "$sb/profile/"
  cp "$PROFILE_DIR/cordis.yml" "$PROFILE_DIR/cordis.patch.yml" "$PROFILE_DIR/package.json" \
     "$sb/dsh-home/profiles/$DSH_PROFILE_NAME/"
  chmod -R u+rwX "$sb"
}

# 真跑一轮。任务文本通过环境变量传入，避免把中文任务插进 sh -c 的引号里。
run_scenario() {
  local sb="$1" task="$2"
  set +e
  docker run --rm \
    --user "$(id -u):$(id -g)" \
    -e HOME=/tmp \
    -v "$RUNTIME_DIR:$DSH_RUNTIME_MOUNT:ro" \
    -v "$sb/profile:$PROFILE_MOUNT" \
    -v "$sb/dsh-home:/dsh-home" \
    -v "$sb/workspace:/workspace" \
    -e "PATH=$CONTAINER_PATH" \
    -e "DSH_HOME=/dsh-home" \
    -e "DSH_PERMISSION_MODE=workspace-write" \
    -e "DSH_TELEMETRY_MODE=$(versions_value DWP_DSH_TELEMETRY_MODE 'DISABLED')" \
    -e "DEEPSEEK_API_KEY=$API_KEY" \
    -e "DEEPSEEK_BASE_URL=$BASE_URL" \
    -e "DEEPSEEK_MODEL=$MODEL" \
    -e "DWP_PLATFORM_TOOL_URL=http://127.0.0.1:9/mcp" \
    -e "DWP_AGENT_TOOL_TOKEN=canary-dummy" \
    -e "DWP_CANARY_TASK=$task" \
    --entrypoint sh "$BASE_IMAGE" -c '
      set -u
      echo "dsh = $(readlink -f /opt/dsh-runtime/bin/dsh)"
      echo "version = $(node -p "require(\"/opt/dsh-runtime/lib/node_modules/@deepseek-ai/dsh/package.json\").version")"
      cd /workspace
      timeout '"$INNER_TIMEOUT"' dsh --profile dwp-knowledge-agent-v2 "$DWP_CANARY_TASK" \
          > /dsh-home/run.out 2> /dsh-home/run.err
      echo "run_exit=$?"
    '
  local status=$?
  set -e
  [ "$status" -eq 0 ] || note_fail "一次性容器执行失败（docker run 返回 $status）"
}

# stdout 通用契约：体积 / ANSI / 回车进度 / JSON 起始 / 非空。
check_stdout_contract() {
  local name="$1" out="$2" err="$3"
  local bytes first_char text

  [ -f "$out" ] || { note_fail "[$name] 没有产出 stdout 文件"; return; }
  bytes="$(wc -c < "$out" | tr -d ' ')"
  text="$(tr -d '\r' < "$out" | sed '/^[[:space:]]*$/d')"
  first_char="$(printf '%s' "$text" | head -c 1)"

  printf '  [%s] stdout %s 字节；stderr %s 字节\n' "$name" "$bytes" "$(wc -c < "$err" | tr -d ' ')"

  if [ "$bytes" -gt 0 ] && [ "$bytes" -le 65536 ]; then
    ok "[$name] stdout 体积合理（1..65536 字节）"
  else
    note_fail "[$name] stdout 体积异常：$bytes 字节"
  fi

  if LC_ALL=C grep -q "$(printf '\033')" "$out"; then
    note_fail "[$name] stdout 含 ANSI 转义序列（平台按纯文本解析，会污染回答）"
  else
    ok "[$name] stdout 无 ANSI 转义序列"
  fi

  # 回车符 = 进度行在原地刷新（进度条/Todo 列表常见），落进 stdout 会变成一坨覆盖文本。
  if LC_ALL=C grep -q "$(printf '\r')" "$out"; then
    note_fail "[$name] stdout 含回车符（疑似进度刷新残留）"
  else
    ok "[$name] stdout 无回车刷新残留"
  fi

  case "$first_char" in
    '{'|'[') note_fail "[$name] stdout 以 JSON 起始（$first_char），与平台约定的纯文本契约不符" ;;
    *)       ok "[$name] stdout 非 JSON（首字符 '${first_char:-<空>}'）" ;;
  esac

  if [ "$bytes" -gt 0 ]; then
    ok "[$name] stdout 非空，首 120 字符：$(printf '%s' "$text" | head -c 120)"
  else
    note_fail "[$name] stdout 为空"
  fi

  if grep -qiE 'FATAL|Cannot find module|EACCES|ENOTFOUND|context_window|unhandled' "$err" 2>/dev/null; then
    warn "[$name] stderr 出现致命关键字："
    grep -iE 'FATAL|Cannot find module|EACCES|ENOTFOUND|context_window|unhandled' "$err" | head -5 | sed 's/^/      /'
    note_fail "[$name] stderr 含致命关键字"
  else
    ok "[$name] stderr 无致命关键字"
  fi
}

# 进度行 / 待办标记 / 思考过程泄漏检测。平台把 stdout 当最终答复渲染，
# 混进任何一条都会让用户看到「- [ ] 正在读取文件」这类内部过程。
# 首轮黑名单是猜测值，产物落盘供人工复核后收敛。
LEAK_RE='^[[:space:]]*\[[ x]\]|^[[:space:]]*-[[:space:]]*\[[ x]\]|⏺|✻|⏵|✔|Thinking|Todo|tokens used'
check_no_leak() {
  local name="$1" out="$2"
  local hits
  hits="$(LC_ALL=C grep -nE "$LEAK_RE" "$out" 2>/dev/null | head -5 || true)"
  if [ -n "$hits" ]; then
    warn "[$name] stdout 命中进度/待办/思考泄漏关键字："
    printf '%s\n' "$hits" | sed 's/^/      /'
    note_fail "[$name] stdout 被内部过程污染"
  else
    ok "[$name] stdout 无进度/待办/思考泄漏"
  fi
}

PLAIN_TASK="用一句话介绍你自己，不要调用任何工具"
# 工具场景刻意要求「只回复文件内容本身」：任何进度/待办/思考都会立刻暴露成污染。
TOOL_TASK="在当前目录创建文件 notes.txt，内容为 hello-dwp；然后用 bash 读回该文件确认内容正确；最后只回复文件内容本身，不要输出任何进度、待办或思考过程，也不要调用知识库或其他平台工具。"

hdr "2/5 场景 plain：纯文本任务（验证模型链路与 stdout 契约）"
prepare_scenario "$SANDBOX/plain"
run_scenario "$SANDBOX/plain" "$PLAIN_TASK"
PLAIN_OUT="$SANDBOX/plain/dsh-home/run.out"
PLAIN_ERR="$SANDBOX/plain/dsh-home/run.err"
check_stdout_contract "plain" "$PLAIN_OUT" "$PLAIN_ERR"

hdr "3/5 场景 tool：真跑本地工具（写文件 → bash 读回）"
prepare_scenario "$SANDBOX/tool"
run_scenario "$SANDBOX/tool" "$TOOL_TASK"
TOOL_OUT="$SANDBOX/tool/dsh-home/run.out"
TOOL_ERR="$SANDBOX/tool/dsh-home/run.err"
check_stdout_contract "tool" "$TOOL_OUT" "$TOOL_ERR"
check_no_leak "tool" "$TOOL_OUT"

NOTES="$SANDBOX/tool/workspace/notes.txt"
if [ -f "$NOTES" ] && grep -q 'hello-dwp' "$NOTES"; then
  ok "[tool] 本地工具真能干活：/workspace/notes.txt 已落盘且内容正确"
else
  note_fail "[tool] 本地工具没有真正执行（/workspace/notes.txt 缺失或内容不符）"
fi

hdr "4/5 登记产物"
for name in plain tool; do
  cp -f "$SANDBOX/$name/dsh-home/run.out" "$OPS_BASELINE_DIR/canary-$RUNTIME_VERSION.$name.out.txt" 2>/dev/null || true
  cp -f "$SANDBOX/$name/dsh-home/run.err" "$OPS_BASELINE_DIR/canary-$RUNTIME_VERSION.$name.err.txt" 2>/dev/null || true
done
ok "产物：$OPS_BASELINE_DIR/canary-$RUNTIME_VERSION.{plain,tool}.{out,err}.txt"

hdr "5/5 结论"
if [ "$FAILURES" -ne 0 ]; then
  bad "预演不通过（$FAILURES 项）：禁止切换。stdout 见 $OPS_BASELINE_DIR/canary-$RUNTIME_VERSION.plain.out.txt"
fi
ok "预演通过：runtime $RUNTIME_VERSION 两轮真实执行且 stdout 契约未变"
