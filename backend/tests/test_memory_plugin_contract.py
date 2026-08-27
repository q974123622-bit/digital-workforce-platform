from sqlalchemy import select

from app import models
from app.services.capability_contract import plugin_contract
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
