"""知识源 / MCP 工具可达性自检（交付与部署阶段可复用的 preflight）。

做三件事：
1. 逐源探测（内部知识引擎 REST、VeRAG MCP，以及已登记的外部 MCP 插件）；
2. 把结果落库，使平台管理端与员工侧立刻反映真实连通性；
3. 打印「当前环境判定」（优先生产 VeRAG → 只通测试用 REST → 都不可达）与逐源结论。

退出码：
- ``0``：至少有一条知识通道可达（正常部署）；
- ``1``：两条知识通道都不可达（部署需要人工介入）；
- ``--strict`` 时，任一源不可达即返回 1。

安全：只打印状态、耗时与可读原因，绝不打印 URL 凭据、Bearer/Basic 值或响应正文。

用法（在 ``backend/`` 目录下）::

    python -m tools.check_knowledge_sources
    python -m tools.check_knowledge_sources --strict --no-persist
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.database import Base, SessionLocal, engine  # noqa: E402
from app.services import knowledge_source_health as health  # noqa: E402
from app.services import knowledge_source_registry as registry  # noqa: E402
from app.services import knowledge_source_probe as probe  # noqa: E402


def _format_row(result: dict[str, Any]) -> str:
    source = result["source"]
    label = registry.source_label(source) if source in registry.known_sources() else source
    state = "可用" if result["state"] == probe.STATE_AVAILABLE else "不可用"
    detail = result.get("detail") or {}
    extra_bits: list[str] = []
    if detail.get("visible_knowledge_bases") is not None:
        extra_bits.append(f"可见知识库 {detail['visible_knowledge_bases']} 个")
    if detail.get("tools_count") is not None:
        extra_bits.append(f"工具 {detail['tools_count']} 个")
    if detail.get("server_info", {}).get("name"):
        extra_bits.append(f"服务 {detail['server_info']['name']}")
    extra = "，".join(extra_bits)
    line = f"  {label:<34} {state:<4} {result['latency_ms']:>6} ms"
    if extra:
        line += f"  （{extra}）"
    if result["state"] != probe.STATE_AVAILABLE and result.get("last_error"):
        line += f"\n      原因：{result['last_error']}"
    return line


def main() -> int:
    parser = argparse.ArgumentParser(description="知识源与 MCP 工具可达性自检")
    parser.add_argument("--timeout", type=float, default=None, help="单源探测超时（秒）")
    parser.add_argument("--no-persist", action="store_true", help="只探测，不写入数据库")
    parser.add_argument("--strict", action="store_true", help="任一源不可达即返回非零退出码")
    parser.add_argument("--no-mcp", action="store_true", help="跳过外部 MCP 插件探测")
    args = parser.parse_args()

    # 幂等建表：允许在应用首次启动之前先跑一次连通性自检（例如部署预检）。
    Base.metadata.create_all(bind=engine)

    db = SessionLocal()
    try:
        sources = list(registry.known_sources())
        if not args.no_mcp:
            from app.services import mcp_registry

            sources += [probe.mcp_source(plugin.id) for plugin in mcp_registry.external_plugins(db)]

        print(f"[check] 环境偏好：{registry.preferred_source_order()}")
        results: list[dict[str, Any]] = []
        for source in sources:
            result = probe.probe_source(source, db=db, timeout_seconds=args.timeout)
            if not args.no_persist:
                health.upsert_health(db, result)
            results.append(result)
            print(_format_row(result))

        if not args.no_persist:
            environment = health.resolve_environment(db)
            print(f"[check] 当前环境判定：{registry.environment_label(environment)}")
        else:
            available = {row["source"] for row in results if row["state"] == probe.STATE_AVAILABLE}
            environment = registry.ENV_UNKNOWN
            for source in registry.preferred_source_order():
                if source in available:
                    environment = registry.source_environment(source)
                    break
            print(f"[check] 当前环境判定（未落库）：{registry.environment_label(environment)}")
    finally:
        db.close()

    knowledge_results = [row for row in results if row["source"] in registry.known_sources()]
    any_knowledge_available = any(row["state"] == probe.STATE_AVAILABLE for row in knowledge_results)
    if not any_knowledge_available:
        print("[check] 结论：两条知识通道都不可达，AI员工检索会返回「知识源暂不可用」。")
        return 1
    if args.strict and any(row["state"] != probe.STATE_AVAILABLE for row in results):
        print("[check] 结论：存在不可达的源（--strict 模式判定失败）。")
        return 1
    print("[check] 结论：知识通道可达。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
