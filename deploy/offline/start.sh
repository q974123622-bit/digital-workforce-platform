#!/bin/bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

image_archive="images/dwp-ai-employee-platform-images-linux-amd64.tar"
secret_file="secrets/llm_api_key"
signing_secret_file="secrets/harness_signing_secret"
employee_password_file="secrets/employee_initial_password"
admin_password_file="secrets/admin_initial_password"
required_images=("dwp-backend:0.2.0-offline" "dwp-frontend:0.2.0-offline" "dwp-dsh:rc6")

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

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

umask 077
mkdir -p secrets data backups
for employee_id in AI-GENERAL AI-INVESTMENT DT-E10281 DT-E20999; do
  mkdir -p "harness/$employee_id/dsh-home" "harness/$employee_id/workspace"
done
chmod 700 secrets data backups harness

docker_gid="$(stat -c '%g' /var/run/docker.sock)"
printf 'DWP_UID=%s\nDWP_GID=%s\nDWP_DOCKER_GID=%s\nDWP_HTTP_PORT=%s\n' \
  "$(id -u)" "$(id -g)" "$docker_gid" "${DWP_HTTP_PORT:-8080}" > .env

if [ ! -f config.env ]; then
  cp config.env.example config.env
  chmod 600 config.env
  echo "已生成 config.env；当前使用 Mock 知识库。"
fi

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

prompt_password() {
  local path="$1" label="$2" first second
  if [ ! -s "$path" ]; then
    read -rsp "请输入${label}初始密码（至少 10 位，输入不回显）: " first; echo
    read -rsp "请再次输入${label}初始密码: " second; echo
    [ "$first" = "$second" ] || fail "两次输入的${label}密码不一致"
    [ "${#first}" -ge 10 ] || fail "${label}密码至少需要 10 个字符"
    printf '%s' "$first" > "$path"
    unset first second
  fi
  chmod 600 "$path"
}
prompt_password "$employee_password_file" "员工账号"
prompt_password "$admin_password_file" "管理员账号"

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

echo "[3/5] 启动平台和四个独立 Harness 实例……"
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

echo "[5/5] 自检四个 Harness 实例……"
for container in dwp-harness-ai-general dwp-harness-ai-investment dwp-harness-dt-e10281 dwp-harness-dt-e20999; do
  running="$(docker inspect --format '{{.State.Running}}' "$container" 2>/dev/null || true)"
  [ "$running" = "true" ] || fail "$container 未运行"
  docker exec "$container" sh -c 'command -v dsh >/dev/null && test -f /dsh-home/profiles/dwp-knowledge-agent-v2/cordis.yml' \
    || fail "$container 的 Harness/Profile 自检失败"
done

port="${DWP_HTTP_PORT:-8080}"
host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "启动完成：前端、后端和 4 个 Harness 实例均已运行"
echo "本机访问: http://127.0.0.1:${port}"
if [ -n "$host_ip" ]; then
  echo "局域网访问: http://${host_ip}:${port}"
fi
echo "完整自检: ./verify.sh"
echo "查看状态: ./status.sh"
