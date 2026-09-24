#!/bin/bash
# 探测容器内能否访问 npm registry —— 决定 dsh 升级走哪条渠道
#
# 用法:
#   ./probe-dsh-registry.sh                     # 用默认镜像 dwp-dsh:rc6
#   DSH_IMAGE=dwp-dsh:rc7 ./probe-dsh-registry.sh
#
# 2026-09-17 开发机实测结论：公网 npm 被 SNI 阻断，华为云源可用 -> 渠道 A'
set -uo pipefail

IMAGE="${DSH_IMAGE:-dwp-dsh:rc6}"
PROBE_TIMEOUT=8
# 首选源取自 versions.env（唯一版本真相），其余为候选
PREFERRED="$(awk -F= '$1=="DWP_DSH_NPM_REGISTRY"{sub(/^[^=]*=/,""); print; exit}' versions.env 2>/dev/null || true)"
CANDIDATES=(
  "${PREFERRED:-https://mirrors.huaweicloud.com/repository/npm/}"
  "https://mirrors.huaweicloud.com/repository/npm/"
  "https://registry.yarnpkg.com"
  "https://mirrors.aliyun.com/npm/"
  "https://registry.npmjs.org/"
  "https://registry.npmmirror.com"
)
hdr(){ printf '\n===== %s =====\n' "$*"; }

hdr "0/5 前置检查"
docker info >/dev/null 2>&1 && echo "  [ OK ] Docker daemon 可用" \
  || { echo "  [FAIL] Docker daemon 不可用"; exit 1; }
docker image inspect "$IMAGE" >/dev/null 2>&1 && echo "  [ OK ] 镜像存在：$IMAGE" \
  || { echo "  [FAIL] 镜像不存在：$IMAGE"; exit 1; }
mkdir -p -m 0700 /run/user/0 2>/dev/null || true

hdr "1/5 容器内 registry 配置"
docker run --rm --entrypoint sh "$IMAGE" -c \
  'npm config get registry; echo "--- 自定义 npmrc ---";
   ls -la /usr/local/etc/npmrc /root/.npmrc /etc/npmrc 2>/dev/null || echo "(无自定义 npmrc)";
   echo "--- npm / node ---"; npm -v; node -v'

hdr "2/5 DNS 解析"
docker run --rm --entrypoint node "$IMAGE" -e "
const d=require('dns');
const names=['registry.npmjs.org','registry.npmmirror.com','mirrors.huaweicloud.com','www.baidu.com'];
Promise.all(names.map(n=>d.promises.resolve4(n)
  .then(a=>console.log('  OK   '+n+' -> '+a.join(',')))
  .catch(e=>console.log('  FAIL '+n+' '+e.code))));
"

hdr "3/5 HTTPS 连通性（各 ${PROBE_TIMEOUT}s 超时）"
docker run --rm --entrypoint node "$IMAGE" -e "
const t=${PROBE_TIMEOUT}000;
const urls=['https://registry.npmjs.org/','https://registry.npmmirror.com/','https://www.baidu.com/'];
Promise.all(urls.map(u=>fetch(u,{signal:AbortSignal.timeout(t)})
  .then(r=>console.log('  HTTP '+r.status+'  '+u))
  .catch(e=>console.log('  ERR  '+(e.cause&&e.cause.code||e.name)+'  '+u))));
"

hdr "4/5 候选 npm 源元数据探测"
for reg in "${CANDIDATES[@]}"; do
  code=$(timeout 30 docker run --rm --entrypoint sh "$IMAGE" -c \
    "npm view @deepseek-ai/dsh version --registry '$reg' 2>/dev/null" 2>/dev/null | tail -1)
  if [ -n "$code" ]; then
    echo "  [ OK ] $reg  -> version=$code"
  else
    echo "  [FAIL] $reg"
  fi
done

hdr "5/5 上游可用版本（取第一个可用源）"
for reg in "${CANDIDATES[@]}"; do
  out=$(timeout 120 docker run --rm --entrypoint npm "$IMAGE" view @deepseek-ai/dsh versions --json \
        --registry "$reg" 2>/dev/null)
  if [ -n "$out" ]; then
    echo "  源: $reg"
    echo "$out"
    echo "--- dist-tags ---"
    timeout 60 docker run --rm --entrypoint npm "$IMAGE" view @deepseek-ai/dsh dist-tags --json \
      --registry "$reg" 2>/dev/null
    break
  fi
done

hdr "当前安装版本"
docker run --rm --entrypoint sh "$IMAGE" -c 'npm ls -g --depth=0 2>/dev/null'

hdr "判定标准"
cat <<'EOF'
  公网 npm 200                     -> 渠道 A ：改 pin 即可
  公网被阻断 + 国内源 OK           -> 渠道 A'：Dockerfile 加 --registry <可用源>   ← 2026-09-17 实测
  全部 FAIL                        -> 渠道 C/D：索取 .tgz 或上游镜像 tar
  注意：registry.yarnpkg.com 元数据可用，但其 dist.tarball 指回 registry.npmjs.org，
        因此只能用于查询版本，不能用于安装。
EOF
