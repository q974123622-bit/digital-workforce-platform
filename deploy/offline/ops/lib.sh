#!/bin/bash
# dwp AI员工平台 —— dsh 可插拔框架运维公共库
#
# 被 ops/*.sh 以 `source "$(dirname "$0")/lib.sh"` 方式加载，不直接执行。
# 约定：
#   - versions.env 是唯一版本真相；.env 由本库从 versions.env 展开生成，供 docker compose 读取。
#   - 所有日志写入 ops/logs/，umask 077。
#   - 不打印 config.env 解析后的完整环境变量（含 Authorization / API Key）。

set -Eeuo pipefail

OPS_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OFFLINE_DIR="$(cd "$OPS_DIR/.." && pwd)"
VERSIONS_FILE="$OFFLINE_DIR/versions.env"
ENV_FILE="$OFFLINE_DIR/.env"
OPS_LOG_DIR="$OPS_DIR/logs"
OPS_BASELINE_DIR="$OPS_DIR/baseline"
RUNTIME_ROOT="$OFFLINE_DIR/runtime"
PROFILE_ROOT="$OFFLINE_DIR/profile"

# 容器内约定（必须与 compose.yaml 的 x-harness-common 保持一致）
DSH_RUNTIME_MOUNT="/opt/dsh-runtime"
PROFILE_MOUNT="/opt/dwp/profile"
CONTAINER_PATH="/opt/dsh-runtime/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin"
DSH_PROFILE_NAME="dwp-knowledge-agent-v2"

# AI员工白名单唯一来源（与 compose.yaml 服务名一一对应）。
# Python 侧对应 backend/app/services/employee_registry.py；两边必须一致，
# backend/tests/test_employee_registry.py 会断言 compose 服务名与本数组一一对应。
DWP_EMPLOYEE_IDS=(AI-GENERAL AI-INVESTMENT AI-RESEARCH AI-FUND AI-XYZQ AI-AUDIT DT-E10281 DT-E20999)

ok()   { printf '  [ OK ] %s\n' "$*"; }
info() { printf '  [INFO] %s\n' "$*"; }
warn() { printf '  [WARN] %s\n' "$*" >&2; }
bad()  { printf '  [FAIL] %s\n' "$*" >&2; exit 1; }
hdr()  { printf '\n===== %s =====\n' "$*"; }

# ---------- 名称映射 ----------

# AI-GENERAL -> harness-ai-general
service_for() { printf 'harness-%s' "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"; }
# AI-GENERAL -> dwp-harness-ai-general
container_for() { printf 'dwp-harness-%s' "$(printf '%s' "$1" | tr '[:upper:]' '[:lower:]')"; }
# AI-GENERAL -> DWP_DSH_RUNTIME_AI_GENERAL
runtime_key_for() { printf 'DWP_DSH_RUNTIME_%s' "$(printf '%s' "$1" | tr '[:lower:]-' '[:upper:]_')"; }

harness_services() { local id; for id in "${DWP_EMPLOYEE_IDS[@]}"; do service_for "$id"; done; }
harness_containers() { local id; for id in "${DWP_EMPLOYEE_IDS[@]}"; do container_for "$id"; done; }

# AI员工 ID 是否合法（大小写不敏感）
is_employee_id() {
  local want id
  want="$(printf '%s' "$1" | tr '[:lower:]' '[:upper:]')"
  for id in "${DWP_EMPLOYEE_IDS[@]}"; do [ "$id" = "$want" ] && return 0; done
  return 1
}

# 规范化AI员工 ID（大小写不敏感 -> 标准大写形式）
normalize_employee_id() { printf '%s' "$1" | tr '[:lower:]' '[:upper:]'; }

# ---------- versions.env / .env 读写 ----------

# versions_value KEY [DEFAULT]
versions_value() {
  local key="$1" default="${2:-}" value=""
  if [ -f "$VERSIONS_FILE" ]; then
    value="$(awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$VERSIONS_FILE" 2>/dev/null || true)"
  fi
  printf '%s' "${value:-$default}"
}

# set_versions_value KEY VALUE —— 原子改写 versions.env（键不存在则追加）
set_versions_value() {
  local key="$1" value="$2" tmp
  [ -f "$VERSIONS_FILE" ] || bad "缺少 $VERSIONS_FILE"
  tmp="$(mktemp "$VERSIONS_FILE.XXXXXX")"
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$VERSIONS_FILE" > "$tmp"
  chmod 644 "$tmp"   # 版本清单不含凭据，保持普通可读（便于人工审计与入库）
  mv -f "$tmp" "$VERSIONS_FILE"
}

# env_value KEY [DEFAULT] —— 读 .env（上次 start.sh 生成）
env_value() {
  local key="$1" default="${2:-}" value=""
  if [ -f "$ENV_FILE" ]; then
    value="$(awk -F= -v k="$key" '$1==k {sub(/^[^=]*=/, ""); print; exit}' "$ENV_FILE" 2>/dev/null || true)"
  fi
  printf '%s' "${value:-$default}"
}

# set_env_key KEY VALUE —— 原子改写 .env
set_env_key() {
  local key="$1" value="$2" tmp
  tmp="$(mktemp "$ENV_FILE.XXXXXX")"
  { [ -f "$ENV_FILE" ] && cat "$ENV_FILE" || true; } > "$tmp.orig"
  awk -v k="$key" -v v="$value" '
    BEGIN { done = 0 }
    index($0, k "=") == 1 { print k "=" v; done = 1; next }
    { print }
    END { if (!done) print k "=" v }
  ' "$tmp.orig" > "$tmp"
  rm -f "$tmp.orig"
  chmod 600 "$tmp"
  mv -f "$tmp" "$ENV_FILE"
}

# 把 versions.env 的框架/Profile 版本展开写入 .env（per-employee 空值回落全局）
#
# 关键点：写入 .env 前把 `./profile/current`、`./runtime/current` 这类软链指针解析成
# 具体目录的相对路径，避免 Docker daemon 解析 bind 源软链时产生歧义；
# 同时让 `docker inspect` 出来的挂载路径就是真实版本目录，便于审计。
#
# 用法: sync_env_from_versions [EMPLOYEE_ID ...]
sync_env_from_versions() {
  local global_runtime global_version profile telemetry id key override
  global_runtime="$(resolve_under_offline "$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')")"
  global_version="$(versions_value DWP_DSH_RUNTIME_VERSION '')"
  profile="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
  telemetry="$(versions_value DWP_DSH_TELEMETRY_MODE 'DISABLED')"
  [ -f "$ENV_FILE" ] || : > "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  set_env_key DWP_DSH_RUNTIME "$global_runtime"
  set_env_key DWP_DSH_RUNTIME_VERSION "$global_version"
  # 底座镜像也必须跟着 versions.env 走：compose 里写的是 ${DWP_DSH_IMAGE:-dwp-dsh:rc6-py}，
  # 而 .env 的同名键优先级更高 —— 只改 versions.env 不改 .env 会出现「配置里换了镜像、
  # 实际容器还在用旧镜像」，技能脚本的 python3 就静默消失了。
  set_env_key DWP_DSH_IMAGE "$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6-py')"
  set_env_key DWP_PROFILE "$profile"
  set_env_key DWP_PROFILE_REV "$(versions_value DWP_PROFILE_REV '')"
  set_env_key DWP_DSH_TELEMETRY_MODE "$telemetry"
  for id in "${DWP_EMPLOYEE_IDS[@]}"; do
    key="$(runtime_key_for "$id")"
    override="$(versions_value "$key" '')"
    [ -n "$override" ] || override="$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
    set_env_key "$key" "$(resolve_under_offline "$override")"
  done
}

# ---------- 路径工具 ----------

# 相对 deploy/offline 的路径 -> 绝对路径
abs_from_offline() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    *)  printf '%s' "$OFFLINE_DIR/${1#./}" ;;
  esac
}

# 把 deploy/offline 下的路径（可为软链）解析为 ./xxx 形式的真实相对路径
# 解析不到（不存在 / 在 deploy/offline 之外）时原样返回，交由后续断言报错。
resolve_under_offline() {
  local p abs
  p="$(abs_from_offline "$1")"
  abs="$(readlink -f "$p" 2>/dev/null || true)"
  [ -n "$abs" ] || { printf '%s' "$1"; return 0; }
  case "$abs" in
    "$OFFLINE_DIR"/*) printf './%s' "${abs#"$OFFLINE_DIR"/}" ;;
    *) printf '%s' "$abs" ;;
  esac
}

# 更新 runtime/current 软链 + versions.env 的框架版本三件套
# 用法: set_runtime_pointer <dir-name> <version>
set_runtime_pointer() {
  local name="$1" version="$2"
  ln -sfn "$name" "$RUNTIME_ROOT/current"
  set_versions_value DWP_DSH_RUNTIME "./runtime/$name"
  set_versions_value DWP_DSH_RUNTIME_VERSION "$version"
}

# runtime 目录名 -> 绝对路径（入参可为 dsh-0.1.5-rc.1 / ./runtime/dsh-0.1.5-rc.1）
runtime_dir_of() {
  case "$1" in
    /*) printf '%s' "$1" ;;
    ./*|../*) printf '%s' "$(abs_from_offline "$1")" ;;
    *) printf '%s' "$RUNTIME_ROOT/$1" ;;
  esac
}

# 版本号 -> dsh 目录名（0.1.5-rc.1 -> dsh-0.1.5-rc.1）
runtime_name_of_version() { printf 'dsh-%s' "${1#dsh-}"; }

# runtime 目录 -> 版本号（读 MANIFEST.json，回退目录名）
runtime_version_of() {
  local dir version
  dir="$(runtime_dir_of "$1")"
  if [ -f "$dir/MANIFEST.json" ]; then
    version="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$dir/MANIFEST.json" | head -1)"
    [ -n "$version" ] && { printf '%s' "$version"; return 0; }
  fi
  basename "$dir" | sed 's/^dsh-//'
}

# ---------- 日志 ----------

# 打开一个日志文件并 echo 其路径；调用方自行 tee
new_log() {
  mkdir -p -m 0700 "$OPS_LOG_DIR"
  umask 077
  printf '%s/%s-%s.log' "$OPS_LOG_DIR" "$1" "$(date +%Y%m%d-%H%M%S)"
}

# ---------- 前置检查 ----------

require_docker() {
  command -v docker >/dev/null 2>&1 || bad "未安装 Docker"
  docker info >/dev/null 2>&1 || bad "当前用户无法访问 Docker"
  docker compose version >/dev/null 2>&1 || bad "未安装 Docker Compose v2"
}

require_compose_file() {
  [ -f "$OFFLINE_DIR/compose.yaml" ] || bad "缺少 $OFFLINE_DIR/compose.yaml"
}

# ---------- 生效状态校验（切换/回滚/状态共用） ----------

# 读取容器实际生效的 runtime 版本（直接读挂载目录里的 package.json，不依赖 CLI 参数）
harness_runtime_version() {
  docker exec "$1" sh -c \
    'node -p "require(\"/opt/dsh-runtime/lib/node_modules/@deepseek-ai/dsh/package.json\").version" 2>/dev/null' \
    2>/dev/null | tr -d '\r' || true
}

# 读取容器 /opt/dsh-runtime 的挂载源（宿主机路径）
harness_mount_source() {
  docker inspect --format \
    '{{range .Mounts}}{{if eq .Destination "/opt/dsh-runtime"}}{{.Source}}{{end}}{{end}}' \
    "$1" 2>/dev/null || true
}

# 读取 dsh-home 里 bundle 软链的指向（dsh 每次运行都会重建，指向当前 runtime）
harness_bundle_link() {
  docker exec "$1" sh -c \
    'readlink /dsh-home/profiles/node_modules/@deepseek-ai/dsh-base 2>/dev/null' \
    2>/dev/null | tr -d '\r' || true
}

# 在运行中的 harness 容器里合成配置树并跑契约校验（最强的「实际生效」证明）
# 用法: harness_contract_check <container> <expected_model>
harness_contract_check() {
  local container="$1" expected_model="$2" gate=/tmp/dsh-gate rc=0
  local checker="node $gate/dsh-dump-parse.js $gate/dump.txt > $gate/parsed.json 2>$gate/parse.err && node $gate/dsh-contract-check.js $gate/parsed.json $gate/contract.json --expected-model '$expected_model' --stderr-file $gate/dump.err"
  docker exec "$container" sh -c "mkdir -p $gate" >/dev/null 2>&1 || true
  docker cp "$OPS_DIR/dsh-dump-parse.js" "$container:$gate/" >/dev/null
  docker cp "$OPS_DIR/dsh-contract-check.js" "$container:$gate/" >/dev/null
  docker cp "$OPS_BASELINE_DIR/contract.json" "$container:$gate/" >/dev/null
  docker exec "$container" sh -c \
    "cd /workspace && dsh --profile $DSH_PROFILE_NAME --dump-config > $gate/dump.txt 2> $gate/dump.err" \
    >/dev/null 2>&1 || true
  docker exec "$container" sh -c "$checker" >/dev/null 2>&1 || rc=$?
  return $rc
}

# 删除 dsh-home 里由旧版本 dsh 生成的 bundle 软链农场
#
# 它是派生状态：dsh 会在下次运行时按当前 runtime 重建。切换框架版本前清理，
# 可避免留下「指向旧版本绝对路径」的历史残留。
purge_bundle_farm() {
  local path="$OFFLINE_DIR/harness/$1/dsh-home/profiles/node_modules"
  [ -e "$path" ] || [ -L "$path" ] || return 0
  # 用 find -delete 而不是 rm -rf：该目录有 500+ 个软链，rm -rf 容易触发环境的安全守卫
  if find "$path" -mindepth 1 -delete 2>/dev/null && rmdir "$path" 2>/dev/null; then
    info "已清理 $1 的 bundle 软链农场（将由 dsh 按当前 runtime 重建）"
  else
    warn "$1 的 bundle 软链农场清理未完成（不影响功能：合成树由 runtime 自身 bundle 决定）"
  fi
  return 0
}

# 校验单个AI员工的 harness 实际生效的 runtime
# 用法: smoke_harness <EMPLOYEE_ID> <期望的 runtime 目录（可为相对/绝对/目录名）> [期望模型]
#
# 硬失败判据：容器运行中 / 挂载源正确 / dsh 版本正确 / 容器内合成树契约校验通过。
# bundle 软链只做提示（2026-09-17 实测：即使它仍指向旧路径，合成树也逐字节一致，
# 说明合成树由 runtime 自身的 bundle 决定；软链只在 profile 声明 dependencies 时才参与解析）。
smoke_harness() {
  local id="$1" expected="$2" expected_model="${3:-}"
  local c expected_dir expected_version mount_src version link ok=0
  c="$(container_for "$id")"
  expected_dir="$(runtime_dir_of "$expected")"
  expected_version="$(runtime_version_of "$expected_dir")"

  if [ "$(docker inspect --format '{{.State.Running}}' "$c" 2>/dev/null || echo false)" != "true" ]; then
    warn "$c 未运行"; return 1
  fi

  mount_src="$(harness_mount_source "$c")"
  if [ "$mount_src" != "$expected_dir" ]; then
    warn "$c 的 /opt/dsh-runtime 挂载源为 ${mount_src:-<空>}，期望 $expected_dir"; ok=1
  fi

  version="$(harness_runtime_version "$c")"
  if [ "$version" != "$expected_version" ]; then
    warn "$c 的 dsh 版本为 ${version:-<空>}，期望 $expected_version"; ok=1
  fi
  [ "$ok" -eq 0 ] || return 1

  # bundle 软链农场是 dsh 的历史派生状态：0.1.0-rc.6 会创建并维护它，0.1.5-rc.1 起不再创建。
  # 实测（2026-09-17）两版合成树逐字节一致，说明它不参与合成树，因此这里只做提示、不做硬失败。
  link="$(harness_bundle_link "$c")"
  if [ -n "$link" ] && [ "${link#"$DSH_RUNTIME_MOUNT"/}" = "$link" ]; then
    # 指向挂载点之外（历史残留）：触发一次 dsh 启动看看是否会被改写
    docker exec "$c" sh -c \
      "cd /workspace && dsh --profile $DSH_PROFILE_NAME --dump-config >/dev/null 2>&1 || true" >/dev/null 2>&1 || true
    link="$(harness_bundle_link "$c")"
  fi
  if [ -z "$link" ]; then
    ok "$c  runtime=$version  bundle 软链农场未生成（该版本不再维护，属正常）"
  elif [ "${link#"$DSH_RUNTIME_MOUNT"/}" != "$link" ]; then
    ok "$c  runtime=$version  bundle -> ${link#"$DSH_RUNTIME_MOUNT"/}"
  else
    warn "$c 的 bundle 软链仍指向 $DSH_RUNTIME_MOUNT 之外：$link（仅提示：合成树不受影响；若 profile 新增 dependencies 需重新评估）"
  fi

  if [ -n "$expected_model" ]; then
    if harness_contract_check "$c" "$expected_model" >/dev/null 2>&1; then
      ok "$c  容器内合成树契约校验通过"
    else
      warn "$c 容器内合成树契约校验不通过（详见 docker exec $c cat /tmp/dsh-gate/dump.err）"
      return 1
    fi
  fi
  return 0
}
