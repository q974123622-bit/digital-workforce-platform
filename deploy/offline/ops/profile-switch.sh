#!/bin/bash
# dwp AI员工平台 —— Profile 层（L2）管理与切换
#
# 取代旧的 `./rebuild.sh harness-profile`（后者要重建 638MB 镜像）。本脚本只做：
#   1) 切换 profile/current 指针（软链）+ 更新 versions.env 的 DWP_PROFILE_REV
#   2) 重新展开 .env 并仅重建受影响的 harness 容器（entrypoint 会把新 profile 复制进 dsh-home）
#   3) 逐容器比对 profile 三件套 sha256，确认切换真实生效
#
# 用法:
#   ./ops/profile-switch.sh --list                              # 列出可用版本与当前指针
#   ./ops/profile-switch.sh v1-20260910 --all                   # 全量切到 v1-20260910
#   ./ops/profile-switch.sh v1-20260910 AI-GENERAL              # 灰度：只切 AI-GENERAL
#   ./ops/profile-switch.sh --new-rev v2-20261001               # 从 docker/dsh-profile 建新版本（并切换）
#   ./ops/profile-switch.sh --new-rev v2-20261001 --from <dir>  # 从指定目录建新版本
#
# 说明：cordis.yml 会被 dsh 在运行时改写（追加注释头），因此该文件只做语义比对（去注释后必须是 `[]`），
#       cordis.patch.yml / package.json 做字节级 sha256 比对。
set -Eeuo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
# shellcheck source=lib.sh
source ./ops/lib.sh

REV=""; ALL=0; LIST=0; REINDEX=0; NEW_REV=""; FROM_DIR=""; TARGETS=()
while [ $# -gt 0 ]; do
  case "$1" in
    --all)     ALL=1; shift ;;
    --list)    LIST=1; shift ;;
    --reindex) REINDEX=1; shift ;;
    --new-rev) NEW_REV="${2:-}"; shift 2 ;;
    --from)    FROM_DIR="${2:-}"; shift 2 ;;
    -h|--help) sed -n '2,20p' "$0"; exit 0 ;;
    -*)        bad "未知参数: $1" ;;
    *) if [ -z "$REV" ]; then REV="$1"; else TARGETS+=("$(normalize_employee_id "$1")"); fi; shift ;;
  esac
done

# 去掉 cordis.yml 的注释与空行，得到语义内容（dsh 会往里追加注释头）
cordis_semantic() { sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' "$1" | tr -d '[:space:]'; }

# 从 cordis.patch.yml 提取 disabled 的 id 列表（单行，空格分隔）
profile_disabled_ids() {
  awk '/^- id: /{id=$3} /^  disabled: true/{if (id != "") printf "%s ", id; id=""}' "$1"
}

# 生成单个 Profile 版本的元数据清单
write_profile_rev_manifest() {
  local rev_dir="$1" rev tmp disabled disabled_json="" id
  rev="$(basename "$rev_dir")"
  disabled="$(profile_disabled_ids "$rev_dir/cordis.patch.yml")"
  for id in $disabled; do
    disabled_json="${disabled_json}${disabled_json:+, }\"$id\""
  done
  tmp="$(mktemp "$rev_dir/MANIFEST.json.XXXXXX")"
  {
    printf '{\n'
    printf '  "rev": "%s",\n' "$rev"
    printf '  "layer": "L2-profile",\n'
    printf '  "mount_target": "/opt/dwp/profile",\n'
    printf '  "created_at": "%s",\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    printf '  "files": {\n'
    printf '    "cordis.yml":        { "sha256": "%s", "note": "空列表占位；dsh 运行时会在文件头追加注释，故只做语义比对" },\n' \
      "$(sha256sum "$rev_dir/cordis.yml" | awk '{print $1}')"
    printf '    "cordis.patch.yml":  { "sha256": "%s" },\n' "$(sha256sum "$rev_dir/cordis.patch.yml" | awk '{print $1}')"
    printf '    "package.json":      { "sha256": "%s" }\n' "$(sha256sum "$rev_dir/package.json" | awk '{print $1}')"
    printf '  },\n'
    printf '  "cordis_semantic": "%s",\n' "$(cordis_semantic "$rev_dir/cordis.yml")"
    printf '  "disabled_tools": [ %s ],\n' "$disabled_json"
    printf '  "bundles": [ "@deepseek-ai/dsh-base", "@deepseek-ai/dsh-headless" ]\n'
    printf '}\n'
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$rev_dir/MANIFEST.json"
}

# 重新生成 profile 根索引（纯 shell，不依赖 jq/python）
regenerate_profile_index() {
  local tmp first=1 d rev created
  tmp="$(mktemp "$PROFILE_ROOT/MANIFEST.json.XXXXXX")"
  {
    printf '{\n'
    printf '  "layer": "L2-profile",\n'
    printf '  "mount_target": "/opt/dwp/profile",\n'
    printf '  "pointer": "current",\n'
    printf '  "current_rev": "%s",\n' "$(readlink "$PROFILE_ROOT/current" 2>/dev/null || echo '')"
    printf '  "note": "本文件位于 profile 根目录，不随 current 指针挂载进容器；由 ops/profile-switch.sh 自动生成。",\n'
    printf '  "revisions": [\n'
    for d in "$PROFILE_ROOT"/*/; do
      [ -d "$d" ] || continue
      [ -L "${d%/}" ] && continue   # 跳过 current 指针软链
      rev="$(basename "$d")"
      [ -f "$d/MANIFEST.json" ] || continue
      created="$(sed -n 's/.*"created_at"[[:space:]]*:[[:space:]]*"\([^"]*\)".*/\1/p' "$d/MANIFEST.json" | head -1)"
      [ "$first" -eq 1 ] || printf ',\n'
      printf '    { "rev": "%s", "created_at": "%s" }' "$rev" "$created"
      first=0
    done
    printf '\n  ]\n}\n'
  } > "$tmp"
  chmod 644 "$tmp"
  mv -f "$tmp" "$PROFILE_ROOT/MANIFEST.json"
}

resolve_from_dir() {
  local d="$1" cand
  case "$d" in
    /*) [ -d "$d" ] && { printf '%s' "$d"; return 0; } ;;
    *)  for cand in "$OFFLINE_DIR/$d" "$OFFLINE_DIR/../../$d"; do
          [ -d "$cand" ] && { printf '%s' "$cand"; return 0; }
        done ;;
  esac
  return 1
}

if [ "$REINDEX" -eq 1 ]; then
  hdr "重建 Profile 元数据"
  for d in "$PROFILE_ROOT"/*/; do
    [ -d "$d" ] || continue
    [ -L "${d%/}" ] && continue
    [ -f "$d/cordis.patch.yml" ] || continue
    write_profile_rev_manifest "$d"
    ok "profile/$(basename "$d")/MANIFEST.json"
  done
  regenerate_profile_index
  ok "profile/MANIFEST.json（索引）"
  exit 0
fi

if [ "$LIST" -eq 1 ]; then
  hdr "可用 Profile 版本"
  for d in "$PROFILE_ROOT"/*/; do
    [ -d "$d" ] || continue
    [ -L "${d%/}" ] && continue
    name="$(basename "$d")"
    mark=""
    [ "$(readlink "$PROFILE_ROOT/current" 2>/dev/null || true)" = "$name" ] && mark="  <- current"
    printf '  %-20s%s\n' "$name" "$mark"
  done
  echo
  info "当前指针：$(readlink "$PROFILE_ROOT/current" 2>/dev/null || echo '<未设置>')"
  exit 0
fi

# ---- 可选：从某个目录创建新的 Profile 版本 ----
if [ -n "$NEW_REV" ]; then
  case "$NEW_REV" in
    */|*..*) bad "版本名不合法：$NEW_REV" ;;
  esac
  SRC_DIR="$(resolve_from_dir "${FROM_DIR:-docker/dsh-profile}")" \
    || bad "找不到来源目录：${FROM_DIR:-docker/dsh-profile}"
  for f in cordis.yml cordis.patch.yml package.json; do
    [ -f "$SRC_DIR/$f" ] || bad "来源目录缺少 $f：$SRC_DIR"
  done
  hdr "创建 Profile 版本 $NEW_REV"
  info "来源：$SRC_DIR"
  mkdir -p "$PROFILE_ROOT/$NEW_REV"
  cp "$SRC_DIR/cordis.yml" "$SRC_DIR/cordis.patch.yml" "$SRC_DIR/package.json" "$PROFILE_ROOT/$NEW_REV/"
  write_profile_rev_manifest "$PROFILE_ROOT/$NEW_REV"
  ok "已创建 profile/$NEW_REV（含 MANIFEST.json）"
  REV="$NEW_REV"
fi

[ -n "$REV" ] || bad "用法: ./ops/profile-switch.sh <rev> [EMPLOYEE_ID ...] | --all | --list | --new-rev <rev> [--from <dir>]"
SRC="$PROFILE_ROOT/$REV"
[ -d "$SRC" ] || bad "Profile 版本不存在：profile/$REV（可用 --list 查看）"
for f in cordis.yml cordis.patch.yml package.json; do
  [ -f "$SRC/$f" ] || bad "profile/$REV 缺少 $f"
done

if [ "$ALL" -eq 1 ] || [ "${#TARGETS[@]}" -eq 0 ]; then
  TARGETS=("${DWP_EMPLOYEE_IDS[@]}")
fi
for id in "${TARGETS[@]}"; do
  is_employee_id "$id" || bad "未知AI员工：$id（可选：${DWP_EMPLOYEE_IDS[*]}）"
done

require_docker
require_compose_file
umask 077
mkdir -p -m 0700 "$OPS_LOG_DIR"
LOG="$(new_log profile-switch)"
exec > >(tee -a "$LOG") 2>&1

hdr "0/4 目标"
info "Profile 版本：$REV"
info "影响实例  ：${TARGETS[*]}"
OLD_REV="$(readlink "$PROFILE_ROOT/current" 2>/dev/null || echo '<none>')"
info "当前指针  ：$OLD_REV"
info "日志      ：$LOG"

hdr "1/4 切换 current 指针与 versions.env"
ln -sfn "$REV" "$PROFILE_ROOT/current"
set_versions_value DWP_PROFILE_REV "$REV"
set_versions_value DWP_PROFILE "./profile/current"
regenerate_profile_index
sync_env_from_versions
ok "profile/current -> $REV；DWP_PROFILE_REV=$REV；.env 已刷新"

hdr "2/4 重建目标 harness 容器"
services=()
for id in "${TARGETS[@]}"; do services+=("$(service_for "$id")"); done
docker compose up -d --no-deps --force-recreate "${services[@]}"
ok "已重建：${services[*]}"

hdr "3/4 逐容器校验 profile"
want_semantic="$(cordis_semantic "$SRC/cordis.yml")"
failures=0
for id in "${TARGETS[@]}"; do
  c="$(container_for "$id")"
  # cordis.patch.yml / package.json：字节级
  for f in cordis.patch.yml package.json; do
    want="$(sha256sum "$SRC/$f" | awk '{print $1}')"
    got="$(docker exec "$c" sha256sum "/dsh-home/profiles/$DSH_PROFILE_NAME/$f" 2>/dev/null | awk '{print $1}' || true)"
    if [ "$want" = "$got" ]; then
      printf '  [ OK ] %-26s %s\n' "$c" "$f"
    else
      printf '  [FAIL] %-26s %s want=%s got=%s\n' "$c" "$f" "${want:0:12}" "${got:0:12}" >&2
      failures=$((failures + 1))
    fi
  done
  # cordis.yml：语义比对（dsh 会追加注释头）
  got_semantic="$(docker exec "$c" sed '/^[[:space:]]*#/d; /^[[:space:]]*$/d' \
    "/dsh-home/profiles/$DSH_PROFILE_NAME/cordis.yml" 2>/dev/null | tr -d '[:space:]' || true)"
  if [ "$want_semantic" = "$got_semantic" ]; then
    printf '  [ OK ] %-26s cordis.yml（语义一致）\n' "$c"
  else
    printf '  [FAIL] %-26s cordis.yml want=%s got=%s\n' "$c" "$want_semantic" "$got_semantic" >&2
    failures=$((failures + 1))
  fi
done

hdr "4/4 结果"
if [ "$failures" -ne 0 ]; then
  warn "有 $failures 项 profile 校验失败；回退：./ops/profile-switch.sh $OLD_REV --all"
  echo "日志：$LOG" >&2
  exit 1
fi
ok "Profile 切换完成：$OLD_REV -> $REV"
echo "日志：$LOG"
