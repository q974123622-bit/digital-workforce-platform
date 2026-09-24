"""知识库目录同步/核对工具（一次性、可重跑，不在运行期调用）。

用途：
1. 枚举内部知识引擎**两个环境**的真实库清单：
   - 测试环境 REST：``GET {base}/marketing_agent/api/v2/rag/kb/list``（翻页直到不足一页）
   - 生产环境 VeRAG MCP：``tools/call list_knowledge_bases``（翻页）
2. 过滤自动化/测试/验证/临时等噪声库（这些库不是业务知识）。
3. 与 ``app.services.knowledge_catalog.KB_CATALOG`` 逐条核对：
   - 目录里的 rest/verag 远端 ID 是否真实存在、名字是否与目录语义一致；
   - 两个环境里有哪些真实业务库尚未进入平台目录（待人工决定是否纳入）。
4. 产出可人工审阅的 JSON 报告（默认 ``docs/kb-catalog-mapping.json``）。

安全：凭据只从环境变量 / secrets 文件读取，绝不写入报告、日志或 stdout。

用法（在 ``backend/`` 目录下）::

    python -m tools.kb_catalog_sync --output ../docs/kb-catalog-mapping.json
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import config  # noqa: E402
from app.services.knowledge_catalog import KB_CATALOG  # noqa: E402

# 噪声库名特征：自动化测试、验证库、对照库、临时库、纯扩展名占位库等。
_NOISE_PATTERNS: tuple[re.Pattern[str], ...] = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r"自动化",
        r"验证库",
        r"对照",
        r"临时",
        r"合并单元格",
        r"素材",
        r"文件夹测试",
        r"制度比对",
        r"个人空间",
        r"\btest\b",
        r"^test",
        r"\bceshi\b",
        r"\bdemo\b",
        r"^p0",
        r"^702",
        r"^通用\d+$",
        r"^QA问答$",
        r"^问答\d+$",
        r"^[a-z]{1,4}\d*$",  # 纯扩展名/短代号：pdf、doc、md、csv、ofd、ppt、et、tp、qa、gch、wsl…
        r"^\d+$",
    )
)

_PAGE_SIZE = 100
_MAX_PAGES = 40


def is_noise(name: str) -> bool:
    """库名是否属于自动化/测试/占位类噪声库。"""
    candidate = (name or "").strip()
    if not candidate:
        return True
    return any(pattern.search(candidate) for pattern in _NOISE_PATTERNS)


def fetch_rest_libraries(timeout: float = 30.0) -> list[dict[str, Any]]:
    """枚举测试环境 REST 侧可见的全部知识库。"""
    import httpx

    base_url = (config.internal_kb_base_url() or "").rstrip("/")
    if not base_url:
        raise RuntimeError("未配置 DWP_INTERNAL_KB_BASE_URL / DWP_INTERNAL_KB_ENDPOINT")
    headers = {
        "Accept": "application/json, text/plain, */*",
        "x-org": config.get(config.INTERNAL_KB_X_ORG) or "",
        "x-tenant": config.get(config.INTERNAL_KB_X_TENANT) or "",
        "X-User": config.get(config.INTERNAL_KB_X_USER) or "",
        "Authorization": config.get(config.INTERNAL_KB_AUTHORIZATION) or "",
    }
    path = "/marketing_agent/api/v2/rag/kb/list"
    libraries: list[dict[str, Any]] = []
    with httpx.Client(base_url=base_url, headers=headers, timeout=timeout) as client:
        for page in range(1, _MAX_PAGES + 1):
            response = client.get(path, params={
                "page": page,
                "page_size": _PAGE_SIZE,
                "strict": "true",
                "filter_system_created": "true",
            })
            response.raise_for_status()
            envelope = response.json()
            if envelope.get("code") != 0:
                raise RuntimeError(f"REST 知识库列表业务错误：code={envelope.get('code')}")
            data = envelope.get("data") or {}
            items = data.get("items") or []
            for item in items:
                if not isinstance(item, dict):
                    continue
                libraries.append({
                    "id": item.get("id"),
                    "name": str(item.get("name") or ""),
                    "document_count": item.get("doc_num") or 0,
                })
            if len(items) < _PAGE_SIZE:
                break
    return libraries


def fetch_verag_libraries(timeout: float = 30.0) -> list[dict[str, Any]]:
    """枚举生产环境 VeRAG MCP 侧可见的全部知识库。"""
    from app.services.external_mcp_client import ExternalMcpClient

    url = config.verag_mcp_url()
    if not url:
        raise RuntimeError("未配置 DWP_VERAG_MCP_URL")
    client = ExternalMcpClient(
        url=url,
        api_key="",
        timeout_seconds=timeout,
        auth_headers=config.verag_mcp_auth_headers(),
        protocol_version=config.verag_mcp_protocol_version(),
    )
    client.initialize()
    libraries: list[dict[str, Any]] = []
    page = 1
    while page <= _MAX_PAGES:
        result = client.call_tool("list_knowledge_bases", {"page": page, "page_size": 50})
        payload = _first_json_payload(result)
        items = payload.get("items") or []
        for item in items:
            if not isinstance(item, dict):
                continue
            libraries.append({
                "id": str(item.get("knowledge_base_id") or ""),
                "name": str(item.get("name") or ""),
                "document_count": item.get("document_count") or 0,
                "knowledge_base_type": item.get("knowledge_base_type"),
            })
        if not payload.get("has_more") or not items:
            break
        page += 1
    return libraries


def _first_json_payload(result: dict[str, Any]) -> dict[str, Any]:
    """从 MCP ``tools/call`` 结果里取出第一段 text 并解析成 JSON。"""
    for item in result.get("content") or []:
        if isinstance(item, dict) and item.get("type") == "text":
            try:
                parsed = json.loads(item.get("text") or "")
            except ValueError:
                continue
            if isinstance(parsed, dict):
                return parsed
    return {}


def build_report(rest: list[dict], verag: list[dict]) -> dict[str, Any]:
    """把两个环境的枚举结果与平台目录做交叉核对。"""
    rest_index = {int(item["id"]): item for item in rest if item.get("id") is not None}
    verag_index = {str(item["id"]): item for item in verag if item.get("id")}

    catalog_rows: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    for entry in KB_CATALOG:
        row = {
            "platform_id": entry["id"],
            "name": entry["name"],
            "domain": entry.get("domain"),
            "data_level": entry.get("data_level"),
            "rest": None,
            "verag": None,
        }
        rest_id = entry.get("rest")
        if rest_id is not None:
            hit = rest_index.get(int(rest_id))
            row["rest"] = {"id": rest_id, "found": hit is not None,
                           "remote_name": (hit or {}).get("name"),
                           "document_count": (hit or {}).get("document_count")}
            if hit is None:
                missing.append({"platform_id": entry["id"], "source": "rest", "remote_id": rest_id})
        verag_id = entry.get("verag")
        if verag_id is not None:
            hit = verag_index.get(str(verag_id))
            row["verag"] = {"id": verag_id, "found": hit is not None,
                            "remote_name": (hit or {}).get("name"),
                            "document_count": (hit or {}).get("document_count")}
            if hit is None:
                missing.append({"platform_id": entry["id"], "source": "verag", "remote_id": verag_id})
        catalog_rows.append(row)

    mapped_rest = {int(entry["rest"]) for entry in KB_CATALOG if entry.get("rest") is not None}
    mapped_verag = {str(entry["verag"]) for entry in KB_CATALOG if entry.get("verag") is not None}

    def _unmapped(items: list[dict], mapped: set) -> list[dict[str, Any]]:
        rows = []
        # 统一按字符串比较：REST 的 id 是整数、VeRAG 的是字符串，直接比会全部误判为「未映射」。
        mapped_keys = {str(value) for value in mapped}
        for item in items:
            if str(item["id"]) in mapped_keys:
                continue
            if is_noise(item.get("name") or ""):
                continue
            rows.append({"id": item["id"], "name": item["name"],
                         "document_count": item.get("document_count")})
        return sorted(rows, key=lambda row: str(row["id"]))

    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "summary": {
            "rest_total": len(rest),
            "rest_business": len([i for i in rest if not is_noise(i.get("name") or "")]),
            "verag_total": len(verag),
            "verag_business": len([i for i in verag if not is_noise(i.get("name") or "")]),
            "catalog_size": len(KB_CATALOG),
        },
        "catalog": catalog_rows,
        "missing_remote": missing,
        "unmapped_rest": _unmapped(rest, mapped_rest),
        "unmapped_verag": _unmapped(verag, mapped_verag),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="核对平台知识库目录与内部知识引擎真实库清单")
    parser.add_argument("--output", default="docs/kb-catalog-mapping.json", help="报告输出路径")
    parser.add_argument("--timeout", type=float, default=30.0, help="单次请求超时（秒）")
    args = parser.parse_args()

    report: dict[str, Any] = {"generated_at": datetime.now().isoformat(timespec="seconds")}
    try:
        report["rest"] = fetch_rest_libraries(args.timeout)
    except Exception as exc:  # noqa: BLE001 - 工具脚本需要把失败原因写进报告
        report["rest_error"] = f"{type(exc).__name__}: {exc}"
        report["rest"] = []
    try:
        report["verag"] = fetch_verag_libraries(args.timeout)
    except Exception as exc:  # noqa: BLE001
        report["verag_error"] = f"{type(exc).__name__}: {exc}"
        report["verag"] = []

    report.update(build_report(report.get("rest") or [], report.get("verag") or []))

    target = Path(args.output)
    if not target.is_absolute():
        target = (BACKEND_ROOT / target).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")

    summary = report["summary"]
    print(f"[kb-catalog] REST {summary['rest_total']} 个库（业务 {summary['rest_business']}）")
    print(f"[kb-catalog] VeRAG {summary['verag_total']} 个库（业务 {summary['verag_business']}）")
    print(f"[kb-catalog] 平台目录 {summary['catalog_size']} 条")
    if report.get("missing_remote"):
        print(f"[kb-catalog] 目录中远端 ID 不存在：{report['missing_remote']}")
    if report.get("rest_error"):
        print(f"[kb-catalog] REST 枚举失败：{report['rest_error']}")
    if report.get("verag_error"):
        print(f"[kb-catalog] VeRAG 枚举失败：{report['verag_error']}")
    print(f"[kb-catalog] 未纳入平台目录的业务库：REST {len(report['unmapped_rest'])} 个、"
          f"VeRAG {len(report['unmapped_verag'])} 个")
    print(f"[kb-catalog] 报告已写入 {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
