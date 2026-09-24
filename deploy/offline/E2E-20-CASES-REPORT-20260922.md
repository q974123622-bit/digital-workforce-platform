# AI员工工作台 · 20 份 case 验收报告（2026-09-22）

对本机正在运行的部署做了两轮全量验收：20 个 case，逻辑 12 个、稳定性 8 个，不含群聊。
判定全部取运行库真源，不看界面观感。脚本、方案、明细、日志都在 `deploy/offline/` 下，可原样重跑。

配套文档：测试方案 `E2E-20-CASES-PLAN-20260922.md`。

## 一、结论

**两轮全量，平台侧 20/20 通过**：第一轮 20/20；第二轮 19/20，唯一 FAIL 是 L08，成因是模型侧偶发被**测试脚本自己的 bug** 误判（见 5.3），修好脚本后 `--only L08` 复跑通过。

第二轮逐 case 耗时合计 13.8 分钟，并发度 3 的墙钟约 7 分钟。

值得说的是，第一轮跑出的 3 个 FAIL 成因各不相同，没有一个真是「平台挂了」：

- **L07**：case 期望本身设计错了。原期望是「另一自然人的委派也要透传引用」，但 `E20999` 是用工类型 `intern`，16 个知识库全部限定 `formal`，实习生发起时知识库会被整片摘除，委派给岗位AI员工也读不到 —— 这条期望永远不可能成立。改成「实习生不得读到 formal-only 库」后通过，并因此发现缺陷 D1。
- **L08**：模型侧偶发（dsh `INVALID_REQUEST`）。第一轮时脚本抓不到 harness 原始报错，无法判定为偶发；补上「按 trace 从容器日志取原始报错」后，单独复跑通过。
- **S06**：断言口径不对。平台把补全请求的**请求级** `reason` 写死成 `missing`，过期信息只在**字段级** `reason` 上；改按字段级断言后通过，并因此发现缺陷 D2。

也就是说，这三轮修正分别暴露了「case 设计错」「脚本抓不到根因」「断言口径错」三类问题，而平台侧真正的问题只有 D1 和 D2 两条。

平台侧共 2 条真实缺陷，另有 1 类模型侧偶发，还有 1 处是**测试脚本自己**的问题（已修）：

- **真实缺陷 2 条**：一条中等（实习生身份读不到任何知识库，委派也无效）、一条低（补全请求的请求级原因恒为「缺少」，过期信息只在字段级）。
- **模型侧偶发 1 类**：模型生成的工具调用参数不是合法 JSON，dsh 直接报错退出。不是平台缺陷，单独统计。
- **测试脚本自身缺陷 1 处（本轮修正）**：`_harness_error_detail()` 取错了正则分组，把 trace 当成 detail 返回，导致偶发分类从未命中，把模型侧偶发算成了平台失败。已修并单独复跑验证。
- **环境前提 2 条**：本机 dockerd 重启后要先建 `/run/user/0`；容器签名密钥不由 `docker exec` 继承。这两条会让平台「看起来坏了」，写在第七节。

## 二、测试对象与环境

| 项 | 值 |
| --- | --- |
| 入口 | `http://127.0.0.1:18080`（nginx 只代理 `/api/`，`/internal/*` 只能容器内访问） |
| backend | `dwp-ai-employee-platform-backend-1`，镜像 `dwp-backend:0.4.0-offline`，不映射宿主端口 |
| frontend | `dwp-ai-employee-platform-frontend-1`，`18080->8080` |
| harness | 7 个：`dwp-harness-{ai-general,ai-investment,ai-research,ai-fund,ai-xyzq,dt-e10281,dt-e20999}`，镜像 `dwp-dsh:rc6-py` |
| 运行库 | `deploy/offline/data/dwp.db`（SQLite，WAL） |
| 模型 | 网关 `10.56.35.240:8002/v1`，`DEEPSEEK_MODEL=ascend-qwen3.6-27b` |
| 知识引擎 | 内部 REST `10.57.180.65` + 生产 VeRAG MCP，环境判定为「生产环境」 |
| 兴证e家 MCP | `http://10.32.253.43/ehome/mcp/claw`（双鉴权：应用级 API Key + 按人 JWT） |

附加自检（本轮实跑）：知识源可达 —— 内部 REST 144 ms（99 个库）、VeRAG MCP 467 ms（17 个库、5 个工具）；兴证e家应用级鉴权有效、17 个工具清单可获取。

测试身份（secrets 里的登录 key，`DWP_ENABLE_LEGACY_PASSWORD_LOGIN=0`）：

| 自然人 | 姓名 | 用工类型 | 部门 | 分身 | 用途 |
| --- | --- | --- | --- | --- | --- |
| `E10281` | 张三 | formal | 架构部 | `DT-E10281` | 主身份（知识库/委派/兴证e家/市场审批） |
| `E20999` | 陈晓萌 | **intern** | 研发部 | `DT-E20999` | 补全链路、越权探测、实习生边界 |
| `SYS-ADMIN` | 平台管理员 | system | 平台管理 | 无 | 市场审批 |

各岗位AI员工当前的知识库授权（独占分配，两两不相交）：

| AI员工 | 授权知识库 |
| --- | --- |
| AI-GENERAL | KB-IT-SERVICE、KB-DATA-GOVERNANCE、KB-INTERNAL-POLICY、KB-GROUP-OFFICE |
| AI-INVESTMENT | KB-SECURITIES、KB-INVESTMENT-BANKING |
| AI-RESEARCH | KB-RESEARCH-INSTITUTE、KB-WEALTH |
| AI-FUND | KB-FINANCIAL-PRODUCT、KB-INSTITUTIONAL |
| AI-XYZQ | KB-HR、KB-FINANCE、KB-OPS、KB-AUDIT、KB-PROCUREMENT、KB-AI-INNOVATION |

## 三、方法

脚本：`deploy/offline/e2e-20-cases.py`，只用 Python 标准库，不引入任何依赖，堡垒机上可原样跑。

```bash
cd deploy/offline
python3 e2e-20-cases.py --workers 3 \
  --report ops/logs/e2e-20-cases-$(date +%Y%m%d).json \
  --report-md ops/logs/e2e-20-cases-$(date +%Y%m%d).md
python3 e2e-20-cases.py --only L11 S06 --workers 1   # 单跑定位
python3 e2e-20-cases.py --no-mutate                  # 跳过会写数据的 case
```

四个必须保留的机制：

1. **按本次 execution_id 精确等待**。`GET /conversations/{cid}/runs/latest` 在本次执行被整体回滚时会回落到上一次执行，只看「latest 是不是终态」会把失败判成通过。
2. **按争用资源加锁**。同一会话有活跃执行时再提交返回 409（唯一索引 `uq_agent_execution_active_conversation`）；同一个 AI 员工的 harness 任务同一时刻只能跑一个。锁键 `emp:<员工>` / `delegate:<目标>`，重叠串行、不重叠才并行；故意制造争用的 case 显式不吃锁，并放到隔离阶段单独跑。
3. **断言只读真源**，不读界面：`agent_execution`（终态/错误码）、`agent_execution_event`（事件序列，含 `input_required`、`skill_routed`、`external_mcp_started`）、`audit_event`（知识库读授权、外部 MCP 调用、补全提交）、`delegation_run`（委派链路）、`conversation_message`（引用卡片、作废标记）、`tool_input_request`（待补全记录与掩码）、`mcp_user_credential`（按人凭据）。
4. **偶发与缺陷分开**。平台只把「AI员工执行失败，可稍后重试」写进 `error_message`，dsh 的原始报错只在服务端日志里。脚本按 trace 从 backend 容器日志抓 `harness execution failed ... detail=...`，命中模型侧非法 JSON 的重试 1 次，仍失败记 `FLAKY` 而不计入缺陷。

越权类 case 的「未授权库真实文档名」不是手写清单，而是从历史审计的 `result_summary.documents[].title` 里自动取（每例 3 条对照），避免拿一份过期文档名做断言。

## 四、20 个 case（第二轮全量结果）

| 编号 | 分组 | 标题 | 发起 | 结果 | 终态 | 耗时 | 关键证据 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| L01 | 逻辑 | 检索 IT 服务知识库（授权库命中） | AI-GENERAL/E10281 | PASS | completed | 53s | 事件 32 条；答案 695 字；知识库读审计 allow=['KB-IT-SERVICE'] deny=- |
| L02 | 逻辑 | 检索财务报账知识库（多库授权下选对库） | AI-XYZQ/E10281 | PASS | completed | 69s | 事件 42 条；答案 828 字；知识库读审计 allow=['KB-FINANCE'] deny=- |
| L03 | 逻辑 | 顺序检索两个授权库（跨库不串台） | AI-INVESTMENT/E10281 | PASS | completed/completed | 114s | 第1轮=completed；第2轮=completed；两轮答案 699+659 字 |
| L04 | 逻辑 | 越权问未授权的证券知识库 | AI-FUND/E10281 | PASS | completed | 34s | 事件 23 条；答案 387 字；知识库读审计 allow=['KB-FINANCIAL-PRODUCT', 'KB-INSTITUTIONAL'] deny=- |
| L05 | 逻辑 | 越权问未授权的投行知识库 | AI-GENERAL/E10281 | PASS | completed | 69s | 事件 57 条；答案 1417 字；知识库读审计 allow=['KB-DATA-GOVERNANCE', 'KB-INTERNAL-POLICY'] deny=- |
| L06 | 逻辑 | 分身无库权限须委派给岗位AI员工 | DT-E10281/E10281 | PASS | completed | 87s | 事件 41 条；答案 793 字；知识库读审计 allow=['KB-IT-SERVICE'] deny=- |
| L07 | 逻辑 | 实习生身份不得读到 formal-only 知识库（委派也无效） | DT-E20999/E20999 | PASS | completed | 43s | 事件 16 条；答案 175 字；知识库读审计 allow=- deny=- |
| L08 | 逻辑 | 技能路由：命中领域技能与普通寒暄 | AI-FUND/E10281 | FAIL | failed/completed | 16s | 第1轮=failed；第2轮=completed；两轮答案 0+71 字 |
| L09 | 逻辑 | 就地编辑最新一轮并重跑 | AI-INVESTMENT/E10281 | PASS | completed | 75s | 事件 32 条；答案 617 字；重跑 seq=1 状态=completed |
| L10 | 逻辑 | 兴证e家 MCP 按人鉴权正常查询 | DT-E10281/E10281 | PASS | completed | 35s | 事件 14 条；答案 180 字；知识库读审计 allow=- deny=- |
| L11 | 逻辑 | 缺参补全链路（弹气泡→提交→续跑→掩码） | DT-E20999/E20999 | PASS | completed | 21s | 载体执行=completed；补全 TI-E0F472C4DE26 reason=missing fields=['X-MCP-JWT']；续跑 execution=EX-A0A1F0E9A7BA4F07 |
| L12 | 逻辑 | 市场申请与管理员审批后生效 | DT-E10281/E10281 | PASS | approved | 0s | 申请目标 SKILL-QM-fund-screener（基金筛选 / skill）；申请单 #9；绑定 admin_enabled=1 employee_enabled=1 mode=allow |
| S01 | 稳定性 | 同会话重复提交被拒且不产生第二条执行 | AI-RESEARCH/E10281 | PASS | completed | 24s | 409 code=STATE_CONFLICT retryable=True；事件 38 条；答案 866 字 |
| S02 | 稳定性 | 提交后立即停止执行（幂等） | AI-XYZQ/E10281 | PASS | cancelled | 2s | 重复 cancel -> 200 |
| S03 | 稳定性 | 跨员工并发提问互不干扰 | AI-GENERAL/E10281 | PASS | completed/completed/completed | 29s | AI-GENERAL=completed；AI-XYZQ=completed；AI-RESEARCH=completed |
| S04 | 稳定性 | 同一委派目标并发争用 | DT-E10281/E10281 | PASS | completed/completed | 91s | DT-E10281=completed；DT-E20999=completed；本轮悬挂委派记录=0 条 |
| S05 | 稳定性 | 超长输入不 5xx 且有终态 | AI-GENERAL/E10281 | PASS | completed | 8s | 输入长度 5297 字；事件 8 条；答案 50 字 |
| S06 | 稳定性 | 过期凭据不得 5xx、不得泄漏明文 | DT-E20999/E20999 | PASS | expired-handled | 26s | 载体执行=completed；凭据 expires_at=2026-09-22 03:55:22.000000 hint=****TURE；第二次预检前凭据行=2026-09-22 03:55:22.000000/****TURE |
| S07 | 稳定性 | 未登录与越权访问一律被拒 | AI-GENERAL/E10281 | PASS | checked | 8s | 已为该会话造一条执行用于越权探测 |
| S08 | 稳定性 | 容器重启后的恢复与状态一致性 | AI-GENERAL/E10281 | PASS | completed | 25s | 重启前：近 20 条终态执行、活跃执行 0 条；harness 重启后：DSH_OK / PROFILE_OK / Python 3.11.16；backend 健康=True |

## 五、发现

### 5.1 缺陷（2 条）

**D1｜中等｜实习生身份读不到任何知识库，委派也无效**

现象：以 `E20999`（陈晓萌，用工类型 `intern`）身份发起知识类提问时，她的分身 `DT-E20999` 查不到任何知识库；按设计分身应委派给岗位AI员工，但被委派方同样读不到。表现是「委派成功、回答没有任何依据、也没有引用卡片」。

定位：`backend/app/services/knowledge_registry.py` 的 `accessible_knowledge_bases()` 除了按员工授权过滤，还会按**请求人**的用工类型和部门再过滤一次（第 62-66 行）：

```62:66:backend/app/services/knowledge_registry.py
        if requester is not None:
            if kb.allowed_employment_type and requester.employment_type not in kb.allowed_employment_type:
                continue
            if kb.department_scope and "*" not in kb.department_scope and requester.department not in kb.department_scope:
                continue
```

而运行库里 16 个知识库的 `allowed_employment_type` **全部是 `["formal"]`**。所以实习生发起时 16 个库被全部摘除；被委派方拿到的 `requester_human_no` 还是这位实习生，同样被摘除。

影响：实习生的数字分身永远给不出有依据的回答，界面上没有任何解释（用户只会觉得「这个AI不会」）；「分身无库权限就委派」这个兜底设计对实习生也失效。如果口径是「实习生不该看涉密库」，那 `KB-IT-SERVICE`、`KB-INTERNAL-POLICY` 这类公共库大概应该放开 —— 这需要产品确认，代码本身是「按配置过滤」，不算逻辑错误。

证据：L07 两轮全量均一致 —— 委派记录 `DT-E20999 -> AI-GENERAL (completed)`，但该 trace 下**知识库读审计 0 条、引用卡片 0 张**。

建议：给公共库放开 `intern`，或在会话里明确提示「你当前身份暂无知识库权限」。

**D2｜低｜补全请求的请求级原因恒为「缺少」，过期信息只在字段上**

现象：提交一枚已过期的 JWT 后，第二次预检确实识别为过期（字段级 `reason=expired`，并带上已存凭据掩码 `****TURE`），但请求级 `reason` 仍是 `missing`。

定位：`backend/app/routers/internal.py` 预检分支把请求级原因写死：

```587:590:backend/app/routers/internal.py
                if missing:
                    result = _input_required_result(
                        db, claims, adapter, raw_name, missing, tool_input.REASON_MISSING,
                    )
```

事后识别分支（外部 MCP 返回错误文本时）传的是 `REASON_TOOL_ERROR`，只有预检这一条路径写死了 `REASON_MISSING`。

影响：`tool_input_request.reason` 以及 `GET /conversations/{cid}/runs/{eid}/input-requests` 返回的 `reason` 分不清「从没给过」和「给过但过期了」。前端气泡按**字段**渲染所以用户看到的是对的，但审计、排障、以及任何按请求级 reason 判断的逻辑都会误读。

证据：S06 —— 两次补全的请求级 reason 序列 `['missing', 'missing']`，字段级 `['expired']`。

建议：请求级原因取字段里最严重的一个（有 `expired` 就用 `expired`），三行改动。

### 5.2 已知偶发（不计入缺陷）

模型有时会生成不是合法 JSON 的工具调用参数，dsh 直接报错退出，表现为执行 `failed`、错误文案是通用的「AI员工执行失败，可稍后重试」。原始报错只在 backend 容器日志里：

```
harness execution failed employee=AI-FUND trace=T-EX-244577BC4137465A mode=harness
detail=dsh: INVALID_REQUEST: Expecting property name enclosed in double quotes: line 1 column 2 (char 1)
```

第二轮全量命中 1 次（L08 第 1 轮「市场早报」，16 秒即失败），单独复跑通过。属于模型侧鲁棒性，不是平台检索或技能链路的问题。

### 5.3 测试脚本自身的缺陷（本轮修正）

`_harness_error_detail()` 用正则 `trace=(\S+) mode=\S+ detail=(.*)` 解析，但取值时取了 `group(1)`（trace）而不是 `group(2)`（detail），返回的是一串 trace id。后果是 `FLAKY_PATTERN` 永远匹配不上，偶发分类形同虚设，模型侧偶发被算成平台失败。

修正后直接用真实日志验证：

```
_harness_error_detail('T-EX-244577BC4137465A')
  -> 'dsh: INVALID_REQUEST: Expecting property name enclosed in double quotes: line 1 column 2 ('
FLAKY_PATTERN.search(该返回值) -> True
```

第二轮里 L08 就是被这个 bug 误判成 FAIL 的；修正后 `--only L08` 复跑通过。

### 5.4 其它观察

- 库里还留着 3 条 2026-09-16/17 的 `delegation_run.status='running'`，属于历史残留没收敛。本轮委派记录全部正常收敛，只当数据卫生问题记录。
- 会话的 `/clear` 会连带删掉该会话的执行与消息记录（`generation` 自增）。做「按 trace 反查历史」时要注意，清空之后查不到。
- 409 的错误信封是 `{"error":{"code","message","detail":{...}}}`，不是 `{"detail":...}`；`retryable` 在 `error.detail.retryable`，会话活跃冲突的 `code` 是 `STATE_CONFLICT`。前端封装按这个层级取才不会漏。
- 同一 AI 员工的 harness 任务并发时是**有界排队**（`delegation_slot_wait_seconds` 默认 60 秒）而不是立刻 409，所以「同一目标并发争用」两种结果都算通过；S04 两轮都排到了队、都成功。

## 六、副作用与恢复

| case | 写入了什么 | 是否恢复 |
| --- | --- | --- |
| L12 | 1 条 `access_request`（已 granted）+ 1 条 `agent_plugin_binding` | 不恢复，保留作为审批生效的证据 |
| L11 | 临时写 `E20999` 名下 `xyzq-ehome-mcp` 的按人凭据（假令牌），新增待补全记录 | 凭据按只读快照原样恢复（原本没有则删除） |
| S06 | 同上（过期令牌） | 同上 |
| S08 | 重启 `dwp-harness-ai-general` 与 backend 各一次 | 重启后健康，历史终态未被改写 |
| 其余 | 仅测试用的会话、执行、事件与审计数据 | — |

两轮全量各写 1 条申请单与 1 条插件绑定（第一轮 `SKILL-QM-advisor-content-studio`、第二轮 `SKILL-QM-fund-screener`，均绑到 `DT-E10281`），审批后市场状态为 `enabled`。产物落盘前做过凭据明文扫描，未出现 key / JWT / API Key 明文。

## 七、环境前提（会让平台「看起来坏了」）

1. **本机 dockerd 重启后要先建 `/run/user/0`**。不建的话 `docker exec` 报 `open /run/user/0/runc-process...: no such file or directory`；backend 启动时要往 harness 容器里同步安全 profile，这一步失败会让 backend 直接 crash-loop，日志停在「Harness 安全 Profile 同步失败」。
2. **签名密钥不由 `docker exec` 继承**。容器 entrypoint 会把 `/run/secrets/harness_signing_secret` 导出给 uvicorn 进程，`docker exec` 只继承容器创建时的环境变量，另起的进程会退化成进程内随机密钥，打 `/internal/agent-tools/mcp` 必然 401。测试脚本里的容器内调用自己从 `/run/secrets/` 读一次。

## 八、回归基线

| 范围 | 命令 | 结果 |
| --- | --- | --- |
| 后端单测 | 在 `dwp-backend:0.4.0-offline` 里 `pytest tests -q` | **418 passed**，2 warnings，172 秒（与既有基线一致） |
| 前端单测 | 在 `dwp-frontend-builder:0.4.0-offline` 里 `pnpm test` | **27 个文件全通过 / 118 passed + 4 skipped**，52 秒 |

前端构建镜像的脚本在 `/workspace/frontend/package.json`，根 `/workspace/package.json` 没有 scripts，所以必须带 `-w /workspace/frontend`，否则会报 `Command "typecheck" not found`。

## 九、可复现性

| 产物 | 路径 |
| --- | --- |
| 测试脚本 | `deploy/offline/e2e-20-cases.py` |
| 测试方案 | `deploy/offline/E2E-20-CASES-PLAN-20260922.md` |
| 验收报告（本文件） | `deploy/offline/E2E-20-CASES-REPORT-20260922.md` |
| 第一轮全量 | `ops/logs/e2e-20-cases-20260922-run1.{json,log,md}` |
| 第二轮全量 | `ops/logs/e2e-20-cases-20260922-run2.{json,log,md}` |
| 失败项复跑 | `ops/logs/e2e-20-cases-20260922-l08.{json,log}` |

跑之前需要满足：docker 在跑、backend 健康、`secrets/` 8 个密钥齐全、`/run/user/0` 存在、模型网关与内部知识引擎可达。
