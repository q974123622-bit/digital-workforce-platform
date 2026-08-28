import pytest
from sqlalchemy import select

from app import models
from app.services import policy
from app.services.capability_contract import plugin_contract
from app.services.capability_executor import execute_capability
from app.services.identity import resolve_identity
from app.services.memory_service import capture_turn, retrieve_for_prompt
from app.services.policy import DECISION_ALLOW, DECISION_DENY, ResourceRef, evaluate


def test_memory_plugin_contract_accepts_the_logical_local_endpoint(db_session):
    plugin = models.Plugin(
        id="agent-memory-test",
        name="本地记忆测试",
        type="memory",
        endpoint_ref="memory://agent-local",
        data_level="L2",
        status="active",
    )
    db_session.add(plugin)
    db_session.flush()

    contract = plugin_contract(plugin)

    assert contract.ready is True
    assert contract.kind == "memory"
    assert contract.actions == ["search"]
    assert contract.executor["primary"] == "adapter"
    assert contract.executor["adapter_ref"] == "memory://agent-local"
    assert contract.input_schema["required"] == ["query"]
    assert contract.input_schema["properties"]["limit"]["maximum"] == 10


def test_memory_plugin_contract_rejects_an_unknown_endpoint(db_session):
    plugin = models.Plugin(
        id="agent-memory-invalid",
        name="错误记忆测试",
        type="memory",
        endpoint_ref="mock://memory",
        data_level="L2",
        status="active",
    )

    contract = plugin_contract(plugin)

    assert contract.ready is False
    assert any("endpoint" in issue for issue in contract.issues or [])


@pytest.mark.parametrize("primary", [[], {}])
def test_memory_plugin_contract_handles_invalid_primary_type(db_session, primary):
    plugin = models.Plugin(
        id="agent-memory-invalid-primary",
        name="错误执行器类型测试",
        type="memory",
        endpoint_ref="memory://agent-local",
        data_level="L2",
        status="active",
        runtime_meta={"executor": {"primary": primary}},
    )

    contract = plugin_contract(plugin)

    assert contract.ready is False
    assert any("执行器" in issue for issue in contract.issues or [])


def test_memory_plugin_contract_cannot_be_reconfigured_as_a_general_tool(db_session):
    plugin = models.Plugin(
        id="agent-memory-invalid-meta",
        name="被覆盖的记忆测试",
        type="memory",
        endpoint_ref="memory://agent-local",
        data_level="L2",
        status="active",
        runtime_meta={
            "actions": ["write"],
            "executor": {"primary": "harness", "tool": "http", "fallback": "demo_adapter"},
        },
    )

    contract = plugin_contract(plugin)

    assert contract.ready is False
    assert any("memory" in issue for issue in contract.issues or [])


def test_l3_memory_search_requires_a_search_whitelist(db_session):
    subject = resolve_identity(db_session, "VE-0003")
    assert subject is not None
    resource = ResourceRef(type="memory", id="agent-memory-l3", data_level="L3")

    db_session.add(
        models.EmployeePluginGrant(
            employee_id="VE-0003",
            plugin_id="agent-memory-l3",
            action="search",
            decision_mode=DECISION_ALLOW,
            grant_source="seed",
        )
    )
    db_session.flush()

    denied = evaluate(db_session, subject, resource, "search")
    assert denied.decision == DECISION_DENY
    assert denied.policy_id == "P-DATA-003"

    db_session.add(
        models.EmployeePluginGrant(
            employee_id="VE-0003",
            plugin_id="agent-memory-l3",
            action="search",
            decision_mode=DECISION_ALLOW,
            grant_source="whitelist",
        )
    )
    db_session.flush()

    allowed = evaluate(db_session, subject, resource, "search")
    assert allowed.decision == DECISION_ALLOW
    assert allowed.policy_id == "P-DATA-003"


def test_generic_capability_executor_cannot_bypass_memory_gateway(db_session):
    plugin = db_session.get(models.Plugin, "agent-memory")
    assert plugin is not None

    with pytest.raises(RuntimeError, match="Gateway"):
        execute_capability(plugin, {"query": "张三"}, trace_id="trace-r2-memory", context=None)


def test_memory_is_a_policy_resource_and_grant_controls_access(db_session):
    subject = resolve_identity(db_session, "VE-0003")
    assert subject is not None
    resource = ResourceRef(type="memory", id="agent-memory", data_level="L2")

    denied = evaluate(db_session, subject, resource, "search")
    assert denied.decision == DECISION_DENY
    assert "未授权" in denied.reason

    db_session.add(
        models.EmployeePluginGrant(
            employee_id="VE-0003",
            plugin_id="agent-memory",
            action="search",
            decision_mode="allow",
        )
    )
    db_session.flush()

    allowed = evaluate(db_session, subject, resource, "search")
    assert allowed.decision == DECISION_ALLOW


def test_can_use_memory_tool_allows_an_authorized_l2_employee(db_session):
    assert policy.can_use_memory_tool(db_session, "DT-E10281") is True


def test_can_use_memory_tool_rejects_a_granted_employee_below_plugin_data_level(db_session):
    employee = db_session.get(models.DigitalEmployee, "DT-E10281")
    assert employee is not None
    employee.max_data_level = "L1"
    db_session.flush()

    assert policy.can_use_memory_tool(db_session, "DT-E10281") is False


def test_can_use_memory_tool_rejects_an_unknown_employee(db_session):
    assert policy.can_use_memory_tool(db_session, "UNKNOWN") is False


def test_agent_memory_seed_uses_l2_and_grants_demo_employees(db_session):
    plugin = db_session.get(models.Plugin, "agent-memory")

    assert plugin is not None
    assert plugin.type == "memory"
    assert plugin.endpoint_ref == "memory://agent-local"
    assert plugin.data_level == "L2"
    assert plugin_contract(plugin).ready is True

    grants = db_session.scalars(
        select(models.EmployeePluginGrant).where(
            models.EmployeePluginGrant.plugin_id == "agent-memory",
            models.EmployeePluginGrant.action == "search",
        )
    ).all()
    assert {grant.employee_id for grant in grants} >= {"DT-E10281", "DT-E20999", "VE-0001", "VE-0002"}

    intern_twin = db_session.get(models.DigitalEmployee, "DT-E20999")
    assert intern_twin is not None
    assert intern_twin.max_data_level == "L2"
    assert policy.can_use_memory_tool(db_session, "DT-E20999") is True


def test_retrieved_memory_exposes_the_real_data_level(db_session):
    memory_id = capture_turn(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-R2-A",
        source_ref="chat:S-R2-A:assistant:1",
        user_text="请记住张三的 IT 账号还没有开通。",
        assistant_text="好的，我会记住这条信息。",
    )
    assert memory_id is not None

    hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="VE-0001",
        query="张三 IT 账号",
        current_session_id="S-R2-B",
    )

    assert hits
    assert hits[0].memory_id == memory_id
    assert hits[0].data_level == "L2"
