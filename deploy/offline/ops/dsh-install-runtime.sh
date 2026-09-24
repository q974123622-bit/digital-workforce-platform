#!/bin/bash
# dwp AI员工平台 —— 生成一个 dsh 框架版本目录（L1 框架层）
#
# 用法:
#   ./ops/dsh-install-runtime.sh <version> [选项]
#
#   <version>          例如 0.1.5-rc.1（也可写 dsh-0.1.5-rc.1）
#   --from-image       从底座镜像内置的全局 node_modules 物化（仅当版本与镜像一致）
#   --registry URL     指定 npm 源；默认 https://mirrors.huaweicloud.com/repository/npm/
#   --force            目标目录已存在时先删除再重建
#   -h, --help         显示帮助
#
# 产物布局（npm global prefix 布局，挂载到容器 /opt/dsh-runtime 后 bin/dsh 可直接执行）:
#   runtime/dsh-<version>/bin/dsh -> ../lib/node_modules/@deepseek-ai/dsh/lib/bin.js
#   runtime/dsh-<version>/lib/node_modules/@deepseek-ai/dsh/...
#   runtime/dsh-<version>/MANIFEST.json
#
# 说明:
#   - 容器内以当前宿主机用户身份运行（--user），产物归属宿主机用户，堡垒机上 xyzqadmin 可直接增删。
#   - 本机公网 npm 被 SNI 阻断，默认使用华为云源；如需换源用 --registry。
#   - 生成后必须执行 ./ops/dsh-preflight.sh <version> 完成契约门禁。
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

VERSION=""; FROM_IMAGE=0; REGISTRY=""; FORCE=0

while [ $# -gt 0 ]; do
  case "$1" in
    --from-image) FROM_IMAGE=1; shift ;;
    --registry)   REGISTRY="${2:-}"; shift 2 ;;
    --force)      FORCE=1; shift ;;
    -h|--help)    sed -n '2,20p' "$0"; exit 0 ;;
    -*)           bad "未知参数: $1" ;;
    *)            VERSION="$1"; shift ;;
  esac
done

[ -n "$VERSION" ] || bad "用法: ./ops/dsh-install-runtime.sh <version> [--from-image] [--registry URL] [--force]"
VERSION="${VERSION#dsh-}"
NAME="$(runtime_name_of_version "$VERSION")"
TARGET="$RUNTIME_ROOT/$NAME"
BASE_IMAGE="$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
[ -n "$REGISTRY" ] || REGISTRY="$(versions_value DWP_DSH_NPM_REGISTRY 'https://mirrors.huaweicloud.com/repository/npm/')"

require_docker
mkdir -p -m 0755 "$RUNTIME_ROOT"
docker image inspect "$BASE_IMAGE" >/dev/null 2>&1 || bad "底座镜像不存在：$BASE_IMAGE（请先导入离线镜像）"

hdr "0/4 目标检查"
if [ -d "$TARGET/lib/node_modules/@deepseek-ai/dsh" ] && [ "$FORCE" -eq 0 ]; then
  ok "已存在，跳过：runtime/$NAME（版本 $(runtime_version_of "$NAME")）"
  info "需要重建请加 --force"
  exit 0
fi
info "目标：runtime/$NAME"
[ "$FORCE" -eq 1 ] && warn "--force：将删除并重建 runtime/$NAME"
rm -rf "$TARGET"
mkdir -p "$TARGET/lib" "$TARGET/bin"

docker_args=(--rm --user "$(id -u):$(id -g)" -e HOME=/tmp -e npm_config_cache=/tmp/.npm-cache -e npm_config_update_notifier=false)

if [ "$FROM_IMAGE" -eq 1 ]; then
  hdr "1/4 从底座镜像物化 dsh（$BASE_IMAGE）"
  docker run "${docker_args[@]}" --entrypoint sh -v "$TARGET:/out" "$BASE_IMAGE" -c '
    set -eu
    baked="$(node -p "require(\"/usr/local/lib/node_modules/@deepseek-ai/dsh/package.json\").version")"
    echo "baked=$baked"
    [ "$baked" = "'"$VERSION"'" ] || { echo "镜像内置版本 $baked 与请求的 '"$VERSION"' 不一致" >&2; exit 3; }
    cp -a --no-preserve=ownership /usr/local/lib/node_modules /out/lib/node_modules
    ln -sfn ../lib/node_modules/@deepseek-ai/dsh/lib/bin.js /out/bin/dsh
  ' || bad "从镜像物化失败（版本不一致或复制失败）"
  SOURCE="image:$BASE_IMAGE"
  ok "已物化 runtime/$NAME"
else
  hdr "1/4 从 npm 源安装 dsh@$VERSION"
  info "registry = $REGISTRY"
  docker run "${docker_args[@]}" --entrypoint sh -v "$TARGET:/out" "$BASE_IMAGE" -c '
    set -eu
    npm install -g --prefix /out --registry "'"$REGISTRY"'" \
      --no-audit --no-fund --loglevel=error "@deepseek-ai/dsh@'"$VERSION"'"
  ' || bad "npm 安装失败（源不可达或版本不存在）"
  SOURCE="registry:$REGISTRY"
  ok "已安装 runtime/$NAME"
fi

hdr "2/4 校验布局与入口"
[ -x "$TARGET/bin/dsh" ] || bad "缺少可执行入口 runtime/$NAME/bin/dsh"
[ -f "$TARGET/lib/node_modules/@deepseek-ai/dsh/package.json" ] || bad "缺少 dsh 包目录"
INSTALLED="$(sed -n 's/.*"version"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' \
  "$TARGET/lib/node_modules/@deepseek-ai/dsh/package.json" | head -1)"
[ "$INSTALLED" = "$VERSION" ] || bad "安装版本 $INSTALLED 与请求版本 $VERSION 不一致"
BIN_TARGET="$(readlink "$TARGET/bin/dsh" 2>/dev/null || true)"
ok "dsh 版本 $INSTALLED，入口 bin/dsh -> ${BIN_TARGET:-<非符号链接>}"

hdr "3/4 计算目录指纹"
TREE_SHA="$(cd "$TARGET" && find lib bin -type f -print0 | sort -z | xargs -0 sha256sum | sha256sum | awk '{print $1}')"
SIZE_BYTES="$(du -sb "$TARGET" | awk '{print $1}')"
NODE_VERSION="$(docker run "${docker_args[@]}" --entrypoint node "$BASE_IMAGE" -p 'process.version' 2>/dev/null || echo unknown)"
ok "tree_sha256=${TREE_SHA:0:16}…  体积 $((SIZE_BYTES/1024/1024)) MiB  node $NODE_VERSION"

hdr "4/4 写入 MANIFEST.json"
cat > "$TARGET/MANIFEST.json" <<EOF
{
  "version": "$INSTALLED",
  "dir": "$NAME",
  "layer": "L1-runtime",
  "source": "$SOURCE",
  "base_image": "$BASE_IMAGE",
  "node_version": "$NODE_VERSION",
  "entrypoint": "bin/dsh -> ${BIN_TARGET:-<none>}",
  "mount_target": "/opt/dsh-runtime",
  "installed_at": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "installed_by": "ops/dsh-install-runtime.sh",
  "tree_sha256": "$TREE_SHA",
  "size_bytes": $SIZE_BYTES,
  "gate_file": "GATE.json"
}
EOF
chmod 644 "$TARGET/MANIFEST.json"
ok "runtime/$NAME 就绪"
echo
echo "下一步：./ops/dsh-preflight.sh $NAME      # 契约门禁"
echo "灰度：  ./ops/dsh-switch.sh AI-GENERAL $NAME"
