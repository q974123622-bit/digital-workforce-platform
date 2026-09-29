#!/bin/bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

# dsh 可插拔框架运维库（versions.env 唯一版本真相 + .env 展开）
[ -f ops/lib.sh ] || fail "缺少 ops/lib.sh（dsh 可插拔框架运维库）"
# shellcheck source=ops/lib.sh
source ops/lib.sh

# 读取已存在的 .env（上次启动写入），使重复执行时不必重新 export
saved_env_value() {
  [ -f .env ] || return 0
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' .env 2>/dev/null || true
}

image_archive="images/dwp-ai-employee-platform-images-linux-amd64.tar"
secret_file="secrets/llm_api_key"
signing_secret_file="secrets/harness_signing_secret"
employee_one_key_file="secrets/login_key_e10281"
employee_two_key_file="secrets/login_key_e20999"
admin_key_file="secrets/login_key_admin"
image_tag="${DWP_IMAGE_TAG:-$(saved_env_value DWP_IMAGE_TAG)}"
image_tag="${image_tag:-0.3.0-offline}"
dsh_image="${DWP_DSH_IMAGE:-$(saved_env_value DWP_DSH_IMAGE)}"
dsh_image="${dsh_image:-$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')}"
required_images=("dwp-backend:${image_tag}" "dwp-frontend:${image_tag}" "$dsh_image")

command -v docker >/dev/null 2>&1 || fail "未安装 Docker Engine"
docker info >/dev/null 2>&1 || fail "当前用户无法访问 Docker；请确认 Docker 已启动且当前用户有权限"
docker compose version >/dev/null 2>&1 || fail "未安装 Docker Compose v2"
[ -S /var/run/docker.sock ] || fail "未找到 /var/run/docker.sock，Harness 无法管理独立员工容器"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) ;;
  *) fail "本包是 linux/amd64，当前架构为 $arch" ;;
esac

docker_root="$(docker info --format '{{.DockerRootDir}}')"
available_kb="$(df -Pk "$docker_root" | awk 'NR == 2 {print $4}')"
[ -n "$available_kb" ] || fail "无法检查 Docker 数据目录剩余空间"
[ "$available_kb" -ge 6291456 ] || fail "Docker 数据目录至少需要 6 GiB 可用空间"

# ---- dsh 可插拔框架：runtime 层与 profile 层预检（缺失时明确报错，不静默回落镜像内置版本）----
global_runtime_rel="$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
global_runtime_dir="$(runtime_dir_of "$global_runtime_rel")"
[ -x "$global_runtime_dir/bin/dsh" ] || fail \
  "dsh runtime 缺失：$global_runtime_rel/bin/dsh
       请先执行 ./ops/dsh-install-runtime.sh <version> 生成版本目录（离线包应已自带）"
profile_rel="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
[ -f "$(abs_from_offline "$profile_rel")/cordis.patch.yml" ] || fail \
  "dsh profile 缺失：$profile_rel（应包含 cordis.yml / cordis.patch.yml / package.json）"
for employee_id in "${DWP_EMPLOYEE_IDS[@]}"; do
  override_rel="$(versions_value "$(runtime_key_for "$employee_id")" '')"
  [ -n "$override_rel" ] || continue
  [ -x "$(runtime_dir_of "$override_rel")/bin/dsh" ] || fail \
    "AI员工 $employee_id 的 runtime 覆盖指向不存在的目录：$override_rel"
done
# 保持 runtime/current 这个人可读指针与 versions.env 一致（离线包解包 / 现场物化后可能不一致）
ln -sfn "$(basename "$global_runtime_dir")" runtime/current
echo "dsh runtime = $global_runtime_rel ($(runtime_version_of "$global_runtime_dir"))  profile = $profile_rel"

umask 077
# data/artifacts/skills 是技能包的展开目录（SkillHub 导入的技能落在这里，再投放到
# harness/<员工>/workspace/skills/<slug>/）。预先建好，避免 Docker 把不存在的 bind 源建成 root 属主目录。
mkdir -p secrets data/artifacts data/artifacts/skills data/attachments backups
for employee_id in "${DWP_EMPLOYEE_IDS[@]}"; do
  mkdir -p "harness/$employee_id/dsh-home" "harness/$employee_id/workspace"
done
# compose 声明了 secrets/qieman_api_key；文件缺失时 Docker 会把 bind 源建成 root 属主目录
[ -f secrets/qieman_api_key ] || : > secrets/qieman_api_key
# 同理：兴证e家员工服务 MCP 的应用级 X-MCP-API-KEY（用户级 JWT 由员工在会话内补全）。
[ -f secrets/ehome_mcp_api_key ] || : > secrets/ehome_mcp_api_key
# 同理：内部知识引擎 VeRAG MCP 的 Bearer token（生产环境知识通道），
# 首次部署若未放入真值则先建空文件占位，之后由交付方写入。
[ -f secrets/verag_mcp_token ] || : > secrets/verag_mcp_token
chmod 600 secrets/* 2>/dev/null || true
chmod 700 secrets data backups harness

port="${DWP_HTTP_PORT:-$(saved_env_value DWP_HTTP_PORT)}"
port="${port:-18080}"
case "$port" in
  ''|*[!0-9]*) fail "DWP_HTTP_PORT 必须是数字" ;;
esac
[ "$port" -ge 1 ] && [ "$port" -le 65535 ] || fail "DWP_HTTP_PORT 必须在 1-65535 之间"

# Re-running start.sh is allowed when this deployment already owns the port.
if command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$port" 2>/dev/null | grep -q .; then
  frontend_running="$(docker compose ps --status running --services 2>/dev/null | grep -c '^frontend$' || true)"
  [ "$frontend_running" -gt 0 ] || fail "端口 $port 已被其他服务占用；请执行 export DWP_HTTP_PORT=其他空闲端口"
fi

docker_gid="$(stat -c '%g' /var/run/docker.sock)"
printf 'DWP_UID=%s\nDWP_GID=%s\nDWP_DOCKER_GID=%s\nDWP_HTTP_PORT=%s\nDWP_IMAGE_TAG=%s\nDWP_DSH_IMAGE=%s\n' \
  "$(id -u)" "$(id -g)" "$docker_gid" "$port" "$image_tag" "$dsh_image" > .env
# 从 versions.env 展开 runtime / profile / 遥测 / per-employee 灰度覆盖键到 .env
# （.env 由本脚本生成，不要手工编辑其中的 DWP_DSH_RUNTIME* —— 改 versions.env 或跑 ops/dsh-switch.sh）
sync_env_from_versions

if [ ! -f config.env ]; then
  cp config.env.example config.env
  echo "已生成 config.env；当前使用 Mock 知识库。"
fi
chmod 600 config.env

config_value() {
  awk -F= -v key="$1" '$1 == key {sub(/^[^=]*=/, ""); print; exit}' config.env
}

set_config_value() {
  local key="$1" value="$2" temp_file
  temp_file="$(mktemp ./config.env.XXXXXX)"
  awk -v key="$key" -v value="$value" '
    BEGIN { replaced = 0 }
    index($0, key "=") == 1 { print key "=" value; replaced = 1; next }
    { print }
    END { if (!replaced) print key "=" value }
  ' config.env > "$temp_file"
  chmod 600 "$temp_file"
  mv -f "$temp_file" config.env
}

model_base_url="$(config_value DEEPSEEK_BASE_URL)"
if [ -z "$model_base_url" ] || echo "$model_base_url" | grep -q 'example.internal'; then
  read -rp "请输入内部模型网关 Base URL（例如 http://主机:端口/v1）: " model_base_url
  case "$model_base_url" in
    http://*|https://*) ;;
    *) fail "模型 Base URL 必须以 http:// 或 https:// 开头" ;;
  esac
  set_config_value DEEPSEEK_BASE_URL "$model_base_url"
fi

model_name="$(config_value DEEPSEEK_MODEL)"
if [ -z "$model_name" ] || [ "$model_name" = "replace-with-enabled-model-name" ]; then
  read -rp "请输入模型网关开放的模型名: " model_name
  [ -n "$model_name" ] || fail "模型名不能为空"
  set_config_value DEEPSEEK_MODEL "$model_name"
fi

model_path="$(config_value DEEPSEEK_CHAT_COMPLETIONS_PATH)"
if [ -z "$model_path" ]; then
  set_config_value DEEPSEEK_CHAT_COMPLETIONS_PATH "/chat/completions"
fi
unset model_base_url model_name model_path

if [ ! -s "$secret_file" ]; then
  read -rsp "请输入模型 API Key（输入不回显）: " llm_key
  echo
  [ -n "$llm_key" ] || fail "API Key 不能为空"
  printf '%s' "$llm_key" > "$secret_file"
  unset llm_key
fi
chmod 600 "$secret_file"

if [ ! -s "$signing_secret_file" ]; then
  if command -v openssl >/dev/null 2>&1; then
    openssl rand -hex 32 > "$signing_secret_file"
  else
    od -An -N32 -tx1 /dev/urandom | tr -d ' \n' > "$signing_secret_file"
  fi
fi
chmod 600 "$signing_secret_file"

prompt_login_key() {
  local path="$1" label="$2" first second
  if [ ! -s "$path" ]; then
    read -rsp "请输入${label}访问密钥（至少 12 位，输入不回显）: " first; echo
    read -rsp "请再次输入${label}访问密钥: " second; echo
    [ "$first" = "$second" ] || fail "两次输入的${label}密钥不一致"
    [ "${#first}" -ge 12 ] || fail "${label}密钥至少需要 12 个字符"
    printf '%s' "$first" > "$path"
    unset first second
  fi
  chmod 600 "$path"
}
prompt_login_key "$employee_one_key_file" "张三"
prompt_login_key "$employee_two_key_file" "陈晓萌"
prompt_login_key "$admin_key_file" "管理员"

missing_image=0
for image in "${required_images[@]}"; do
  docker image inspect "$image" >/dev/null 2>&1 || missing_image=1
done
if [ "$missing_image" -eq 1 ]; then
  [ -f "$image_archive" ] || fail "缺少离线镜像文件 $image_archive"
  echo "[1/5] 导入前端、后端和 Harness 离线镜像……"
  docker load -i "$image_archive"
else
  echo "[1/5] 所需镜像已存在，跳过导入"
fi

echo "[2/5] 校验 Docker Compose 配置……"
docker compose config --quiet

echo "[3/5] 启动平台和独立 Harness 实例……"
docker compose up -d --remove-orphans

echo "[4/5] 等待平台健康检查……"
healthy=0
for _ in $(seq 1 48); do
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' dwp-ai-employee-platform-backend-1 2>/dev/null || true)"
  if [ "$state" = "healthy" ]; then
    healthy=1
    break
  fi
  if [ "$state" = "unhealthy" ] || [ "$state" = "exited" ]; then
    docker compose logs --tail=120 backend
    fail "后端启动失败，状态为 $state"
  fi
  sleep 5
done
[ "$healthy" -eq 1 ] || fail "后端健康检查超时，请运行 ./status.sh"

echo "[5/5] 自检 Harness 实例……"
for employee_id in "${DWP_EMPLOYEE_IDS[@]}"; do
  container="$(container_for "$employee_id")"
  running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
  [ "$running" = "true" ] || fail "$container 未运行"
  docker exec "$container" sh -c \
    "test -x $DSH_RUNTIME_MOUNT/bin/dsh && command -v dsh >/dev/null && test -f /dsh-home/profiles/$DSH_PROFILE_NAME/cordis.yml" \
    || fail "$container 的 Harness runtime/Profile 自检失败（检查 DWP_DSH_RUNTIME 挂载）"
  # 技能脚本是 `python scripts/...` 形态：底座镜像缺 python3 时技能装了也跑不起来。
  docker exec "$container" sh -c 'command -v python3 >/dev/null' \
    || fail "$container 内没有 python3：请确认 DWP_DSH_IMAGE 指向 dwp-dsh:rc6-py（docker/Dockerfile.dsh-runtime 构建）"
  printf '  %s -> dsh %s，python %s\n' "$container" "$(harness_runtime_version "$container")" \
    "$(docker exec "$container" python3 -V 2>/dev/null | awk '{print $2}')"
done

host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "启动完成：前端、后端和 ${#DWP_EMPLOYEE_IDS[@]} 个 Harness 实例均已运行"
echo "本机访问: http://127.0.0.1:${port}"
if [ -n "$host_ip" ]; then
  echo "局域网访问: http://${host_ip}:${port}"
fi
echo "完整自检: ./verify.sh"
echo "查看状态: ./status.sh"
skillhub_url="$(env_value DWP_SKILLHUB_BASE_URL "$(saved_env_value DWP_SKILLHUB_BASE_URL)")"
if [ -n "$skillhub_url" ]; then
  echo "技能市场: SkillHub = $skillhub_url（管理端「插件」→「技能市场」可浏览与导入）"
else
  echo "技能市场: 未接入（config.env 的 DWP_SKILLHUB_BASE_URL 为空；置为内网地址后重启后端即可）"
fi
