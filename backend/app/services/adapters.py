"""Mock Adapter 注册表（Sprint 2）。

全部为虚构数据，不接任何真实系统。业务模块不得直接调用本模块，
必须经 Plugin Gateway（gateway.invoke_plugin）。
"""

import json
from pathlib import Path

from .. import models
from .agentteams_database_scenarios import DatabaseScenarioCatalog


_ONBOARDING_FIXTURE = (
    Path(__file__).resolve().parents[3] / "mock-data" / "agentteams" / "onboarding-a.json"
)
_ONBOARDING_EMPLOYEE_REF = "新员工A"
_ROLE_CHANGE_FIXTURE = (
    Path(__file__).resolve().parents[3] / "mock-data" / "agentteams" / "role-change-zhang-ming.json"
)
_ROLE_CHANGE_EMPLOYEE_REF = "张明"
_ROLE_CHANGE_APPROVAL_REF = "AUD-RC-001"
_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_DATABASE_SCENARIOS: DatabaseScenarioCatalog | None = None


class AdapterInputError(ValueError):
    """A caller supplied invalid input to a governed demo adapter."""


def _validate_onboarding_input(params: dict) -> None:
    """Validate only the fixed, user-supplied Task 4 input surface."""
    if not isinstance(params, dict) or set(params) != {"employee_ref"}:
        raise AdapterInputError("invalid_params")
    employee_ref = params.get("employee_ref")
    if not isinstance(employee_ref, str) or employee_ref != _ONBOARDING_EMPLOYEE_REF:
        raise AdapterInputError("invalid_employee_ref")


def _onboarding_fixture_section(params: dict, section_name: str) -> dict:
    """Read one fixed fictional fixture section after strict request validation.

    The request controls neither fixture location nor section selection. Callers
    receive a newly built domain view, never the parsed fixture itself.
    """
    _validate_onboarding_input(params)

    try:
        fixture = json.loads(_ONBOARDING_FIXTURE.read_text(encoding="utf-8"))
        section = fixture[section_name][_ONBOARDING_EMPLOYEE_REF]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError("onboarding_demo_fixture_unavailable") from error
    if not isinstance(section, dict):
        raise RuntimeError("onboarding_demo_fixture_unavailable")
    return section


def _bounded_evidence(
    items: object, *, include_source_evidence_ids: bool = False,
) -> list[dict[str, object]]:
    if not isinstance(items, list):
        raise RuntimeError("onboarding_demo_fixture_unavailable")
    bounded: list[dict[str, str]] = []
    for item in items:
        if not isinstance(item, dict):
            raise RuntimeError("onboarding_demo_fixture_unavailable")
        evidence_id = item.get("evidence_id")
        label = item.get("item")
        status = item.get("status")
        if not all(isinstance(value, str) and value for value in (evidence_id, label, status)):
            raise RuntimeError("onboarding_demo_fixture_unavailable")
        record: dict[str, object] = {
            "evidence_id": evidence_id,
            "item": label,
            "status": status,
        }
        if include_source_evidence_ids:
            source_ids = item.get("source_evidence_ids")
            if (
                not isinstance(source_ids, list)
                or not source_ids
                or not all(isinstance(value, str) and value for value in source_ids)
            ):
                raise RuntimeError("onboarding_demo_fixture_unavailable")
            record["source_evidence_ids"] = list(source_ids)
        bounded.append(record)
    return bounded


def _mock_agentteams_onboarding_employee(_plugin: models.Plugin, params: dict) -> dict:
    section = _onboarding_fixture_section(params, "hr_materials")
    materials = _bounded_evidence(section.get("materials"))
    return {
        "source": "demo",
        "employee_ref": _ONBOARDING_EMPLOYEE_REF,
        "readiness": str(section.get("readiness", "")),
        "evidence_ids": [item["evidence_id"] for item in materials],
        "materials": materials,
    }


def _onboarding_specialist_view(params: dict, section_name: str, result_key: str) -> dict:
    section = _onboarding_fixture_section(params, section_name)
    evidence = _bounded_evidence(section.get(result_key))
    return {
        "source": "demo",
        "employee_ref": _ONBOARDING_EMPLOYEE_REF,
        "readiness": str(section.get("readiness", "")),
        "evidence_ids": [item["evidence_id"] for item in evidence],
        result_key: evidence,
    }


def _mock_agentteams_onboarding_materials(_plugin: models.Plugin, params: dict) -> dict:
    return _onboarding_specialist_view(params, "hr_specialist_materials", "materials")


def _mock_agentteams_onboarding_training(_plugin: models.Plugin, params: dict) -> dict:
    return _onboarding_specialist_view(params, "hr_training", "training")


def _mock_agentteams_it_accounts(_plugin: models.Plugin, params: dict) -> dict:
    return _onboarding_specialist_view(params, "it_accounts", "accounts")


def _mock_agentteams_it_devices(_plugin: models.Plugin, params: dict) -> dict:
    return _onboarding_specialist_view(params, "it_devices", "devices")


def _mock_agentteams_it_readiness(_plugin: models.Plugin, params: dict) -> dict:
    section = _onboarding_fixture_section(params, "it_readiness")
    accounts = _bounded_evidence(section.get("accounts"))
    pending_items = _bounded_evidence(section.get("pending_items"))
    return {
        "source": "demo",
        "employee_ref": _ONBOARDING_EMPLOYEE_REF,
        "readiness": str(section.get("readiness", "")),
        "evidence_ids": [item["evidence_id"] for item in accounts + pending_items],
        "accounts": accounts,
        "pending_items": pending_items,
    }


def _mock_agentteams_onboarding_evidence(_plugin: models.Plugin, params: dict) -> dict:
    section = _onboarding_fixture_section(params, "audit_evidence")
    evidence = _bounded_evidence(
        section.get("evidence"), include_source_evidence_ids=True,
    )
    open_gaps = _bounded_evidence(
        section.get("open_gaps"), include_source_evidence_ids=True,
    )
    return {
        "source": "demo",
        "employee_ref": _ONBOARDING_EMPLOYEE_REF,
        "readiness": str(section.get("readiness", "")),
        "evidence_ids": [item["evidence_id"] for item in evidence + open_gaps],
        "evidence": evidence,
        "open_gaps": open_gaps,
    }


def _validate_role_change_input(params: dict) -> None:
    if not isinstance(params, dict) or set(params) != {"employee_ref"}:
        raise AdapterInputError("invalid_params")
    if params.get("employee_ref") != _ROLE_CHANGE_EMPLOYEE_REF:
        raise AdapterInputError("invalid_employee_ref")


def _validate_role_change_activation_input(params: dict) -> None:
    if not isinstance(params, dict) or set(params) != {"employee_ref", "approval_ref"}:
        raise AdapterInputError("invalid_params")
    if params.get("employee_ref") != _ROLE_CHANGE_EMPLOYEE_REF:
        raise AdapterInputError("invalid_employee_ref")
    if params.get("approval_ref") != _ROLE_CHANGE_APPROVAL_REF:
        raise AdapterInputError("invalid_approval_ref")


def _role_change_fixture_section(params: dict, section_name: str, *, activation: bool = False) -> dict:
    ( _validate_role_change_activation_input if activation else _validate_role_change_input)(params)
    try:
        fixture = json.loads(_ROLE_CHANGE_FIXTURE.read_text(encoding="utf-8"))
        section = fixture[section_name]
    except (OSError, ValueError, KeyError, TypeError) as error:
        raise RuntimeError("role_change_demo_fixture_unavailable") from error
    if not isinstance(section, dict):
        raise RuntimeError("role_change_demo_fixture_unavailable")
    return section


def _role_change_view(params: dict, section_name: str, result_key: str, *, activation: bool = False) -> dict:
    section = _role_change_fixture_section(params, section_name, activation=activation)
    evidence_ids = section.get("evidence_ids")
    result = section.get(result_key)
    if (
        not isinstance(evidence_ids, list)
        or not all(isinstance(item, str) and item for item in evidence_ids)
        or not isinstance(result, dict)
    ):
        raise RuntimeError("role_change_demo_fixture_unavailable")
    response = {
        "source": "demo",
        "employee_ref": _ROLE_CHANGE_EMPLOYEE_REF,
        "evidence_ids": list(evidence_ids),
        result_key: dict(result),
    }
    approval_ref = section.get("approval_ref")
    if isinstance(approval_ref, str) and approval_ref:
        response["approval_ref"] = approval_ref
    return response


def _mock_agentteams_role_change_hr(_plugin: models.Plugin, params: dict) -> dict:
    return _role_change_view(params, "hr_role_change", "role_change")


def _mock_agentteams_role_change_it_plan(_plugin: models.Plugin, params: dict) -> dict:
    return _role_change_view(params, "it_permission_plan", "permission_plan")


def _mock_agentteams_role_change_audit(_plugin: models.Plugin, params: dict) -> dict:
    return _role_change_view(params, "audit_review", "review")


def _mock_agentteams_role_change_it_activate(_plugin: models.Plugin, params: dict) -> dict:
    return _role_change_view(params, "it_activation", "activation", activation=True)


def _database_catalog() -> DatabaseScenarioCatalog:
    global _DATABASE_SCENARIOS
    if _DATABASE_SCENARIOS is None:
        _DATABASE_SCENARIOS = DatabaseScenarioCatalog.from_root(_PROJECT_ROOT)
    return _DATABASE_SCENARIOS


def _validate_database_inspection_input(params: dict) -> None:
    if not isinstance(params, dict) or set(params) != {
        "scenario_id", "signal", "filters", "limit",
    }:
        raise AdapterInputError("invalid_database_inspection")
    try:
        _database_catalog().inspect(
            params.get("scenario_id"),
            params.get("signal"),
            filters=params.get("filters"),
            limit=params.get("limit"),
        )
    except ValueError:
        raise AdapterInputError("invalid_database_inspection") from None


def _mock_agentteams_database_inspector(_plugin: models.Plugin, params: dict) -> dict:
    _validate_database_inspection_input(params)
    inspection = _database_catalog().inspect(
        params["scenario_id"],
        params["signal"],
        filters=params["filters"],
        limit=params["limit"],
    )
    return inspection.model_dump(mode="json")


_INPUT_VALIDATORS: dict[str, callable] = {
    "mock://agentteams/onboarding-employee": _validate_onboarding_input,
    "mock://agentteams/onboarding-materials": _validate_onboarding_input,
    "mock://agentteams/onboarding-training": _validate_onboarding_input,
    "mock://agentteams/it-readiness": _validate_onboarding_input,
    "mock://agentteams/it-accounts": _validate_onboarding_input,
    "mock://agentteams/it-devices": _validate_onboarding_input,
    "mock://agentteams/onboarding-evidence": _validate_onboarding_input,
    "mock://agentteams/role-change/hr": _validate_role_change_input,
    "mock://agentteams/role-change/it-plan": _validate_role_change_input,
    "mock://agentteams/role-change/audit": _validate_role_change_input,
    "mock://agentteams/role-change/it-activate": _validate_role_change_activation_input,
    "mock://agentteams/database-inspector": _validate_database_inspection_input,
}


def validate_adapter_input(plugin: models.Plugin, params: dict) -> None:
    """Perform side-effect-free validation for endpoints with strict inputs.

    This intentionally ignores every other legacy adapter. It does not load a
    fixture or invoke a handler, so callers may safely run it before a Harness.
    """
    validator = _INPUT_VALIDATORS.get(plugin.endpoint_ref)
    if validator is not None:
        validator(params)


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
        "employee_name": params.get("employee_name") or "该员工",
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
    "mock://agentteams/onboarding-employee": _mock_agentteams_onboarding_employee,
    "mock://agentteams/onboarding-materials": _mock_agentteams_onboarding_materials,
    "mock://agentteams/onboarding-training": _mock_agentteams_onboarding_training,
    "mock://agentteams/it-readiness": _mock_agentteams_it_readiness,
    "mock://agentteams/it-accounts": _mock_agentteams_it_accounts,
    "mock://agentteams/it-devices": _mock_agentteams_it_devices,
    "mock://agentteams/onboarding-evidence": _mock_agentteams_onboarding_evidence,
    "mock://agentteams/role-change/hr": _mock_agentteams_role_change_hr,
    "mock://agentteams/role-change/it-plan": _mock_agentteams_role_change_it_plan,
    "mock://agentteams/role-change/audit": _mock_agentteams_role_change_audit,
    "mock://agentteams/role-change/it-activate": _mock_agentteams_role_change_it_activate,
    "mock://agentteams/database-inspector": _mock_agentteams_database_inspector,
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
        "owner_employee": "VE-0001",
    },
    "expense-claim": {
        "steps": ["报销申请提交", "直属领导审批", "财务复核打款"],
        "demo_prompt": "帮我提交差旅报销",
        "owner_employee": "VE-0002",
    },
    "leave-request": {
        "steps": ["提交请假申请", "直属领导审批", "考勤记录同步"],
        "demo_prompt": "帮我请一天年假",
        "owner_employee": "VE-0002",
    },
    "meeting-notes": {
        "steps": ["会议记录采集", "结构化整理", "分发到共享盘"],
        "demo_prompt": "整理上周架构部周会纪要",
        "owner_employee": "VE-0001",
    },
    "weekly-report": {
        "steps": ["汇总本周完成", "整理下周计划", "标注风险项"],
        "demo_prompt": "生成本周费用周报",
        "owner_employee": "RPA-0001",
    },
    "purchase-request": {
        "steps": ["采购申请", "三家比价", "采购下单", "资产入库"],
        "demo_prompt": "采购两台显示器",
        "owner_employee": "VE-0004",
    },
    "rpa-report": {
        "steps": ["拉取数据", "生成报表", "归档"],
        "demo_prompt": "生成入职权限报表",
        "owner_employee": "RPA-0001",
    },
}


def run_adapter(plugin: models.Plugin, params: dict) -> dict:
    # Keep this validation for direct adapter callers that bypass capability execution.
    validate_adapter_input(plugin, params)
    handler = REGISTRY.get(plugin.endpoint_ref)
    if handler is None:
        raise RuntimeError(f"未注册的 Mock Adapter：{plugin.endpoint_ref}")
    return handler(plugin, params)
