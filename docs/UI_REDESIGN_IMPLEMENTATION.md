# CISC 数字员工平台 UI 重构说明

## 设计方向

员工端采用“导航、会话列表、聊天主区、按需资料面板”的工作空间结构。空间组织参考 Cumora，排版与留白进一步采用 Claude 式内容优先原则；不复制任何商标、图标、业务代码、AgentTeams、看板或日历。

- Cumora 固定参考提交：`74dc994989536408f37e9c1df0e28c86cf2f974c`
- Taste Skill 固定提交：`ccbc15639c97057cbfcf32ecebc38ef716e4bb37`
- 视觉参数：Variance 5、Motion 4、Density 5
- 技术栈：React 18、Vite 5、Tailwind CSS 3、Ant Design 5

## Claude 风格设计系统

- 页面、内容与导航分别使用暖灰纸张色 `#F7F6F1`、暖白 `#FCFBF8`、浅暖灰 `#F0EEE8`。
- 正文为暖黑 `#2D2B27`，边界为低对比暖灰；兴业蓝 `#174F7C` 承担主交互，兴业红仅用于危险和品牌节点。
- UI 与正文使用随包分发的 `Noto Sans SC Variable`，编辑型标题使用 `Noto Serif SC Variable`，不请求公网字体。
- 字体位于 `frontend/public/fonts/`，许可证为同目录 `OFL.txt`。
- 背景只有极弱纸张纹理，不使用蓝紫渐变、玻璃拟态、漂浮光球、满屏卡片或无意义动画。
- 所有未知运行状态显示“尚未检查”，不得由登记、审核或发布状态推断 Runtime 已健康。

## 品牌边界

界面采用红蓝双品牌线和原创“节点、对话轨迹、向上聚合”辅助标识。该标识不替代兴业证券正式司徽。正式司徽和 CISC 组合标识只能使用公司授权原始资产。

旧版橙色卡通图片不再被任何运行入口引用。辅助标识使用代码原生 SVG，保证离线、清晰缩放和可审计，不依赖 AI 生成位图。

## 样式结构

- `styles/tokens.css`：语义 Token 基础值。
- `theme.ts`：Ant Design 对应 Token。
- `styles/product.css`：产品外壳、交互、响应式和跨组件视觉规范。
- `components/ui/BrandMark.tsx`：原创辅助标识。

`product.css` 在 Tailwind 与历史兼容样式之后加载，用于消除旧页面的视觉分叉。后续新增页面必须使用语义变量，不得增加散落品牌色。

## 插件详情

员工端点击插件资源主体后，通过 `/plugins?plugin=PLG-...` 打开右侧详情；启停按钮会阻止点击冒泡。刷新、前进和后退均保留详情选择。

安全详情接口：

```text
GET /api/v1/my/plugins/{plugin_id}
GET /api/v1/admin/plugins/{plugin_id}
```

返回内容只包含简介、分类、版本与发布状态、工具名称和说明、安全指令摘要、授权结果及 Runtime 摘要。接口禁止输出 Endpoint、Secret、认证、内部地址、制品路径、源码、完整 Skill 指令和原始 Runtime 错误。员工接口从登录 Cookie 推导本人和目标数字分身；管理员接口额外给出脱敏版本历史与绑定摘要。

## 企业通讯录

桌面端固定为“部门树（约 220px）→ 同事名单（约 340px）→ 同事资料”的三栏结构：

- 部门由当前 `Employee.department` 归组，不伪造上级组织。
- 本人数字分身单独置顶，岗位员工带“数字员工”类型。
- 搜索覆盖姓名、工号、部门、职责和负责人。
- 选择名单仅更新资料区；“发消息”是资料区唯一主操作。
- 移动端按分组、名单、全屏资料逐级进入，不压缩桌面三栏。
- `DirectoryEntry` 已预留部门 ID、头像、岗位、在线状态等字段，后续可替换为企微数据源而不改页面骨架。

## 关键行为

- 会话列表始终限制在侧栏内，名称、时间和摘要按可用宽度截断。
- 消息正文最大阅读宽度约 760px；窄屏根据剩余视口动态收缩。
- 长 Markdown、代码块和表格仅在消息内部滚动，不撑破工作台。
- 资料、来源与执行轨迹采用按需面板，执行记录完成后仍可恢复。
- 桌面使用左侧导航；移动端使用底部导航和单栏会话切换。
- 所有动效遵守 `prefers-reduced-motion`。
- 插件详情抽屉和通讯录列表均有加载、空数据、失败与未检查状态，不以空白页代替错误反馈。

## 本地验证

```bash
pnpm --dir frontend typecheck
pnpm --dir frontend test
pnpm --dir frontend build
pnpm --dir frontend preview --host 0.0.0.0 --port 4173

cd backend
.venv/Scripts/python -m pytest tests/test_plugin_memory_mvp.py -q
.venv/Scripts/python -m pytest -q
```

重点检查视口：1920×1080、1440×900、1366×768、1050×943、390×844。

## 堡垒机修改

离线包中的 `source/frontend/` 来自仓库根目录 `frontend/`。修改后执行：

```bash
./rebuild.sh frontend
./verify.sh
```

不得直接修改容器内 Nginx 静态文件，否则下次重建会丢失。
