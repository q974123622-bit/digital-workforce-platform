#!/bin/bash
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

backend_image="dwp-backend:0.1.0-offline"
frontend_image="dwp-frontend:0.1.0-offline"
image_archive="images/dwp-demo-images-linux-amd64.tar"
secret_file="secrets/llm_api_key"

fail() {
  echo "ERROR: $*" >&2
  exit 1
}

command -v docker >/dev/null 2>&1 || fail "未安装 Docker"
docker info >/dev/null 2>&1 || fail "当前用户无法访问 Docker；请确认服务已启动并具有权限"
docker compose version >/dev/null 2>&1 || fail "未安装 Docker Compose v2"

docker_root="$(docker info --format '{{.DockerRootDir}}')"
available_kb="$(df -Pk "$docker_root" | awk 'NR == 2 {print $4}')"
[ -n "$available_kb" ] || fail "无法检查 Docker 数据目录剩余空间"
[ "$available_kb" -ge 2097152 ] || fail "Docker 数据目录 $docker_root 可用空间不足 2 GiB"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) ;;
  *) fail "离线镜像为 linux/amd64，当前服务器架构为 $arch" ;;
esac

mkdir -p secrets data backups
chmod 700 secrets data backups
umask 077
printf 'DWP_UID=%s\nDWP_GID=%s\n' "$(id -u)" "$(id -g)" > .env

if [ ! -s "$secret_file" ]; then
  read -rsp "请输入新测试环境 API Key（输入不回显）: " llm_key
  echo
  [ -n "$llm_key" ] || fail "API Key 不能为空"
  printf '%s' "$llm_key" > "$secret_file"
  unset llm_key
fi
chmod 600 "$secret_file"

if ! docker image inspect "$backend_image" >/dev/null 2>&1 || \
   ! docker image inspect "$frontend_image" >/dev/null 2>&1; then
  [ -f "$image_archive" ] || fail "缺少离线镜像文件 $image_archive"
  echo "[1/4] 导入离线镜像，请稍候……"
  docker load -i "$image_archive"
else
  echo "[1/4] 离线镜像已存在，跳过导入"
fi

echo "[2/4] 启动数字员工平台……"
docker compose up -d --remove-orphans

echo "[3/4] 等待后端健康检查……"
healthy=0
for _ in $(seq 1 36); do
  state="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}' dwp-demo-backend-1 2>/dev/null || true)"
  if [ "$state" = "healthy" ]; then
    healthy=1
    break
  fi
  if [ "$state" = "unhealthy" ] || [ "$state" = "exited" ]; then
    docker compose logs --tail=100 backend
    fail "后端启动失败，状态为 $state"
  fi
  sleep 5
done
[ "$healthy" -eq 1 ] || fail "后端健康检查超时，请运行 ./status.sh 查看日志"

echo "[4/4] 检查内部大模型连通性……"
docker compose exec -T backend python - <<'PY'
import json
import urllib.request

key = open('/run/secrets/llm_api_key', encoding='utf-8').read().strip()
req = urllib.request.Request(
    'http://api.llmxt.cisctest:8002/v1/chat/completions',
    data=json.dumps({
        'model': 'ascend-deepseek-v4-flash',
        'messages': [{'role': 'user', 'content': '只回复：容器模型连通成功'}],
        'stream': False,
        'max_tokens': 30,
    }, ensure_ascii=False).encode('utf-8'),
    headers={'Authorization': f'Bearer {key}', 'Content-Type': 'application/json'},
    method='POST',
)
try:
    with urllib.request.urlopen(req, timeout=120) as resp:
        body = json.load(resp)
    print('模型检查:', body['choices'][0]['message'].get('content', '无 content 字段'))
except Exception as exc:
    print(f'WARNING: 平台已启动，但容器访问模型失败: {type(exc).__name__}: {exc}')
    print('请检查 Docker 网桥到 api.llmxt.cisctest:8002 的 DNS、路由、防火墙和 Key 权限。')
PY

port="${DWP_HTTP_PORT:-8080}"
host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo
echo "启动完成"
echo "本机访问: http://127.0.0.1:${port}"
if [ -n "$host_ip" ]; then
  echo "局域网访问: http://${host_ip}:${port}"
fi
echo "查看状态: ./status.sh"
echo "停止服务: ./stop.sh"
echo "备份数据: ./backup.sh"
