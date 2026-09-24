#!/bin/bash
# dwp AI员工平台 —— 堡垒机一键部署（离线 / 无外网）
# 用法: ./deploy.sh [--port 18080] [--skip-rebuild] [--no-probe]
set -Eeuo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=ops/lib.sh
source ./ops/lib.sh

PORT="${DWP_HTTP_PORT:-18080}"; SKIP_REBUILD=0; DO_PROBE=1
while [ $# -gt 0 ]; do
  case "$1" in
    --port)         PORT="${2:-}"; shift 2 ;;
    --skip-rebuild) SKIP_REBUILD=1; shift ;;
    --no-probe)     DO_PROBE=0; shift ;;
    -h|--help)      sed -n '2,3p' "$0"; exit 0 ;;
    *) echo "未知参数: $1" >&2; exit 2 ;;
  esac
done

ok(){ printf '  [ OK ] %s\n' "$*"; }
bad(){ printf '  [FAIL] %s\n' "$*" >&2; exit 1; }
warn(){ printf '  [WARN] %s\n' "$*"; }
hdr(){ printf '\n===== %s =====\n' "$*"; }

command -v docker >/dev/null 2>&1 || bad "未安装 Docker"
docker info >/dev/null 2>&1 || bad "当前用户无法访问 Docker（未启动或不在 docker 组）"
docker compose version >/dev/null 2>&1 || bad "未安装 Docker Compose v2"
[ -S /var/run/docker.sock ] || bad "缺少 /var/run/docker.sock"
[ "$(uname -m)" = "x86_64" ] || bad "架构 $(uname -m)，离线镜像是 linux/amd64"

hdr "0/8 预检"
docker_root="$(docker info --format '{{.DockerRootDir}}')"
avail_kb="$(df -Pk "$docker_root" | awk 'NR==2{print $4}')"
[ "${avail_kb:-0}" -ge 6291456 ] || bad "Docker 数据目录 $docker_root 可用不足 6 GiB"
ok "Docker $(docker version --format '{{.Server.Version}}')，数据目录 $docker_root 可用 $((avail_kb/1024/1024)) GiB"

# 数据盘带 noexec 时容器能创建、进程却起不来（overlay2 merged 目录里的 /bin/sh 无法 exec），
# runc 报的是一句没头没尾的 "starting container process caused permission denied"。
# 这类主机在导入镜像阶段完全看不出问题，所以必须在这里先卡住。
mount_point="$(df -P "$docker_root" 2>/dev/null | awk 'NR==2{print $6}')"
mount_opts=""
[ -n "$mount_point" ] && mount_opts="$(awk -v m="$mount_point" '$2==m {print $4; exit}' /proc/mounts 2>/dev/null)"
case ",$mount_opts," in
  *,noexec,*)
    bad "Docker 数据目录 $docker_root 所在文件系统（$mount_point）挂载选项含 noexec，容器无法启动
        修法：sudo mount -o remount,exec $mount_point（持久化写 /etc/fstab），或把 data-root 换到允许 exec 的文件系统" ;;
esac

# runc 版本：Docker 26 要求 runc >= 1.1.12。麒麟/信创机常自带 1.0.x 的 runc（或 podman 带的那个），
# 症状是「镜像导入成功、任何容器都起不来」，报错只有一句 starting container process caused
# "permission denied"，看不出跟 runc 有关。`runc features` 是 1.1.0 才有的子命令，用它判定最省事。
RUNC_VER=""
if command -v runc >/dev/null 2>&1; then
  RUNC_VER="$(runc -v 2>/dev/null | awk 'NR==1{print $3}')"
  if runc features >/dev/null 2>&1; then
    ok "runc ${RUNC_VER:-未知}（支持 features）"
  else
    warn "runc ${RUNC_VER:-未知} 过旧：不支持 runc features，Docker 需要 >= 1.1.12，容器很可能起不来"
    warn "  修法：换用官方 runc（docker 静态包里自带），见 2/8 的容器自检失败提示"
  fi
else
  warn "PATH 里找不到 runc 命令，无法确认运行时版本"
fi

# dsh 可插拔框架：三层（底座镜像 / runtime / profile）就位性预检
DSH_IMAGE="$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
RUNTIME_REL="$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
RUNTIME_DIR="$(runtime_dir_of "$RUNTIME_REL")"
PROFILE_REL="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"
PROFILE_DIR="$(abs_from_offline "$PROFILE_REL")"
[ -f versions.env ] || bad "缺少 versions.env（dsh 框架版本真相文件）"
for f in cordis.yml cordis.patch.yml package.json; do
  [ -f "$PROFILE_DIR/$f" ] || bad "dsh profile 缺失 $f：$PROFILE_REL"
done

# 需要就位的 runtime 集合（全局 + 全部 per-employee 覆盖）
RUNTIME_RELS=("$RUNTIME_REL")
for id in "${DWP_EMPLOYEE_IDS[@]}"; do
  override_rel="$(versions_value "$(runtime_key_for "$id")" '')"
  [ -n "$override_rel" ] || continue
  RUNTIME_RELS+=("$override_rel")
done
MISSING_RUNTIME=()
for rel in "${RUNTIME_RELS[@]}"; do
  [ -x "$(runtime_dir_of "$rel")/bin/dsh" ] || MISSING_RUNTIME+=("$rel")
done
if [ "${#MISSING_RUNTIME[@]}" -eq 0 ]; then
  ok "dsh 三层就位：镜像 $DSH_IMAGE / runtime $RUNTIME_REL ($(runtime_version_of "$RUNTIME_DIR")) / profile $PROFILE_REL"
else
  warn "以下 runtime 目录缺失，将在镜像导入后尝试从底座镜像现场物化："
  printf '        %s\n' "${MISSING_RUNTIME[@]}"
fi

if command -v ss >/dev/null 2>&1 && ss -ltnH "sport = :$PORT" 2>/dev/null | grep -q .; then
  if [ "$(docker compose ps --status running --services 2>/dev/null | grep -c '^frontend$' || true)" -gt 0 ]; then
    ok "端口 $PORT 已被本部署占用（重复执行，允许）"
  else
    bad "端口 $PORT 被其他服务占用，请用 --port 换一个"
  fi
else
  ok "端口 $PORT 空闲"
fi

[ -f images/dwp-ai-employee-platform-images-linux-amd64.tar ] || bad "缺少 images/dwp-ai-employee-platform-images-linux-amd64.tar"
for f in llm_api_key harness_signing_secret login_key_e10281 login_key_e20999 login_key_admin qieman_api_key verag_mcp_token ehome_mcp_api_key; do
  [ -f "secrets/$f" ] || bad "缺少 secrets/$f"
done
for f in llm_api_key login_key_e10281 login_key_e20999 login_key_admin verag_mcp_token; do
  [ -s "secrets/$f" ] || bad "secrets/$f 为空"
done
ok "8 个 secret 文件齐全（qieman 允许为空：无外网不启用）"

grep -qE '^DEEPSEEK_BASE_URL=https?://'        config.env || bad "config.env 未配置 DEEPSEEK_BASE_URL"
grep -qE '^DEEPSEEK_MODEL=.+'                  config.env || bad "config.env 未配置 DEEPSEEK_MODEL"
# 知识检索至少要有一种通道：测试走 REST，生产走 VeRAG MCP。
if ! grep -qE '^DWP_INTERNAL_KB_BASE_URL=https?://' config.env \
   && ! grep -qE '^DWP_VERAG_MCP_URL=https?://'      config.env; then
  bad "config.env 未配置任何知识通道（DWP_INTERNAL_KB_BASE_URL 与 DWP_VERAG_MCP_URL 均为空）"
fi
grep -qE '^DWP_VERAG_MCP_URL=https?://' config.env || warn "DWP_VERAG_MCP_URL 为空：生产环境知识通道（VeRAG）不可用"
grep -qE '^DWP_QIEMAN_MCP_URL=$' config.env || warn "DWP_QIEMAN_MCP_URL 非空，但本环境无外网"
grep -qE '^DWP_XYZQ_MCP_URL=$'   config.env || warn "DWP_XYZQ_MCP_URL 非空，但本环境无外网"
# 兴证e家走内网（10.32.253.43:80）。URL 置空时 seed 不会建插件行，界面上彻底不出现，
# 这是「端点不通就不显示」的首选做法（比注册出来再置灰干净）。
EHOME_URL_VALUE="$(awk -F= '$1=="DWP_EHOME_MCP_URL"{sub(/^[^=]*=/,""); gsub(/^"|"$/,""); print; exit}' config.env 2>/dev/null || true)"
if [ -n "$EHOME_URL_VALUE" ]; then
  EHOME_HOST="$(printf '%s' "$EHOME_URL_VALUE" | sed -E 's#^https?://([^/:]+).*#\1#')"
  if timeout 5 bash -c "cat < /dev/null > /dev/tcp/$EHOME_HOST/80" 2>/dev/null; then
    ok "兴证e家 MCP 端点可达：$EHOME_HOST:80"
  else
    warn "兴证e家 MCP 端点不可达（$EHOME_HOST:80）：需申请防火墙放通，否则该插件不出现"
  fi
  [ -s secrets/ehome_mcp_api_key ] || warn "secrets/ehome_mcp_api_key 为空：兴证e家插件不会注册（应用级 key 必需）"
else
  ok "未接入兴证e家员工服务 MCP（端点已置空，界面不出现该插件）"
fi
# 技能市场：DWP_SKILLHUB_BASE_URL 为空或 DWP_SKILLHUB_ENABLED=0 即视为未接入，
# 管理端「技能市场」页签不渲染，不算失败。
if grep -qE '^DWP_SKILLHUB_BASE_URL=https?://' config.env \
   && ! grep -qE '^DWP_SKILLHUB_ENABLED=0' config.env; then
  ok "SkillHub 已配置：管理端「技能市场」页签可用"
else
  ok "未接入 SkillHub（已关闭），管理端不显示「技能市场」页签"
fi
grep -qE '^DWP_DSH_IMAGE=.*-py$' versions.env \
  || warn "DWP_DSH_IMAGE 未指向带 Python 的底座镜像：技能脚本（python scripts/...）无法执行"
ok "config.env 关键项检查通过"

hdr "1/8 初始化目录与权限"
umask 077
mkdir -p secrets data/artifacts data/artifacts/skills data/attachments backups
for id in AI-GENERAL AI-INVESTMENT AI-RESEARCH AI-FUND AI-XYZQ DT-E10281 DT-E20999; do
  mkdir -p "harness/$id/dsh-home" "harness/$id/workspace"
done
[ -f secrets/qieman_api_key ] || : > secrets/qieman_api_key
# 兴证e家员工服务 MCP 的应用级 X-MCP-API-KEY（用户级 JWT 由员工在会话内补全）；
# 缺文件时 Docker 会把 bind 源建成 root 属主目录，必须先占位。
[ -f secrets/ehome_mcp_api_key ] || : > secrets/ehome_mcp_api_key
# 内部知识引擎 VeRAG MCP（生产环境知识通道）的 Bearer token；
# 缺文件时 Docker 会把 bind 源建成 root 属主目录，必须先占位。
[ -f secrets/verag_mcp_token ] || : > secrets/verag_mcp_token
chmod 600 secrets/*
chmod 700 secrets data backups harness
ok "7 个 Harness 工作区（含 AI-XYZQ）+ data/backups/secrets 就绪"

hdr "2/8 校验并导入离线镜像"
( cd images && sha256sum -c SHA256SUMS ) || bad "镜像 tar 校验失败，文件可能损坏"
ok "SHA256 校验通过"

need_load=0
for img in dwp-backend:0.4.0-offline dwp-frontend:0.4.0-offline dwp-frontend-builder:0.4.0-offline "$DSH_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || need_load=1
done
if [ "$need_load" -eq 1 ]; then
  docker load -i images/dwp-ai-employee-platform-images-linux-amd64.tar
  ok "镜像已导入"
else
  ok "所需镜像已存在，跳过导入"
fi

# 镜像导入成功 ≠ 容器跑得起来。数据盘 noexec、麒麟 KYSEC 的 exec 管控、AppArmor/SELinux
# 拦截、daemon.json 里配了 userns-remap，都会让容器在「创建成功、进程起不来」这一步失败
# （runc: starting container process caused "permission denied"）。在这里就试跑一次，
# 否则要等到 4/8 start.sh 才炸，报错还看不出是主机问题。
if ! smoke_out="$(docker run --rm --entrypoint /bin/sh dwp-backend:0.4.0-offline -c 'exit 0' 2>&1)"; then
  printf '        %s\n' "$smoke_out" >&2
  bad "镜像能导入但容器起不来。依次排查：
        1) runc 版本（当前 ${RUNC_VER:-未知}）：Docker 需要 >= 1.1.12，麒麟自带的多是 1.0.x
           验：runc features（报 No help topic for 'features' 即过旧）
           修：用官方 docker 静态包里的 runc 替换 /usr/bin/runc 后 systemctl restart docker
        2) 数据盘 noexec：$(df -P "$docker_root" 2>/dev/null | awk 'NR==2{print $6}') 的挂载选项
        3) 麒麟安全模块 exec 管控（KYSEC）：sudo getstatus / sudo setstatus softmode
        4) docker info 的 Security Options（AppArmor/SELinux）
        5) /etc/docker/daemon.json 是否配了 userns-remap"
fi
ok "容器可正常启动（运行环境自检通过）"

# 底座镜像 dwp-dsh:rc6-py 不在交付包的 images/*.tar 里（包内只有它构建时的基底 dwp-dsh:rc6），
# 但它完全可以离线构建：FROM 的是已导入的基底镜像，Python 运行时从已导入的 dwp-backend 镜像
# COPY 过来，构建期不联网、不装包。缺了就就地补建，不必为一张镜像重传整个安装包。
if ! docker image inspect "$DSH_IMAGE" >/dev/null 2>&1; then
  hdr "2c/8 就地构建 dsh 底座镜像（$DSH_IMAGE）"
  SRC_ROOT="$(cd "$PWD/../.." && pwd)"
  DSH_DOCKERFILE="$SRC_ROOT/docker/Dockerfile.dsh-runtime"
  [ -f "$DSH_DOCKERFILE" ] || bad "缺少 $DSH_DOCKERFILE，无法就地构建 $DSH_IMAGE"
  DSH_BUILD_BASE="$(versions_value DWP_DSH_BASE_IMAGE 'dwp-dsh:rc6')"
  docker image inspect "$DSH_BUILD_BASE" >/dev/null 2>&1 || bad "底座基础镜像不存在：$DSH_BUILD_BASE"
  PY_SOURCE_IMAGE=""
  for cand in dwp-backend:0.4.0-offline dwp-backend:0.3.0-offline \
              "$(docker images --format '{{.Repository}}:{{.Tag}}' \
                   | awk '$1 ~ /^dwp-backend:/ && $1 !~ /rollback/ {print $1; exit}')"; do
    [ -n "$cand" ] || continue
    if docker image inspect "$cand" >/dev/null 2>&1; then PY_SOURCE_IMAGE="$cand"; break; fi
  done
  [ -n "$PY_SOURCE_IMAGE" ] || bad "找不到 dwp-backend 镜像（Python 运行时来源），无法构建 $DSH_IMAGE"
  echo "     底座 $DSH_BUILD_BASE + Python 来源 $PY_SOURCE_IMAGE（离线构建，无网络请求）"
  build_ctx="$(mktemp -d)"
  docker build -f "$DSH_DOCKERFILE" \
    --build-arg "DSH_BASE_IMAGE=$DSH_BUILD_BASE" \
    --build-arg "PY_SOURCE_IMAGE=$PY_SOURCE_IMAGE" \
    -t "$DSH_IMAGE" "$build_ctx" || { rm -rf "$build_ctx"; bad "构建 $DSH_IMAGE 失败"; }
  rm -rf "$build_ctx"
  docker image inspect "$DSH_IMAGE" >/dev/null 2>&1 || bad "构建后仍找不到 $DSH_IMAGE"
  ok "已就地构建 $DSH_IMAGE（python3 标准库自检在构建期已通过）"
fi

if [ "${#MISSING_RUNTIME[@]}" -gt 0 ]; then
  hdr "2b/8 补齐 dsh runtime 层（从底座镜像物化）"
  for rel in "${MISSING_RUNTIME[@]}"; do
    dir="$(runtime_dir_of "$rel")"
    version="$(basename "$dir" | sed 's/^dsh-//')"
    if "$OPS_DIR/dsh-install-runtime.sh" "$version" --from-image >/dev/null 2>&1 && [ -x "$dir/bin/dsh" ]; then
      ok "runtime $rel 已从底座镜像物化（$version）"
    else
      bad "runtime $rel 缺失且无法从底座镜像物化；若该版本不是镜像内置版本，请让打包侧用 --runtime $rel 随包发布"
    fi
  done
  # 物化完成后把人可读指针指回 versions.env 声明的生效版本
  ln -sfn "$(basename "$RUNTIME_DIR")" runtime/current
  ok "runtime/current -> $(readlink runtime/current)"
fi

hdr "3/8 确定镜像 tag"
TAG="${DWP_IMAGE_TAG:-}"
if [ -z "$TAG" ] && docker image inspect dwp-backend:0.4.0-offline >/dev/null 2>&1; then TAG=0.4.0-offline; fi
if [ -z "$TAG" ] && [ -f .env ]; then TAG="$(awk -F= '$1=="DWP_IMAGE_TAG"{print $2; exit}' .env)"; fi
if [ -z "$TAG" ]; then
  TAG="$(docker images --format '{{.Repository}} {{.Tag}}' | awk '$1=="dwp-backend" && $2!="<none>" && $2 !~ /^rollback-/ {print $2; exit}')"
fi
[ -n "$TAG" ] || bad "找不到 dwp-backend 镜像 tag"
for img in "dwp-backend:$TAG" "dwp-frontend:$TAG" "dwp-frontend-builder:$TAG" "$DSH_IMAGE"; do
  docker image inspect "$img" >/dev/null 2>&1 || bad "镜像不存在：$img"
done
export DWP_IMAGE_TAG="$TAG"
ok "DWP_IMAGE_TAG=$TAG"

hdr "4/8 首次启动（建立 .env 与全部容器）"
export DWP_HTTP_PORT="$PORT"
./start.sh
ok "start.sh 完成"

if [ "$SKIP_REBUILD" -eq 1 ]; then
  hdr "5/8 跳过源码重建（--skip-rebuild）"
  warn "当前运行的是 0.4.0 镜像内 2026-09-10 的旧源码"
else
  hdr "5/8 用当前源码重建 backend / frontend"
  export DWP_SOURCE_ROOT=../..
  # 注意：`all` 已不再包含 harness-profile —— dsh 框架与 profile 现在由 runtime/ 与 profile/ 目录管理，
  # 重建镜像会把框架悄悄打回旧版本（历史上最危险的隐式回退点）。
  ./rebuild.sh all
  ok "backend / frontend 重建完成（每步已自动打 rollback-<时间戳> 备份 tag）"
fi

hdr "6/8 基础自检"
./verify.sh
ok "verify.sh 全部通过"

if [ "$DO_PROBE" -eq 1 ]; then
  hdr "7/8 端到端探测（容器内真实运行环境）"
  echo "--- 模型网关 ---"
  docker compose exec -T backend python -c "
import os, pathlib, httpx
key = pathlib.Path('/run/secrets/llm_api_key').read_text().strip()
base = os.environ['DEEPSEEK_BASE_URL'].rstrip('/')
path = os.environ.get('DEEPSEEK_CHAT_COMPLETIONS_PATH') or '/chat/completions'
r = httpx.post(base + path,
    headers={'Authorization': 'Bearer ' + key},
    json={'model': os.environ['DEEPSEEK_MODEL'],
          'messages': [{'role': 'user', 'content': 'ping'}], 'max_tokens': 8},
    timeout=60)
print('模型 HTTP %s | 含 choices: %s' % (r.status_code, 'choices' in r.text))
print(r.text[:200])
" || warn "模型探测执行失败（不阻塞部署）"

  echo "--- 内部知识引擎 ---"
  docker compose exec -T backend python -c "
from app.services.internal_knowledge_adapter import InternalKnowledgeAdapter
a = InternalKnowledgeAdapter()
for kb, q in [('KB-IT-SERVICE', 'IT服务'), ('KB-SECURITIES', '融资融券'), ('KB-INVESTMENT-BANKING', '投行承揽')]:
    try:
        r = a.search(employee_id='AI-GENERAL', knowledge_base_id=kb, query=q, trace_id='deploy-probe')
        print('OK   %-24s hits=%d | %s' % (kb, len(r['hits']), [h['title'] for h in r['hits'][:2]]))
    except Exception as exc:
        print('FAIL %-24s %s: %s' % (kb, type(exc).__name__, exc))
" || warn "知识库探测执行失败（不阻塞部署）"
else
  hdr "7/8 跳过端到端探测（--no-probe）"
fi

hdr "8/8 完成"
host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
echo "  访问地址 : http://${host_ip:-<堡垒机IP>}:$PORT"
echo "  管理后台 : http://${host_ip:-<堡垒机IP>}:$PORT/admin"
echo
echo "  登录 key（明文同时存在于 deploy/offline/secrets/）："
printf '    张三    : %s\n' "$(cat secrets/login_key_e10281)"
printf '    陈晓萌  : %s\n' "$(cat secrets/login_key_e20999)"
printf '    管理员  : %s\n' "$(cat secrets/login_key_admin)"
echo
echo "  镜像："
docker images --format '    {{.Repository}}:{{.Tag}}  {{.Size}}' | grep -E 'dwp-' | sort
echo
echo "  dsh 框架（三层可插拔）："
printf '    底座镜像 : %s\n' "$DSH_IMAGE"
printf '    runtime  : %s（%s）\n' "$RUNTIME_REL" "$(runtime_version_of "$RUNTIME_DIR")"
printf '    profile  : %s\n' "$PROFILE_REL"
echo
echo "  常用命令：./status.sh  ./verify.sh  ./backup.sh  ./stop.sh"
echo "  dsh 运维："
echo "    ./ops/dsh-status.sh                     查看每个实例实际生效的框架版本"
echo "    ./ops/dsh-switch.sh --all dsh-0.1.5-rc.1 切换框架版本（门禁 + 预演 + 冒烟 + 失败自动回滚）"
echo "    ./ops/dsh-switch.sh AI-GENERAL dsh-0.1.5-rc.1   按AI员工灰度"
echo "    ./ops/dsh-rollback.sh                   秒级回滚到上一版本"
echo "    ./ops/profile-switch.sh --list          查看/切换 Profile 版本"
