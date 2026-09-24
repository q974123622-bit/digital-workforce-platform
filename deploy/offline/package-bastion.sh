#!/bin/bash
# 开发机侧：组装「堡垒机可一键部署」的传输包
#
# 用法: ./package-bastion.sh [--all-runtime] [--runtime <dir-name>]...
#
#   --all-runtime      打包全部 runtime 版本目录（默认只打包「无法从底座镜像物化」的版本，
#                      其余版本由堡垒机侧 deploy.sh 用 `dsh-install-runtime.sh --from-image` 现场生成，
#                      可显著减小传输包体积）
#   --runtime <name>   额外强制包含某个 runtime 版本目录（可重复）
#
# 可用环境变量覆盖: IMAGES_DIR / OUT_ROOT
set -Eeuo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$(dirname "${BASH_SOURCE[0]}")"
# shellcheck source=ops/lib.sh
source ./ops/lib.sh

IMAGES_DIR="${IMAGES_DIR:-/root/digitalNPC/dwp-ai-employee-platform-offline-0.4.0-linux-amd64/images}"
OUT_ROOT="${OUT_ROOT:-/root/digitalNPC/bastion-bundle}"
STAMP="$(date +%Y%m%d-%H%M)"
STAGE="$OUT_ROOT/dwp-ai-employee-platform"
ARCHIVE="$OUT_ROOT/dwp-ai-employee-platform-bastion-$STAMP.tar.gz"
fail(){ echo "ERROR: $*" >&2; exit 1; }

ALL_RUNTIME=0; FORCE_RUNTIME=()
while [ $# -gt 0 ]; do
  case "$1" in
    --all-runtime) ALL_RUNTIME=1; shift ;;
    --runtime)     FORCE_RUNTIME+=("${2:-}"); shift 2 ;;
    -h|--help)     sed -n '2,12p' "$0"; exit 0 ;;
    *)             fail "未知参数: $1" ;;
  esac
done

[ -f "$IMAGES_DIR/dwp-ai-employee-platform-images-linux-amd64.tar" ] || fail "找不到镜像 tar：$IMAGES_DIR"
[ -f "$IMAGES_DIR/SHA256SUMS" ] || fail "找不到 $IMAGES_DIR/SHA256SUMS"
[ -d "$REPO_ROOT/backend/app" ] || fail "REPO_ROOT 不正确：$REPO_ROOT"
[ -f "$REPO_ROOT/deploy/offline/deploy.sh" ] || fail "缺少 deploy/offline/deploy.sh"
[ -f "$REPO_ROOT/deploy/offline/versions.env" ] || fail "缺少 deploy/offline/versions.env"

DSH_IMAGE="$(versions_value DWP_DSH_IMAGE 'dwp-dsh:rc6')"
GLOBAL_RUNTIME_REL="$(versions_value DWP_DSH_RUNTIME './runtime/dsh-0.1.0-rc.6')"
PROFILE_REL="$(resolve_under_offline "$(versions_value DWP_PROFILE './profile/current')")"

# 底座镜像内置的 dsh 版本：该版本可在堡垒机现场物化，无需随包传输
BAKED_VERSION=""
if docker image inspect "$DSH_IMAGE" >/dev/null 2>&1; then
  BAKED_VERSION="$(docker run --rm --entrypoint node "$DSH_IMAGE" -p \
    "require('/usr/local/lib/node_modules/@deepseek-ai/dsh/package.json').version" 2>/dev/null || true)"
fi

rm -rf "$STAGE"; mkdir -p "$STAGE"

echo "[1/8] 复制源码（排除 .git / node_modules / 运行数据 / runtime 与 profile 层）"
tar -C "$REPO_ROOT" \
  --exclude='./.git'            --exclude='./.git/*' \
  --exclude='*/.git'            --exclude='*/.git/*' \
  --exclude='*/node_modules'    --exclude='*/node_modules/*' \
  --exclude='*/__pycache__'     --exclude='*/__pycache__/*' \
  --exclude='*/.pytest_cache'   --exclude='*/.pytest_cache/*' \
  --exclude='*/.venv'           --exclude='*/.venv/*' \
  --exclude='*.pyc' --exclude='*.log' \
  --exclude='*.db' --exclude='*.db-shm' --exclude='*.db-wal' \
  --exclude='./frontend/dist'      --exclude='./frontend/dist/*' \
  --exclude='./frontend/coverage'  --exclude='./frontend/coverage/*' \
  --exclude='./deploy/offline/data'     --exclude='./deploy/offline/data/*' \
  --exclude='./deploy/offline/backups'  --exclude='./deploy/offline/backups/*' \
  --exclude='./deploy/offline/images'   --exclude='./deploy/offline/images/*' \
  --exclude='./deploy/offline/harness'  --exclude='./deploy/offline/harness/*' \
  --exclude='./deploy/offline/runtime'  --exclude='./deploy/offline/runtime/*' \
  --exclude='./deploy/offline/ops/logs' --exclude='./deploy/offline/ops/logs/*' \
  --exclude='./deploy/offline/ops/state' --exclude='./deploy/offline/ops/state/*' \
  --exclude='./deploy/offline/ops/baseline/canary-*' \
  --exclude='./deploy/offline/ops/baseline/*.latest.txt' \
  --exclude='./deploy/offline/ops/baseline/*.stderr.txt' \
  --exclude='./deploy/offline/ops/baseline/gate-*.json' \
  --exclude='./deploy/offline/.env' \
  -cf - . | tar -C "$STAGE" -xf -

echo "[2/8] 重建 Harness 工作区骨架（含 AI-XYZQ）与运行目录"
for id in AI-GENERAL AI-INVESTMENT AI-RESEARCH AI-FUND AI-XYZQ DT-E10281 DT-E20999; do
  mkdir -p "$STAGE/deploy/offline/harness/$id/dsh-home" "$STAGE/deploy/offline/harness/$id/workspace"
done
mkdir -p "$STAGE/deploy/offline/data/artifacts" "$STAGE/deploy/offline/data/attachments" \
         "$STAGE/deploy/offline/backups" "$STAGE/deploy/offline/secrets" \
         "$STAGE/deploy/offline/runtime" "$STAGE/deploy/offline/ops/logs" "$STAGE/deploy/offline/ops/state"

echo "[3/8] 复制 dsh 框架层（runtime）与 Profile 层（profile）"
# runtime 目录里全是 node_modules，会被上面的 --exclude 误伤，因此整体排除后单独 cp -a 复制
shipped=(); materialized=()
for d in "$RUNTIME_ROOT"/*/; do
  [ -d "$d" ] || continue
  [ -L "${d%/}" ] && continue
  name="$(basename "$d")"
  [ -x "$d/bin/dsh" ] || continue
  version="$(runtime_version_of "$d")"
  forced=0
  for f in ${FORCE_RUNTIME[@]+"${FORCE_RUNTIME[@]}"}; do
    [ "$f" = "$name" ] && forced=1
  done
  if [ "$ALL_RUNTIME" -eq 0 ] && [ -n "$BAKED_VERSION" ] && [ "$version" = "$BAKED_VERSION" ] && [ "$forced" -eq 0 ]; then
    materialized+=("$name")
    continue
  fi
  cp -a "$d" "$STAGE/deploy/offline/runtime/$name"
  shipped+=("$name")
done
# runtime/current 软链：优先指向本次交付生效的版本；若该版本改为现场物化，
# 则临时指向一个随包发布的版本，并由 deploy.sh / start.sh 在物化后重新指回。
CURRENT_NAME="$(basename "$(runtime_dir_of "$GLOBAL_RUNTIME_REL")")"
CURRENT_SHIPPED=0
for name in ${shipped[@]+"${shipped[@]}"}; do [ "$name" = "$CURRENT_NAME" ] && CURRENT_SHIPPED=1; done
if [ "$CURRENT_SHIPPED" -eq 1 ]; then
  ln -sfn "$CURRENT_NAME" "$STAGE/deploy/offline/runtime/current"
elif [ "${#shipped[@]}" -gt 0 ]; then
  ln -sfn "${shipped[0]}" "$STAGE/deploy/offline/runtime/current"
  warn "生效版本 $CURRENT_NAME 改为现场物化，包内 runtime/current 暂指向 ${shipped[0]}；"
  warn "  deploy.sh 物化后会重新指回 $CURRENT_NAME，start.sh 也会按 versions.env 同步该指针。"
else
  ln -sfn "$CURRENT_NAME" "$STAGE/deploy/offline/runtime/current"
fi
cp -a "$PROFILE_ROOT/." "$STAGE/deploy/offline/profile/"
if [ "${#shipped[@]}" -gt 0 ]; then
  echo "     随包发布 runtime: ${shipped[*]}"
else
  echo "     随包发布 runtime: （无，全部现场物化）"
fi
if [ "${#materialized[@]}" -gt 0 ]; then
  echo "     现场物化 runtime: ${materialized[*]}（版本与底座镜像内置一致，deploy.sh 会自动生成）"
fi
echo "     生效版本: $CURRENT_NAME ；profile: $PROFILE_REL"

echo "[4/8] 收集真实凭据（从本机 secrets/ 读取；脚本内不内置任何凭据）"
umask 077
S="$STAGE/deploy/offline/secrets"
SRC_SECRETS="$REPO_ROOT/deploy/offline/secrets"
# 与 compose.yaml 声明的 secret 一一对应；缺一个就直接失败，避免打出「装得上但跑不起来」的包。
# 用户级 X-MCP-JWT 按人签发、会过期，绝不打包：由员工在会话内的「信息补全」气泡里
# 粘贴一次，平台加密存到本人名下后复用。
SECRET_FILES=(
  llm_api_key
  login_key_e10281
  login_key_e20999
  login_key_admin
  qieman_api_key
  harness_signing_secret
  verag_mcp_token
  ehome_mcp_api_key
)
for name in "${SECRET_FILES[@]}"; do
  [ -s "$SRC_SECRETS/$name" ] || fail "缺少凭据 $SRC_SECRETS/$name；请先准备 deploy/offline/secrets/ 下的凭据文件（本脚本不再内置凭据）"
  cp "$SRC_SECRETS/$name" "$S/$name"
done
chmod 600 "$S"/*; chmod 700 "$S"

echo "[5/8] 关闭目标环境不可达的外部服务（必须在首次启动前）"
# 四条都必须在这里改，启动之后再改来不及：seed 只 insert 不 delete，已建的插件行不会被清掉。
#   盈米 stargate.yingmi.com / 兴业 developer.xyzq.cn —— 无外网，不可达；
#   兴证e家 10.32.253.43 —— 目标堡垒机同样不通，按「不通就不显示」一并置空。
#   seed.ensure_ehome_mcp() 在 URL 或应用级 key 为空时直接 return，Plugin 行根本不会创建，
#   界面上（插件市场、MCP 接入、运行能力核验）彻底不出现。
#   SkillHub 10.49.70.2 同样不通 → 关闭，管理端「技能市场」页签不渲染。
sed -i -E 's|^DWP_QIEMAN_MCP_URL=.*|DWP_QIEMAN_MCP_URL=|;
           s|^DWP_XYZQ_MCP_URL=.*|DWP_XYZQ_MCP_URL=|;
           s|^DWP_EHOME_MCP_URL=.*|DWP_EHOME_MCP_URL=|;
           s|^DWP_SKILLHUB_BASE_URL=.*|DWP_SKILLHUB_BASE_URL=|;
           s|^DWP_SKILLHUB_ENABLED=.*|DWP_SKILLHUB_ENABLED=0|' \
  "$STAGE/deploy/offline/config.env"
chmod 600 "$STAGE/deploy/offline/config.env"
chmod +x "$STAGE/deploy/offline/install.sh" 2>/dev/null || true

echo "[6/8] 复制离线镜像（原样，保持 SHA256SUMS 有效）"
cp -a "$IMAGES_DIR" "$STAGE/deploy/offline/images"
chmod +x "$STAGE"/deploy/offline/*.sh "$STAGE"/deploy/offline/ops/*.sh 2>/dev/null || true
chmod +x "$STAGE"/scripts/*.sh 2>/dev/null || true

# images/*.tar 是原样复制的（SHA256SUMS 必须保持有效），不能往里塞镜像；但必须核对底座镜像在不在。
# dwp-dsh:rc6-py 由 docker/Dockerfile.dsh-runtime 构建，历史上没进过这个 tar（里面只有它的基底
# dwp-dsh:rc6），目标机上会以「镜像不存在：dwp-dsh:rc6-py」在 deploy.sh 第 3 步失败。
# 缺了不算致命（deploy.sh 会用包内 Dockerfile 就地离线构建），但必须写进交付说明。
DSH_IN_TAR=no
if tar -xOf "$STAGE/deploy/offline/images/dwp-ai-employee-platform-images-linux-amd64.tar" manifest.json 2>/dev/null \
     | grep -qF "\"$DSH_IMAGE\""; then
  DSH_IN_TAR=yes
else
  warn "images/*.tar 不含底座镜像 $DSH_IMAGE"
  warn "  目标机 deploy.sh 会用 docker/Dockerfile.dsh-runtime 就地离线构建"
  warn "  （前提：tar 内已有基底 dwp-dsh:rc6 与 Python 来源 dwp-backend）"
fi

echo "[7/8] 写入版本说明与一键部署脚本"
# 包根放一份 install.sh：上传后 `bash install.sh <包.tar.gz>` 就能跑完全流程。
cp "$STAGE/deploy/offline/install.sh" "$STAGE/install.sh"
cp "$STAGE/deploy/offline/install.sh" "$OUT_ROOT/install.sh"
chmod +x "$STAGE/install.sh" "$OUT_ROOT/install.sh"
{
  echo "packaged_at_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  echo "source_repo=$REPO_ROOT"
  echo "source_policy=working tree; excluded .git/node_modules/__pycache__/frontend-dist/deploy-offline-data/deploy-offline-harness-runtime/deploy-offline-runtime-layer/backups/.env/ops-logs"
  echo "image_archive=deploy/offline/images/dwp-ai-employee-platform-images-linux-amd64.tar"
  echo "image_sha256=$(awk '{print $1}' "$STAGE/deploy/offline/images/SHA256SUMS")"
  echo "image_tag=0.4.0-offline"
  echo "model_gateway=http://10.56.35.240:8002/v1"
  echo "model_name=ascend-qwen3.6-27b"
  echo "internal_kb=http://10.57.180.65"
  echo "external_mcp=disabled_no_egress"
  echo "one_click=bash install.sh <package.tar.gz> [install_dir]"
  echo "# 以下外部服务在目标环境不可达，已在 config.env 中关闭，界面不出现："
  echo "disabled_services=qieman_mcp,xyzq_mcp,ehome_mcp,skillhub_market"
  echo "# ---- dsh 可插拔框架（三层）----"
  echo "dsh_base_image=$DSH_IMAGE"
  echo "dsh_base_image_in_tar=$DSH_IN_TAR"
  echo "dsh_base_baked_version=${BAKED_VERSION:-unknown}"
  echo "dsh_runtime_shipped=$(printf '%s ' ${shipped[@]+"${shipped[@]}"})"
  echo "dsh_runtime_materialize_on_bastion=$(printf '%s ' ${materialized[@]+"${materialized[@]}"})"
  echo "dsh_runtime_current=$CURRENT_NAME"
  echo "dsh_profile_rev=$(basename "$PROFILE_REL")"
  echo "dsh_gate_contract=deploy/offline/ops/baseline/contract.json"
} > "$STAGE/SOURCE-REVISION.txt"

echo "[8/8] 打包"
mkdir -p "$OUT_ROOT"
tar -C "$OUT_ROOT" -czf "$ARCHIVE" "$(basename "$STAGE")"
( cd "$OUT_ROOT" && sha256sum "$(basename "$ARCHIVE")" | tee "$(basename "$ARCHIVE").sha256" )
chmod 644 "$ARCHIVE" "$ARCHIVE.sha256"
echo
echo "源目录 : $STAGE"
echo "压缩包 : $ARCHIVE"
du -sh "$STAGE" "$ARCHIVE"
