# AI员工平台企业微信渠道

这个模块是AI员工平台的企业微信渠道适配器。它只负责企微消息收发、测试用户校验和平台会话绑定；AI员工、人设、历史、知识库、Policy、工具与审计继续由原平台后端处理。

## 1. 准备凭证

在本目录复制环境变量模板：

```cmd
copy .env.example .env
```

打开 `.env`，填入从企业微信机器人管理页获取的 Bot ID 和 Secret：

```env
WECOM_BOT_ID=你的Bot ID
WECOM_BOT_SECRET=你的Secret
WECOM_TEST_USER_ID=你的企微userid
PLATFORM_BASE_URL=http://127.0.0.1:8000
PLATFORM_ACTOR_NO=E10281
PLATFORM_EMPLOYEE_NO=VE-0003
```

`.env` 已被仓库根目录的 `.gitignore` 忽略，不要在聊天、截图或Git提交中公开Secret。

## 2. 安装依赖

在仓库根目录执行：

```cmd
pnpm install
```

## 3. 启动渠道

在仓库根目录执行：

```cmd
pnpm --filter @digital-workforce/wecom-gateway start
```

看到以下日志表示凭证认证成功：

```text
[企微] 认证成功。普通文字进入AI员工平台；/状态 检查后端；/echo <文字> 测试连接。
```

## 4. 检查平台后端

先按照仓库根目录 README 启动FastAPI后端，然后在企业微信中发送：

```text
/状态
```

预期机器人回复：

```text
AI员工平台后端正常
服务：digital-workforce-platform
```

如果后端尚未启动，机器人会明确回复后端不可用。连接Echo测试请发送：

```text
/echo 测试123
```

## 5. 与AI员工自然对话

`WECOM_TEST_USER_ID` 应填写企微消息日志中 `from.userid` 的值。当前只允许这个账号进入平台通道，避免机器人使用范围内的其他成员被错误映射成同一个平台员工。

白名单测试用户发送普通文字会直接进入平台：

```text
帮我介绍一下你自己
```

网关会创建或复用 `PLATFORM_ACTOR_NO` 与 `PLATFORM_EMPLOYEE_NO` 的私聊会话，并将消息写入现有平台接口。企微先显示“AI员工正在处理”，网关随后轮询平台会话，并在最多60秒内将AI员工的真实回复返回企微。联调阶段的 `/平台 <消息>` 前缀仍然兼容，但不再需要。

当前命令：

- `/状态`：检查平台后端。
- `/echo <文字>`：只测试企微长连接，不进入平台。
- 其他文字：进入AI员工平台。

按 `Ctrl+C` 停止测试。

## 常见问题

- 无法建立连接：确认本机可以访问 `openws.work.weixin.qq.com:443`。
- 已连接但认证失败：重新检查 Bot ID、Secret 和机器人是否选择了“API模式 → 使用长连接”。
- 认证成功但收不到消息：检查机器人的使用范围是否包含当前测试账号。
- 连接反复中断：确保没有其他程序同时使用同一个机器人建立长连接。
