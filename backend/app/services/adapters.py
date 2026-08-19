"""Mock Adapter 注册表（Sprint 2）。

全部为虚构数据，不接任何真实系统。业务模块不得直接调用本模块，
必须经 Plugin Gateway（gateway.invoke_plugin）。
"""

import json
from pathlib import Path

from .. import models

REPO_ROOT = Path(__file__).resolve().parents[3]


def _mock_kb_l1(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "kb_id": "KB-L1-PUB",
        "query": params.get("query", ""),
        "hits": [
            {"title": "员工守则（示例）", "snippet": "办公时间为周一至周五 09:00–18:00，请假需提前申请。"},
            {"title": "FAQ（示例）", "snippet": "工牌入职当天由 HR 统一发放。"},
        ],
    }


def _mock_kb_l2(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "kb_id": "KB-L2-HR",
        "query": params.get("query", ""),
        "hits": [
            {"title": "新员工入职流程（示例）", "snippet": "报到 → 签署合同与保密协议 → 领取工牌 → 培训两天 → 开通账号。"},
        ],
    }


def _mock_hr_mcp(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "employee_no": params.get("employee_no", "E10021"),
        "name": "王老师",
        "department": "人力资源部",
        "employment_type": "formal",
    }


def _mock_adp_onboarding(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "workflow": "adp-onboarding",
        "employee_name": params.get("employee_name", "王小明"),
        "status": "started",
        "steps": ["资料核对", "账号开通", "工牌发放"],
    }


def _mock_rpa_report(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "report": "demo-report",
        "rows": 3,
        "status": "generated",
        "note": "虚构报表，仅用于演示审批链路",
    }


def _mock_internet_search(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "engine": "demo-search",
        "query": params.get("query", ""),
        "results": [{"title": "示例结果（虚构）", "url": "https://example.invalid/demo"}],
    }


def _mock_collaboration(_plugin: models.Plugin, params: dict) -> dict:
    """按 target_employee_id + action 匹配场景，动态填充本次调用上下文。"""
    target = str(params.get("target_employee_id", ""))
    action = str(params.get("action", "ask"))
    request = str(params.get("request", ""))
    source_employee_id = params.get("source_employee_id")
    trace_id = params.get("trace_id")
    path = REPO_ROOT / "mock-data" / "skill-fixtures" / "collaboration" / "collaboration-scenarios.json"
    scenarios = json.loads(path.read_text(encoding="utf-8")).get("scenarios", [])

    base = {
        "source": "demo",
        "source_employee_id": source_employee_id,
        "target_employee_id": target,
        "action": action,
        "request": request,
        "trace_id": trace_id,
    }

    for scenario in scenarios:
        if scenario.get("target_employee_id") == target and scenario.get("action") == action:
            template = scenario.get("response_template")
            response = template.replace("{request}", request) if template else scenario.get("response")
            visited = scenario.get("visited_employee_ids")
            if not visited:
                visited = [source_employee_id, target] if source_employee_id else [target]
            return {
                **base,
                "scenario_id": scenario.get("scenario_id"),
                "status": scenario.get("status"),
                "response": response,
                "reason": scenario.get("reason"),
                "visited_employee_ids": visited,
            }
    return {
        **base,
        "scenario_id": None,
        "status": "not_found",
        "response": None,
        "reason": "未找到匹配的协作场景",
        "visited_employee_ids": [],
    }


def _mock_read_document(_plugin: models.Plugin, params: dict) -> dict:
    """只读取 mock-data/skill-fixtures/documents/ 下的虚构文档，并防目录穿越。"""
    name = str(params.get("document_name", ""))
    base = REPO_ROOT / "mock-data" / "skill-fixtures" / "documents"

    # 只接受纯文件名，拒绝任何路径片段或上级目录引用
    if not name or Path(name).name != name or name in {".", ".."}:
        return {"source": "demo", "status": "error", "content": None, "reason": "invalid document_name"}

    target = (base / name).resolve()
    if target.parent != base.resolve() or not target.is_file():
        return {"source": "demo", "status": "not_found", "content": None, "reason": "document not found"}

    content = target.read_text(encoding="utf-8")
    if not content.strip():
        return {"source": "demo", "status": "empty", "content": "", "reason": None}
    return {"source": "demo", "status": "success", "content": content, "reason": None}


def _mock_query_work_records(_plugin: models.Plugin, params: dict) -> dict:
    """查询虚构工作记录，支持按 employee_id / status 过滤。"""
    path = REPO_ROOT / "mock-data" / "skill-fixtures" / "work-records" / "work-records.json"
    records = json.loads(path.read_text(encoding="utf-8")).get("records", [])
    employee_id = params.get("employee_id")
    status = params.get("status")
    if employee_id:
        records = [r for r in records if r.get("employee_id") == employee_id]
    if status:
        records = [r for r in records if r.get("status") == status]
    return {"source": "demo", "status": "success", "records": records}


def _load_seed_json() -> dict:
    """集中读取 mock-data/seed.json，供目录类 Handler 复用，避免多处重复解析。"""
    path = REPO_ROOT / "mock-data" / "seed.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _mock_knowledge_catalog(_plugin: models.Plugin, params: dict) -> dict:
    """列出 Knowledge Base 目录（可选按 level / domain 过滤）。"""
    level = str(params.get("level") or "").strip()
    domain = str(params.get("domain") or "").strip()
    kbs: list[dict] = []
    for kb in _load_seed_json().get("knowledge_bases", []):
        if level and kb.get("level") != level:
            continue
        if domain and kb.get("domain") != domain:
            continue
        kbs.append(
            {
                "id": kb.get("id"),
                "name": kb.get("name"),
                "level": kb.get("level"),
                "domain": kb.get("domain"),
                "description": kb.get("description"),
            }
        )
    return {"source": "demo", "status": "success", "knowledge_bases": kbs}


def _mock_employee_search(_plugin: models.Plugin, params: dict) -> dict:
    """搜索 Mock 员工目录：keyword 匹配 employee_no/name/department，支持 department/type/digital_only。"""
    keyword = str(params.get("keyword") or "").strip().lower()
    department = str(params.get("department") or "").strip()
    emp_type = str(params.get("type") or "").strip()
    digital_only = bool(params.get("digital_only"))
    data = _load_seed_json()
    results: list[dict] = []
    if not digital_only:
        for h in data.get("human_employees", []):
            results.append(
                {
                    "id": h.get("employee_no"),
                    "employee_no": h.get("employee_no"),
                    "name": h.get("name"),
                    "type": "human",
                    "department": h.get("department"),
                    "status": h.get("status"),
                }
            )
    for d in data.get("digital_employees", []):
        results.append(
            {
                "id": d.get("employee_no"),
                "employee_no": d.get("employee_no"),
                "name": d.get("name"),
                "type": d.get("type"),
                "department": d.get("department"),
                "status": d.get("status") or "active",
                "runtime": d.get("runtime_type"),
            }
        )
    matched: list[dict] = []
    for e in results:
        if emp_type and e.get("type") != emp_type:
            continue
        if department and e.get("department") != department:
            continue
        if keyword:
            haystack = " ".join(str(e.get(k) or "") for k in ("employee_no", "name", "department")).lower()
            if keyword not in haystack:
                continue
        matched.append(e)
    return {"source": "demo", "status": "success", "employees": matched}


def _mock_document_catalog(_plugin: models.Plugin, params: dict) -> dict:
    """列出 Demo 文档 Fixture 目录，只返回名称与大小，不返回正文。"""
    base = REPO_ROOT / "mock-data" / "skill-fixtures" / "documents"
    docs: list[dict] = []
    if base.is_dir():
        for f in sorted(base.iterdir()):
            if f.is_file():
                docs.append({"document_name": f.name, "size": f.stat().st_size})
    return {"source": "demo", "status": "success", "documents": docs}


REGISTRY: dict[str, callable] = {
    "mock://kb/l1": _mock_kb_l1,
    "mock://kb/l2": _mock_kb_l2,
    "mock://mcp/hr-employee": _mock_hr_mcp,
    "mock://adp/onboarding": _mock_adp_onboarding,
    "mock://rpa/report": _mock_rpa_report,
    "mock://http/internet-search": _mock_internet_search,
    "mock://collaboration/employee": _mock_collaboration,
    "mock://document/read": _mock_read_document,
    "mock://work/records": _mock_query_work_records,
    "mock://knowledge/catalog": _mock_knowledge_catalog,
    "mock://employee/search": _mock_employee_search,
    "mock://document/catalog": _mock_document_catalog,
}


def run_adapter(plugin: models.Plugin, params: dict) -> dict:
    handler = REGISTRY.get(plugin.endpoint_ref)
    if handler is None:
        raise RuntimeError(f"未注册的 Mock Adapter：{plugin.endpoint_ref}")
    return handler(plugin, params)
