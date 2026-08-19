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
        "status": "completed",
        "steps": ["资料核对完成", "账号开通完成", "工牌发放完成"],
        "note": "演示：入职流程已发起并执行完成",
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


def _mock_expense_claim(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "workflow": "expense-claim",
        "employee_name": params.get("employee_name", "张三"),
        "amount": params.get("amount", 1200),
        "status": "submitted",
        "steps": ["报销申请提交", "直属领导审批", "财务复核打款"],
        "note": "虚构报销流程：一线城市住宿每晚不超过 600 元",
    }


def _mock_leave_request(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "workflow": "leave-request",
        "employee_name": params.get("employee_name", "张三"),
        "leave_type": params.get("leave_type", "年假"),
        "days": params.get("days", 1),
        "status": "pending_approval",
        "steps": ["提交请假申请", "直属领导审批", "考勤记录同步"],
        "note": "虚构请假流程：需提前一天提交，审批通过后同步考勤",
    }


def _mock_meeting_notes(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "workflow": "meeting-notes",
        "meeting": params.get("meeting", "架构部周会"),
        "status": "ready",
        "outline": ["结论", "决议", "行动项（含负责人与截止时间）", "遗留问题"],
        "note": "虚构会议纪要：会后 2 小时内由轮值同学输出",
    }


def _mock_weekly_report(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "report": "weekly-report",
        "period": params.get("period", "2026-W34"),
        "rows": 5,
        "status": "generated",
        "note": "虚构周报：本周完成 / 下周计划 / 风险项",
    }


def _mock_purchase_request(_plugin: models.Plugin, params: dict) -> dict:
    return {
        "source": "demo",
        "workflow": "purchase-request",
        "item": params.get("item", "办公显示器"),
        "amount": params.get("amount", 2500),
        "status": "pending_approval",
        "steps": ["采购申请", "三家比价", "采购下单", "资产入库"],
        "note": "虚构采购流程：金额超过 2000 元需审批（敏感）",
    }


REGISTRY: dict[str, callable] = {
    "mock://kb/l1": _mock_kb_l1,
    "mock://kb/l2": _mock_kb_l2,
    "mock://mcp/hr-employee": _mock_hr_mcp,
    "mock://adp/onboarding": _mock_adp_onboarding,
    "mock://rpa/report": _mock_rpa_report,
    "mock://http/internet-search": _mock_internet_search,
    "mock://workflow/expense-claim": _mock_expense_claim,
    "mock://workflow/leave-request": _mock_leave_request,
    "mock://workflow/meeting-notes": _mock_meeting_notes,
    "mock://rpa/weekly-report": _mock_weekly_report,
    "mock://workflow/purchase-request": _mock_purchase_request,
}

# 工作流目录（职场「工作流」卡片展示用）：步骤 + 示例指令
WORKFLOW_META: dict[str, dict] = {
    "adp-onboarding": {
        "steps": ["资料核对", "账号开通", "工牌发放"],
        "demo_prompt": "帮我整理新员工入职准备清单",
    },
    "expense-claim": {
        "steps": ["报销申请提交", "直属领导审批", "财务复核打款"],
        "demo_prompt": "帮我提交差旅报销",
    },
    "leave-request": {
        "steps": ["提交请假申请", "直属领导审批", "考勤记录同步"],
        "demo_prompt": "帮我请一天年假",
    },
    "meeting-notes": {
        "steps": ["会议记录采集", "结构化整理", "分发到共享盘"],
        "demo_prompt": "整理上周架构部周会纪要",
    },
    "weekly-report": {
        "steps": ["汇总本周完成", "整理下周计划", "标注风险项"],
        "demo_prompt": "生成本周费用周报",
    },
    "purchase-request": {
        "steps": ["采购申请", "三家比价", "采购下单", "资产入库"],
        "demo_prompt": "采购两台显示器",
    },
    "rpa-report": {
        "steps": ["拉取数据", "生成报表", "归档"],
        "demo_prompt": "生成入职权限报表",
    },
}


def run_adapter(plugin: models.Plugin, params: dict) -> dict:
    handler = REGISTRY.get(plugin.endpoint_ref)
    if handler is None:
        raise RuntimeError(f"未注册的 Mock Adapter：{plugin.endpoint_ref}")
    return handler(plugin, params)
