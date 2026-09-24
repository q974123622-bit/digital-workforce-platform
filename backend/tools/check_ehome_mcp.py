"""兴证e家员工服务 MCP 连通性自检（部署与运维 preflight）。

只做 ``initialize`` + ``tools/list``（带**应用级** ``X-MCP-API-KEY``），不涉及按人的
``X-MCP-JWT``——用户级凭据由员工在会话内补全，运维侧无从代填。

退出码：
- ``0``：可达且返回了工具清单；
- ``1``：未配置，或不可达 / 工具清单为空。

安全：只打印端点地址、工具数量与工具名，绝不打印任何凭据。

用法（在 ``backend/`` 目录下）::

    python -m tools.check_ehome_mcp
"""

from __future__ import annotations

import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import config  # noqa: E402
from app.services.external_mcp_client import ExternalMcpClient, ExternalMcpError  # noqa: E402

# 兴证e家当前暴露 17 个查询类工具；数量对不上通常意味着服务端改版，值得提醒。
EXPECTED_TOOL_COUNT = 17


def main() -> int:
    if not config.ehome_mcp_configured():
        print("[check] 兴证e家 MCP 未配置（DWP_EHOME_MCP_URL 或应用级 key 缺失），跳过。")
        return 1
    client = ExternalMcpClient(
        url=config.ehome_mcp_url(),
        api_key="",
        timeout_seconds=config.ehome_mcp_timeout(),
        auth_headers={config.ehome_mcp_app_header(): config.ehome_mcp_api_key()},
    )
    try:
        tools = client.list_tools()
    except ExternalMcpError as exc:
        print(f"[check] 兴证e家 MCP 不可达：{exc}")
        return 1
    names = [str(tool.get("name") or "") for tool in tools]
    print(f"[check] 兴证e家 MCP 可达：{config.ehome_mcp_url()}，工具 {len(names)} 个")
    for name in names:
        print(f"        - {name}")
    if len(names) != EXPECTED_TOOL_COUNT:
        print(f"[check] 提示：工具数与预期（{EXPECTED_TOOL_COUNT}）不一致，服务端可能已改版。")
    if not names:
        print("[check] 结论：工具清单为空。")
        return 1
    print("[check] 结论：兴证e家 MCP 应用级鉴权有效、工具清单可获取。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
