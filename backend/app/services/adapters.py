"""Mock Adapter 注册表（Sprint 2）。

全部为虚构数据，不接任何真实系统。业务模块不得直接调用本模块，
必须经 Plugin Gateway（gateway.invoke_plugin）。
"""

from .. import models


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


REGISTRY: dict[str, callable] = {
    "mock://kb/l1": _mock_kb_l1,
    "mock://kb/l2": _mock_kb_l2,
    "mock://mcp/hr-employee": _mock_hr_mcp,
    "mock://adp/onboarding": _mock_adp_onboarding,
    "mock://rpa/report": _mock_rpa_report,
    "mock://http/internet-search": _mock_internet_search,
}


def run_adapter(plugin: models.Plugin, params: dict) -> dict:
    handler = REGISTRY.get(plugin.endpoint_ref)
    if handler is None:
        raise RuntimeError(f"未注册的 Mock Adapter：{plugin.endpoint_ref}")
    return handler(plugin, params)
