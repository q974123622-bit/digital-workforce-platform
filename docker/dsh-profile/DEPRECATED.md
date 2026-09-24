# 已废弃（2026-09-17）

本目录曾是 dsh Profile 的唯一真源，被 `docker/Dockerfile.dsh` 与
`deploy/offline/rebuild/Dockerfile.dsh-profile-hotfix` 复制进镜像。

**真源已迁移到 `deploy/offline/profile/<rev>/`**（例如 `deploy/offline/profile/v1-20260910/`），
原因是旧的「改一个插件开关 → 重建 638MB 镜像 → 导出/传输/导入 → 重建容器」路径太重。

现在请使用：

```bash
cd deploy/offline

# 查看可用 Profile 版本与当前指针
./ops/profile-switch.sh --list

# 从本目录创建一个新的 Profile 版本（保留历史目录作为来源，便于比对）
./ops/profile-switch.sh --new-rev v2-20261001 --from docker/dsh-profile

# 切换（灰度或全量）
./ops/profile-switch.sh v2-20261001 AI-GENERAL
./ops/profile-switch.sh v2-20261001 --all
```

`./rebuild.sh harness-profile` 已改为委托 `ops/profile-switch.sh`，
并会在本目录与 `deploy/offline/profile/<rev>/` 不一致时给出提示。

本目录保留为历史备份，请勿再直接编辑。
