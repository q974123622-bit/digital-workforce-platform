# 内部知识引擎只读探测工具

该工具用于人工验证内部知识引擎的网络连通性、身份认证、知识库可见性和只读 RAG 检索。它独立于 FastAPI、前端及企业微信网关，不会修改平台配置、数据库或正式 `KnowledgeAdapter`。

## 安全边界

- 工具仅包含知识库列表 GET 和知识片段检索 POST，不包含创建、上传、修改、启停或删除操作。
- 检索请求始终携带 `enable_filters=true`，命令行不提供关闭权限过滤的选项。
- Python 探测脚本只从环境变量读取配置；可选 PowerShell 启动器只解析 Git 忽略的 `tools/.env` 中五个白名单变量。
- 工具不会输出 Authorization、完整请求头、基础地址或完整服务响应。
- 列表仅显示知识库 ID、名称和文档数；检索仅显示文档名、实际分数字段及最多 240 个字符的片段。
- 请勿将真实环境变量值、命令历史、终端输出或内部响应提交到 Git。

## 配置

复制 `tools/internal_kb_probe.env.example` 的字段结构，将真实值填写到已由 Git 忽略的 `tools/.env`。不要在其他环境示例或受跟踪文件中填写真实值。

```dotenv
DWP_INTERNAL_KB_BASE_URL=
DWP_INTERNAL_KB_X_ORG=
DWP_INTERNAL_KB_X_TENANT=
DWP_INTERNAL_KB_X_USER=
DWP_INTERNAL_KB_AUTHORIZATION=
```

值可以不加引号；包含空格的 Authorization 也会作为完整值读取。启动器不会执行文件内容，只接受以上五个变量，并在命令结束后清理进程环境。

也可以不使用 `.env`，直接在当前 PowerShell 会话中设置变量：

```powershell
$env:DWP_INTERNAL_KB_BASE_URL = Read-Host "Internal KB base URL"
$env:DWP_INTERNAL_KB_X_ORG = Read-Host "x-org"
$env:DWP_INTERNAL_KB_X_TENANT = Read-Host "x-tenant"
$env:DWP_INTERNAL_KB_X_USER = Read-Host "X-User"
$secureAuthorization = Read-Host "Authorization" -AsSecureString
$authorizationPointer = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureAuthorization)
try {
  $env:DWP_INTERNAL_KB_AUTHORIZATION = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($authorizationPointer)
} finally {
  [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($authorizationPointer)
  Remove-Variable secureAuthorization, authorizationPointer
}
```

Authorization 使用隐藏输入；其他身份字段会显示在屏幕上。请在受控终端中操作，并避免录屏或共享终端。

## 运行

从仓库根目录执行：

```powershell
.\tools\run_internal_kb_probe.ps1 list -Keyword "IT服务"
```

```powershell
.\tools\run_internal_kb_probe.ps1 retrieve `
  -KbId 123 `
  -Question "VPN申请需要经过哪些步骤？"
```

只有人工执行上述命令后，才会访问 `DWP_INTERNAL_KB_BASE_URL` 指向的服务。若执行策略阻止本地脚本，可使用：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File tools/run_internal_kb_probe.ps1 list
```

## 错误说明

- `401`：检查身份和认证环境变量是否有效。
- `403`：检查当前身份是否有目标知识库的读取权限。
- `404`：检查基础地址是否指向正确的服务和接口版本。
- 超时或网络错误：检查受控网络、代理和服务可达性。
- 业务 `code` 非 `0`：工具显示有限长度的业务错误消息，不显示请求头或凭据。

## 清理当前会话

探测完成后移除当前 PowerShell 进程中的变量：

```powershell
Remove-Item Env:DWP_INTERNAL_KB_BASE_URL
Remove-Item Env:DWP_INTERNAL_KB_X_ORG
Remove-Item Env:DWP_INTERNAL_KB_X_TENANT
Remove-Item Env:DWP_INTERNAL_KB_X_USER
Remove-Item Env:DWP_INTERNAL_KB_AUTHORIZATION
```

不要把真实探测响应保存到仓库。确认连通性和响应结构后，再评估正式 `InternalKnowledgeAdapter` 接入。