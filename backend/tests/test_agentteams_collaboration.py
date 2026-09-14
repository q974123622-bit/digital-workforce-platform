import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from app import models
from app.services import config
from app.services.agentteams_protocol import AgentProtocolMessage


def _enable_test_leader(db_session):
    """Seed twins need a Matrix reference and explicit team membership for protocol tests."""
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")
    leader.runtime_ref = "@dwp-twin-e10281:local"
    db_session.add(models.TeamMember(
        team_id="TEAM-ONBOARD", employee_id=leader.employee_no, role="member",
    ))
    if not db_session.query(models.TeamPluginGrant).filter_by(
        team_id="TEAM-ONBOARD", plugin_id="weekly-report", action="execute"
    ).first():
        db_session.add(models.TeamPluginGrant(
            team_id="TEAM-ONBOARD",
            plugin_id="weekly-report",
            action="execute",
            decision_mode="allow",
            grant_source="test",
        ))
    db_session.flush()
    return leader


def _protocol(*, root_id, task_id, sender, receiver, type, key, payload, parent_id=None):
    return AgentProtocolMessage(
        version="1.0", message_id=f"MSG-{key}", type=type,
        root_task_id=root_id, task_id=task_id, parent_task_id=parent_id,
        trace_id=root_id, sender=sender, receiver=receiver,
        timestamp="2026-08-31T15:00:00+08:00", idempotency_key=key,
        payload=payload,
    )


def _matrix_sender(runtime_ref):
    return runtime_ref if runtime_ref.startswith("@") else f"@{runtime_ref}:{config.agentteams_matrix_domain()}"


def _create_run(db_session, *, thinking_enabled=True):
    from app.services.agentteams_collaboration import create_task

    leader = _enable_test_leader(db_session)
    return (
        create_task(
            db_session,
            team_id="TEAM-ONBOARD",
            leader_agent_id="DT-E10281",
            title="新员工入职",
            instruction="协调完成入职准备",
            material={"employee": "李四"},
            thinking_enabled=thinking_enabled,
        ),
        leader,
    )


def _delegate(db_session, run, sender, matrix_sender, subtasks, key="delegate-1", parent=None):
    from app.services.agentteams_collaboration import apply_message

    return apply_message(
        db_session,
        matrix_sender=_matrix_sender(matrix_sender),
        message=_protocol(
            root_id=run.id,
            task_id=parent.id if parent is not None else run.id,
            parent_id=parent.parent_task_id if parent is not None else None,
            sender=sender,
            receiver="platform",
            type="task.delegate",
            key=f"{run.id}:{key}",
            payload={"subtasks": subtasks},
        ),
    )


def _subtask(key, assignee, *, depends_on=None, required=True):
    capabilities = {"VE-0002": "hr-employee-mcp", "VE-0003": "adp-onboarding", "RPA-0001": "weekly-report"}
    return {
        "client_task_key": key,
        "title": f"{key} task",
        "instruction": f"complete {key}",
        "assignee_agent_id": assignee,
        "depends_on": depends_on or [],
        "required": required,
        "required_capability": capabilities.get(assignee, "unavailable"),
    }


def _apply_node_message(db_session, run, node, *, type, key, payload):
    from app.services.agentteams_collaboration import apply_message

    employee = db_session.get(models.DigitalEmployee, node.assignee_agent_id)
    return apply_message(
        db_session,
        matrix_sender=_matrix_sender(employee.runtime_ref),
        message=_protocol(
            root_id=run.id,
            task_id=node.id,
            parent_id=node.parent_task_id,
            sender=employee.employee_no,
            receiver="platform",
            type=type,
            key=f"{run.id}:{node.id}:{key}",
            payload=payload,
        ),
    )


def _start_node(db_session, run, node):
    _apply_node_message(db_session, run, node, type="task.accepted", key="accepted", payload={})
    _apply_node_message(db_session, run, node, type="task.progress", key="progress", payload={"progress": 10})


def test_recursive_task_records_persist(db_session):
    run = models.TaskRun(
        id="AT-TASK-1", team_id="TEAM-ONBOARD", trace_id="AT-TASK-1",
        request="完成入职协作分析", status="running", source="agentteams",
    )
    root = models.AgentTaskNode(
        id="AT-TASK-1", root_task_id="AT-TASK-1", parent_task_id=None,
        client_task_key="root", title="入职协作分析", instruction="完成分析",
        coordinator_agent_id="DT-E10281", assignee_agent_id="DT-E10281",
        capability="coordinate", depth=0, required=True, status="planning",
        input_payload={}, output_payload={}, trace_id="AT-TASK-1",
    )
    child = models.AgentTaskNode(
        id="AT-TASK-2", root_task_id="AT-TASK-1", parent_task_id="AT-TASK-1",
        client_task_key="policy", title="制度核查", instruction="核查制度",
        coordinator_agent_id="DT-E10281", assignee_agent_id="VE-0001",
        capability="knowledge.read", depth=1, required=True, status="assigned",
        input_payload={}, output_payload={}, trace_id="AT-TASK-1",
    )
    db_session.add_all([run, root, child])
    db_session.commit()
    assert db_session.get(models.AgentTaskNode, "AT-TASK-2").parent_task_id == "AT-TASK-1"


def test_matrix_event_id_is_unique(db_session):
    first = models.AgentMessage(
        id="MSG-1", root_task_id="AT-TASK-1", task_id="AT-TASK-1",
        message_type="task.submit", sender_agent_id="platform",
        receiver_agent_id="manager", body="任务", payload={},
        matrix_room_id="!room:test", matrix_event_id="$event-1",
        idempotency_key="AT-TASK-1:submit:1", direction="outbound",
        delivery_status="sent",
    )
    db_session.add(first)
    db_session.commit()
    duplicate = models.AgentMessage(
        id="MSG-2", root_task_id="AT-TASK-1", task_id="AT-TASK-1",
        message_type="task.submit", sender_agent_id="platform",
        receiver_agent_id="manager", body="任务", payload={},
        matrix_room_id="!room:test", matrix_event_id="$event-1",
        idempotency_key="AT-TASK-1:submit:2", direction="outbound",
        delivery_status="sent",
    )
    db_session.add(duplicate)
    try:
        db_session.commit()
        raised = False
    except Exception:
        db_session.rollback()
        raised = True
    assert raised


def test_create_task_writes_root_and_outbox(db_session):
    from app.services.agentteams_collaboration import create_task

    _enable_test_leader(db_session)
    run = create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="DT-E10281",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={"employee": "李四"},
        thinking_enabled=False,
    )

    root = db_session.get(models.AgentTaskNode, run.id)
    outbox = db_session.query(models.AgentMessage).filter_by(task_id=run.id).all()
    assert root.status == "planning"
    assert len(outbox) == 1
    assert outbox[0].message_type == "task.submit"
    assert outbox[0].delivery_status == "pending"
    assert outbox[0].payload["team_catalog_version"] == 1
    assert outbox[0].payload["team_capability_catalog"]
    assert root.input_payload["_agentteams_runtime"] == {"thinking_enabled": False}
    assert outbox[0].payload["thinking_enabled"] is False


def test_create_task_defaults_to_thinking_enabled(db_session):
    from app.services.agentteams_collaboration import create_task

    _enable_test_leader(db_session)
    run = create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="DT-E10281",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={},
    )

    submit = db_session.query(models.AgentMessage).filter_by(task_id=run.id).one()
    assert submit.payload["thinking_enabled"] is True


def _relationship_runtime():
    relationships = {
        "VE-0001": {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
        "VE-0002": {"role": "HR负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
        "VE-0003": {"role": "IT负责人", "reports_to": ["VE-0001"], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
        "VE-0005": {"role": "审计专员", "reports_to": ["VE-0001"], "direct_reports": []},
        "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
        "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
        "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
        "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
    }
    return SimpleNamespace(team_id="TEAM-ONBOARD", team_relationships=relationships)


def _prepare_relationship_team(db_session):
    employees = {
        "VE-0001": ("Manager", "管理"),
        "VE-0002": ("HR负责人", "人力资源部"),
        "VE-0003": ("IT负责人", "IT服务部"),
        "VE-0005": ("审计专员", "审计部"),
        "VE-HR-MATERIAL": ("HR材料专员", "人力资源部"),
        "VE-HR-TRAINING": ("HR培训专员", "人力资源部"),
        "VE-IT-ACCOUNT": ("IT账号专员", "IT服务部"),
        "VE-IT-DEVICE": ("IT设备专员", "IT服务部"),
    }
    team = db_session.get(models.Team, "TEAM-ONBOARD")
    team.leader_employee_id = "VE-0001"
    current_members = {
        item.employee_id
        for item in db_session.query(models.TeamMember).filter_by(team_id=team.id)
    }
    for employee_id, (name, department) in employees.items():
        employee = db_session.get(models.DigitalEmployee, employee_id)
        if employee is None:
            employee = models.DigitalEmployee(
                employee_no=employee_id,
                name=name,
                type="virtual",
                owner_human_no="E10281",
                department=department,
            )
            db_session.add(employee)
        employee.status = "active"
        employee.runtime_type = "agentteams"
        employee.runtime_ref = f"@{employee_id.lower()}:local"
        if employee_id not in current_members:
            db_session.add(models.TeamMember(team_id=team.id, employee_id=employee_id, role="worker"))
    db_session.flush()


def _relationship_catalog():
    capabilities = {
        "VE-0001": [],
        "VE-0002": ["hr.onboarding.coordinate"],
        "VE-0003": ["it.onboarding.coordinate"],
        "VE-0005": ["audit.evidence.read"],
        "VE-HR-MATERIAL": ["hr.materials.read"],
        "VE-HR-TRAINING": ["hr.training.read"],
        "VE-IT-ACCOUNT": ["it.accounts.read"],
        "VE-IT-DEVICE": ["it.devices.read"],
    }
    return [
        {
            "agent_id": agent_id,
            "name": agent_id,
            "department": "",
            "runtime_bound": True,
            "capabilities": values,
            "tool_summaries": values,
        }
        for agent_id, values in capabilities.items()
    ]


def test_platform_projects_relationship_graph_into_submit_and_assign(
    db_session, monkeypatch
):
    from app.services import agentteams_collaboration as collaboration_module
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService

    _prepare_relationship_team(db_session)
    monkeypatch.setattr(config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setattr(
        collaboration_module, "load_strict_runtime_config", _relationship_runtime
    )
    service = AgentTeamsCollaborationService()
    catalog = _relationship_catalog()
    monkeypatch.setattr(
        service,
        "_team_capability_catalog",
        lambda _db, _team_id, owner_human_no="": catalog,
    )

    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={"employee": "李四"},
        owner_human_no="E10281",
    )
    submit = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.submit"
    ).one()
    assert submit.payload["team_relationship_graph"] == {
        "self": {"agent_id": "VE-0001", "role": "Manager"},
        "reports_to": [],
        "direct_reports": [
            {"agent_id": "VE-0002", "role": "HR负责人", "capabilities": ["hr.onboarding.coordinate"]},
            {"agent_id": "VE-0003", "role": "IT负责人", "capabilities": ["it.onboarding.coordinate"]},
            {"agent_id": "VE-0005", "role": "审计专员", "capabilities": ["audit.evidence.read"]},
        ],
    }

    service.apply_message(
        db_session,
        matrix_sender="@ve-0001:local",
        message=_protocol(
            root_id=run.id,
            task_id=run.id,
            sender="VE-0001",
            receiver="platform",
            type="task.delegate",
            key=f"{run.id}:department-leads",
            payload={"subtasks": [
                {
                    "client_task_key": "hr-lead", "title": "HR统筹", "instruction": "组织HR核查",
                    "assignee_agent_id": "VE-0002", "depends_on": [], "required_capability": "hr.onboarding.coordinate",
                },
                {
                    "client_task_key": "it-lead", "title": "IT统筹", "instruction": "组织IT核查",
                    "assignee_agent_id": "VE-0003", "depends_on": [], "required_capability": "it.onboarding.coordinate",
                },
            ]},
        ),
    )

    assignments = {
        item.receiver_agent_id: item.payload
        for item in db_session.query(models.AgentMessage).filter_by(
            root_task_id=run.id, message_type="task.assign"
        )
    }
    assert [item["agent_id"] for item in assignments["VE-0002"]["team_relationship_graph"]["direct_reports"]] == [
        "VE-HR-MATERIAL", "VE-HR-TRAINING"
    ]
    assert [item["agent_id"] for item in assignments["VE-0003"]["team_relationship_graph"]["direct_reports"]] == [
        "VE-IT-ACCOUNT", "VE-IT-DEVICE"
    ]


def test_platform_rejects_forged_non_direct_report_without_partial_tree(
    db_session, monkeypatch
):
    from app.services import agentteams_collaboration as collaboration_module
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict

    _prepare_relationship_team(db_session)
    monkeypatch.setattr(config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setattr(
        collaboration_module, "load_strict_runtime_config", _relationship_runtime
    )
    service = AgentTeamsCollaborationService()
    catalog = _relationship_catalog()
    monkeypatch.setattr(
        service,
        "_team_capability_catalog",
        lambda _db, _team_id, owner_human_no="": catalog,
    )
    run = service.create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="VE-0001",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={},
        owner_human_no="E10281",
    )
    forged = {
        "client_task_key": "skip-hr-lead", "title": "越级派发", "instruction": "直接找材料专员",
        "assignee_agent_id": "VE-HR-MATERIAL", "depends_on": [], "required_capability": "hr.materials.read",
    }

    with pytest.raises(CollaborationConflict, match="delegate_target_not_direct_report"):
        service.apply_message(
            db_session,
            matrix_sender="@ve-0001:local",
            message=_protocol(
                root_id=run.id,
                task_id=run.id,
                sender="VE-0001",
                receiver="platform",
                type="task.delegate",
                key=f"{run.id}:forged",
                payload={"subtasks": [forged]},
            ),
        )

    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_manager_delegate_creates_recursive_children(db_session):
    from app.services.agentteams_collaboration import apply_message, create_task

    leader = _enable_test_leader(db_session)
    run = create_task(
        db_session,
        team_id="TEAM-ONBOARD",
        leader_agent_id="DT-E10281",
        title="新员工入职",
        instruction="协调完成入职准备",
        material={},
        thinking_enabled=False,
    )
    message = _protocol(
        root_id=run.id,
        task_id=run.id,
        sender="DT-E10281",
        receiver="platform",
        type="task.delegate",
        key=f"{run.id}:delegate:1",
        payload={
            "subtasks": [
                {
                    "client_task_key": "policy", "title": "制度核查",
                    "instruction": "核查制度", "assignee_agent_id": "VE-0002",
                    "depends_on": [], "required_capability": "hr-employee-mcp",
                },
                {
                    "client_task_key": "account", "title": "账号开通",
                    "instruction": "开通账号", "assignee_agent_id": "VE-0003",
                    "depends_on": ["policy"], "required_capability": "adp-onboarding",
                },
            ],
        },
    )

    apply_message(db_session, matrix_sender=leader.runtime_ref, message=message)

    children = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).all()
    assert {child.client_task_key for child in children} == {"policy", "account"}
    assert db_session.get(models.AgentTaskNode, run.id).status == "waiting_children"
    assignment = db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, message_type="task.assign", direction="outbound"
    ).one()
    assert assignment.payload["thinking_enabled"] is False


def test_worker_can_delegate_inside_own_subtree(db_session):
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, policy)

    _delegate(
        db_session,
        run,
        "VE-0002",
        db_session.get(models.DigitalEmployee, "VE-0002").runtime_ref,
        [_subtask("research", "VE-0003")],
        key="delegate-child",
        parent=policy,
    )

    grandchild = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=policy.id).one()
    assert grandchild.client_task_key == "research"
    assert policy.status == "waiting_children"


def test_delegate_rejects_cycle(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    with pytest.raises(CollaborationConflict, match="dependency_cycle"):
        _delegate(
            db_session,
            run,
            "DT-E10281",
            leader.runtime_ref,
            [
                _subtask("policy", "VE-0002", depends_on=["account"]),
                _subtask("account", "VE-0003", depends_on=["policy"]),
            ],
        )

    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0
    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"


def test_delegate_rejects_depth_four(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("one", "VE-0002")])
    first = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, first)
    _delegate(
        db_session, run, "VE-0002", db_session.get(models.DigitalEmployee, "VE-0002").runtime_ref,
        [_subtask("two", "VE-0003")], key="depth-2", parent=first,
    )
    second = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=first.id).one()
    _start_node(db_session, run, second)
    _delegate(
        db_session, run, "VE-0003", db_session.get(models.DigitalEmployee, "VE-0003").runtime_ref,
        [_subtask("three", "RPA-0001")], key="depth-3", parent=second,
    )
    third = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=second.id).one()
    _start_node(db_session, run, third)

    with pytest.raises(CollaborationConflict, match="max_depth_exceeded"):
        _delegate(
            db_session, run, "RPA-0001", db_session.get(models.DigitalEmployee, "RPA-0001").runtime_ref,
            [_subtask("four", "VE-0002")], key="depth-4", parent=third,
        )


def test_delegate_rejects_non_team_assignee(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    with pytest.raises(CollaborationConflict, match="invalid_team_assignee"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("buy", "VE-0004")])

    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_message_rejects_matrix_sender_mismatch(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, _leader = _create_run(db_session)
    with pytest.raises(CollaborationConflict, match="matrix_sender_mismatch"):
        _delegate(db_session, run, "DT-E10281", "@imposter:local", [_subtask("policy", "VE-0002")])

    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"


def test_duplicate_idempotency_key_has_no_second_effect(db_session):
    run, leader = _create_run(db_session)
    events = _delegate(
        db_session, run, "DT-E10281", leader.runtime_ref,
        [_subtask("policy", "VE-0002")], key="same-key",
    )
    counts = (
        db_session.query(models.AgentTaskNode).count(),
        db_session.query(models.AgentTaskEvent).count(),
        db_session.query(models.AgentMessage).count(),
    )

    repeated = _delegate(
        db_session, run, "DT-E10281", leader.runtime_ref,
        [_subtask("policy", "VE-0002")], key="same-key",
    )

    assert events
    assert repeated == []
    assert counts == (
        db_session.query(models.AgentTaskNode).count(),
        db_session.query(models.AgentTaskEvent).count(),
        db_session.query(models.AgentMessage).count(),
    )


def test_progress_updates_only_assigned_node(db_session):
    run, leader = _create_run(db_session)
    _delegate(
        db_session, run, "DT-E10281", leader.runtime_ref,
        [_subtask("policy", "VE-0002"), _subtask("account", "VE-0003")],
    )
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id, client_task_key="policy").one()
    account = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id, client_task_key="account").one()
    _apply_node_message(db_session, run, policy, type="task.accepted", key="accepted", payload={})

    _apply_node_message(db_session, run, policy, type="task.progress", key="progress", payload={"progress": 55})

    assert policy.status == "running"
    assert policy.progress == 55
    assert account.status == "assigned"
    assert db_session.get(models.AgentTaskNode, run.id).status == "waiting_children"
    assert db_session.get(models.TaskRun, run.id).status == "planning"


def test_required_children_trigger_parent_summarizing(db_session):
    run, leader = _create_run(db_session, thinking_enabled=False)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, policy)

    _apply_node_message(
        db_session, run, policy, type="task.result", key="result", payload={"result": "制度已核查"},
    )

    root = db_session.get(models.AgentTaskNode, run.id)
    summary_request = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.summary", direction="outbound"
    ).one()
    assert summary_request.payload["thinking_enabled"] is False
    summary_request = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.summary", direction="outbound",
    ).one()
    assert root.status == "summarizing"
    assert summary_request.receiver_agent_id == "DT-E10281"
    assert summary_request.delivery_status == "pending"
    assert summary_request.payload["team_catalog_version"] == 3
    assert summary_request.payload["team_capability_catalog"]
    assert summary_request.payload["existing_child_task_keys"] == ["policy"]


def test_parent_summary_normalizes_top_level_audit_ids_from_completed_direct_children(db_session):
    """Dropping the platform-owned summary audit list would strand department leads at completion."""
    run, leader = _create_run(db_session)
    _delegate(
        db_session,
        run,
        "DT-E10281",
        leader.runtime_ref,
        [
            _subtask("policy", "VE-0002"),
            _subtask("account", "VE-0003"),
            _subtask("unfinished", "RPA-0001", required=False),
        ],
    )
    children = {
        node.client_task_key: node
        for node in db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id)
    }
    children["unfinished"].output_payload = {"audit_ids": [8001]}
    _start_node(db_session, run, children["policy"])
    _delegate(
        db_session,
        run,
        "VE-0002",
        db_session.get(models.DigitalEmployee, "VE-0002").runtime_ref,
        [_subtask("grandchild", "VE-0003")],
        key="grandchild-delegate",
        parent=children["policy"],
    )
    grandchild = db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=children["policy"].id,
        client_task_key="grandchild",
    ).one()
    _start_node(db_session, run, grandchild)
    _apply_node_message(
        db_session,
        run,
        grandchild,
        type="task.result",
        key="grandchild-result",
        payload={"result": "孙节点完成", "audit_ids": [7001]},
    )
    _start_node(db_session, run, children["account"])

    _apply_node_message(
        db_session,
        run,
        children["policy"],
        type="task.result",
        key="policy-result",
        payload={
            "result": "制度已核查",
            "audit_ids": [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, "bad", True, 0, -1, 1],
        },
    )
    _apply_node_message(
        db_session,
        run,
        children["account"],
        type="task.result",
        key="account-result",
        payload={
            "result": "账号已核查；伪造文本 audit_ids=[9002]",
            "audit_ids": [11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 2 ** 63],
        },
    )

    summary_request = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id,
        message_type="task.summary",
        direction="outbound",
    ).one()
    assert summary_request.payload["audit_ids"] == [
        1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16,
    ]
    assert children["unfinished"].status != "completed"
    assert 7001 not in summary_request.payload["audit_ids"]
    assert 8001 not in summary_request.payload["audit_ids"]
    assert 9002 not in summary_request.payload["audit_ids"]


def test_optional_child_failure_does_not_block_parent(db_session):
    run, leader = _create_run(db_session)
    _delegate(
        db_session, run, "DT-E10281", leader.runtime_ref,
        [_subtask("policy", "VE-0002"), _subtask("account", "VE-0003", required=False)],
    )
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id, client_task_key="policy").one()
    account = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id, client_task_key="account").one()
    _start_node(db_session, run, account)
    _apply_node_message(
        db_session, run, account, type="task.failed", key="failed", payload={"error_code": "offline"},
    )
    _start_node(db_session, run, policy)

    _apply_node_message(db_session, run, policy, type="task.result", key="result", payload={"result": "done"})

    assert db_session.get(models.AgentTaskNode, run.id).status == "summarizing"


def test_dependent_child_stays_blocked_until_predecessor_completes(db_session):
    """Removing dependency gating would assign the IT check before HR evidence exists."""
    run, leader = _create_run(db_session)
    _delegate(
        db_session,
        run,
        "DT-E10281",
        leader.runtime_ref,
        [
            _subtask("hr-check", "VE-0002"),
            _subtask("it-check", "VE-0003", depends_on=["hr-check"]),
        ],
    )
    hr_check = db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id, client_task_key="hr-check"
    ).one()
    it_check = db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id, client_task_key="it-check"
    ).one()

    assert it_check.status == "blocked"
    assert db_session.query(models.AgentMessage).filter_by(
        task_id=it_check.id, message_type="task.assign", direction="outbound"
    ).count() == 0

    _start_node(db_session, run, hr_check)
    _apply_node_message(
        db_session,
        run,
        hr_check,
        type="task.result",
        key="result",
        payload={"result": "HR evidence", "token": "must-not-leak"},
    )

    assignment = db_session.query(models.AgentMessage).filter_by(
        task_id=it_check.id, message_type="task.assign", direction="outbound"
    ).one()
    assert it_check.status == "assigned"
    assert assignment.payload["required_capability"] == it_check.capability
    assert assignment.payload["dependency_results"] == [
        {"client_task_key": "hr-check", "status": "completed", "result": {"result": "HR evidence"}}
    ]
    assert db_session.query(models.AgentTaskEvent).filter_by(
        task_id=it_check.id, event_type="task.unblocked"
    ).count() == 1


def test_manager_can_delegate_follow_up_round_from_summarizing(db_session):
    """Removing the summarizing transition would prevent adaptive manager rounds."""
    run, leader = _create_run(db_session)
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "summarizing"
    root.input_payload = {"coordination": {"round": 1, "selected_skill_ids": ["scenario-a"]}}
    db_session.flush()

    _delegate(
        db_session,
        run,
        "DT-E10281",
        leader.runtime_ref,
        [_subtask("follow-up", "VE-0002")],
        key="follow-up",
    )

    assert root.status == "waiting_children"
    assert root.input_payload["coordination"] == {
        "round": 2,
        "selected_skill_ids": ["scenario-a"],
        "team_catalog_version": 1,
    }


def test_delegate_rejects_node_limit_without_partial_tree(db_session):
    """Removing total-node validation would persist a child after the root limit is full."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict

    run, leader = _create_run(db_session)
    service = AgentTeamsCollaborationService(max_nodes=1)
    root = db_session.get(models.AgentTaskNode, run.id)
    message = _protocol(
        root_id=run.id,
        task_id=run.id,
        sender="DT-E10281",
        receiver="platform",
        type="task.delegate",
        key=f"{run.id}:node-limit",
        payload={"subtasks": [_subtask("one", "VE-0002")]},
    )

    with pytest.raises(CollaborationConflict, match="max_nodes_exceeded"):
        service.apply_message(db_session, matrix_sender=_matrix_sender(leader.runtime_ref), message=message)

    assert root.status == "planning"
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0
    assert db_session.query(models.AgentMessage).filter_by(
        root_task_id=run.id, message_type="task.assign"
    ).count() == 0


def test_delegate_rejects_follow_up_round_limit_without_partial_tree(db_session):
    """Removing round validation would let the manager exceed its bounded follow-up rounds."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict

    run, leader = _create_run(db_session)
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "summarizing"
    root.input_payload = {"coordination": {"round": 1}}
    service = AgentTeamsCollaborationService(max_rounds=0)
    message = _protocol(
        root_id=run.id,
        task_id=run.id,
        sender="DT-E10281",
        receiver="platform",
        type="task.delegate",
        key=f"{run.id}:round-limit",
        payload={"subtasks": [_subtask("one", "VE-0002")]},
    )

    with pytest.raises(CollaborationConflict, match="max_rounds_exceeded"):
        service.apply_message(db_session, matrix_sender=_matrix_sender(leader.runtime_ref), message=message)

    assert root.status == "summarizing"
    assert root.input_payload == {"coordination": {"round": 1}}
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_delegate_rejects_capability_outside_current_team_catalog(db_session):
    """Removing capability validation would let a plan name an ability no current teammate can use."""
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    invalid = _subtask("unknown-capability", "VE-0002")
    invalid["required_capability"] = "not-granted-by-this-team"

    with pytest.raises(CollaborationConflict, match="capability_not_available"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [invalid])

    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_team_capability_catalog_only_lists_active_runtime_bound_team_members(db_session):
    """Inactive, unbound, or non-team employees must not enter the manager catalog."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService

    db_session.add_all([
        models.DigitalEmployee(
            employee_no="VE-INACTIVE-TEST",
            name="Inactive team member",
            type="virtual",
            owner_human_no="E10281",
            status="inactive",
            runtime_type="agentteams",
            runtime_ref="inactive-worker",
        ),
        models.DigitalEmployee(
            employee_no="VE-UNBOUND-TEST",
            name="Unbound team member",
            type="virtual",
            owner_human_no="E10281",
            status="active",
            runtime_type="agentteams",
            runtime_ref=None,
        ),
        models.TeamMember(
            team_id="TEAM-ONBOARD", employee_id="VE-INACTIVE-TEST", role="worker",
        ),
        models.TeamMember(
            team_id="TEAM-ONBOARD", employee_id="VE-UNBOUND-TEST", role="worker",
        ),
    ])
    db_session.flush()

    catalog = AgentTeamsCollaborationService()._team_capability_catalog(db_session, "TEAM-ONBOARD")
    catalog_ids = {item["agent_id"] for item in catalog}

    assert catalog_ids == {
        "VE-0001",
        "VE-0002",
        "VE-0003",
        "VE-0005",
        "VE-HR-MATERIAL",
        "VE-HR-TRAINING",
        "VE-IT-ACCOUNT",
        "VE-IT-DEVICE",
        "RPA-0001",
    }
    assert {"VE-0004", "VE-INACTIVE-TEST", "VE-UNBOUND-TEST"}.isdisjoint(catalog_ids)
    assert all(db_session.get(models.DigitalEmployee, agent_id).status == "active" for agent_id in catalog_ids)
    assert all(item["runtime_bound"] is True for item in catalog)


def _strict_runtime_document(
    *,
    manager_agent_id="VE-0001",
    manager_mxid="@strict-manager:local",
):
    """A credential-shaped test document; tests assert none of it becomes catalog data."""
    agents = {}
    for role, agent_id in {
        "manager": manager_agent_id, "hr": "VE-0002", "it": "VE-0003", "audit": "VE-0005",
        "hr_material": "VE-HR-MATERIAL", "hr_training": "VE-HR-TRAINING",
        "it_account": "VE-IT-ACCOUNT", "it_device": "VE-IT-DEVICE",
    }.items():
        agents[role] = {
            "agent_id": agent_id,
            "mxid": manager_mxid if role == "manager" else f"@strict-{role}:local",
            "token": f"private-{role}-token",
            "allowed_skill_ids": [f"{role}.skill"],
            "safe_tool_catalog": [],
        }
    return {
        "matrix_url": "http://matrix.local",
        "room_id": "!strict-room:local",
        "team_id": "TEAM-ONBOARD",
        "platform": {"mxid": "@platform:local", "token": "private-platform-token"},
        "agents": agents,
        "team_catalog": {
            "VE-0002": {"capabilities": ["hr.onboarding.coordinate"]},
            "VE-0003": {"capabilities": ["it.onboarding.coordinate"]},
            "VE-0005": {"capabilities": ["audit.evidence.read"]},
            "VE-HR-MATERIAL": {"capabilities": ["hr.materials.read"]},
            "VE-HR-TRAINING": {"capabilities": ["hr.training.read"]},
            "VE-IT-ACCOUNT": {"capabilities": ["it.accounts.read"]},
            "VE-IT-DEVICE": {"capabilities": ["it.devices.read"]},
        },
        "team_relationships": {
            manager_agent_id: {"role": "Manager", "reports_to": [], "direct_reports": ["VE-0002", "VE-0003", "VE-0005"]},
            "VE-0002": {"role": "HR负责人", "reports_to": [manager_agent_id], "direct_reports": ["VE-HR-MATERIAL", "VE-HR-TRAINING"]},
            "VE-0003": {"role": "IT负责人", "reports_to": [manager_agent_id], "direct_reports": ["VE-IT-ACCOUNT", "VE-IT-DEVICE"]},
            "VE-0005": {"role": "审计负责人", "reports_to": [manager_agent_id], "direct_reports": []},
            "VE-HR-MATERIAL": {"role": "HR材料专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-HR-TRAINING": {"role": "HR培训专员", "reports_to": ["VE-0002"], "direct_reports": []},
            "VE-IT-ACCOUNT": {"role": "IT账号专员", "reports_to": ["VE-0003"], "direct_reports": []},
            "VE-IT-DEVICE": {"role": "IT设备专员", "reports_to": ["VE-0003"], "direct_reports": []},
        },
    }


def test_strict_team_catalog_uses_validated_runtime_not_legacy_grants(db_session, monkeypatch, tmp_path):
    """Strict plans must use only the configured Matrix team, never a stale grant row."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService

    runtime_path = tmp_path / "strict-runtime.json"
    runtime_path.write_text(json.dumps(_strict_runtime_document()), encoding="utf-8")
    monkeypatch.setattr(config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(runtime_path))

    team = db_session.get(models.Team, "TEAM-ONBOARD")
    team.leader_employee_id = "VE-0001"
    member_ids = {member.employee_id for member in db_session.query(models.TeamMember).filter_by(team_id=team.id)}
    for employee_id in ("VE-0001", "VE-0002", "VE-0003", "VE-0005", "RPA-0001"):
        if employee_id not in member_ids:
            db_session.add(models.TeamMember(team_id=team.id, employee_id=employee_id, role="worker"))

    runtime_agents = _strict_runtime_document()["agents"]
    for role, identity in runtime_agents.items():
        employee = db_session.get(models.DigitalEmployee, identity["agent_id"])
        employee.status = "active"
        employee.runtime_type = "agentteams"
        employee.runtime_ref = identity["mxid"]
        db_session.add(models.EmployeePluginGrant(
            employee_id=employee.employee_no,
            plugin_id=f"obsolete.{role}.grant",
            decision_mode="allow",
        ))
    extra = db_session.get(models.DigitalEmployee, "RPA-0001")
    extra.status = "active"
    extra.runtime_type = "agentteams"
    extra.runtime_ref = "@extra-rpa:local"
    db_session.add(models.EmployeePluginGrant(
        employee_id=extra.employee_no, plugin_id="obsolete.rpa.grant", decision_mode="allow",
    ))
    db_session.flush()

    catalog = AgentTeamsCollaborationService()._team_capability_catalog(db_session, team.id)

    assert {item["agent_id"]: item["capabilities"] for item in catalog} == {
        "VE-0001": [],
        "VE-0002": ["hr.onboarding.coordinate"],
        "VE-0003": ["it.onboarding.coordinate"],
        "VE-0005": ["audit.evidence.read"],
        "VE-HR-MATERIAL": ["hr.materials.read"],
        "VE-HR-TRAINING": ["hr.training.read"],
        "VE-IT-ACCOUNT": ["it.accounts.read"],
        "VE-IT-DEVICE": ["it.devices.read"],
    }
    assert "RPA-0001" not in {item["agent_id"] for item in catalog}
    assert "private-" not in json.dumps(catalog)


def test_strict_team_catalog_fails_closed_when_runtime_is_invalid(db_session, monkeypatch, tmp_path):
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict

    monkeypatch.setattr(config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(tmp_path / "missing-runtime.json"))

    with pytest.raises(CollaborationConflict, match="invalid_runtime_config"):
        AgentTeamsCollaborationService()._team_capability_catalog(db_session, "TEAM-ONBOARD")


def test_delegate_rejects_parent_chain_assignee_without_partial_tree(db_session):
    """Removing ancestor validation would allow a worker to delegate work back to its manager."""
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("hr", "VE-0002")])
    hr = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, hr)

    with pytest.raises(CollaborationConflict, match="parent_chain_cycle"):
        _delegate(
            db_session,
            run,
            "VE-0002",
            db_session.get(models.DigitalEmployee, "VE-0002").runtime_ref,
            [_subtask("back-to-manager", "DT-E10281")],
            key="parent-chain",
            parent=hr,
        )

    assert hr.status == "running"
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=hr.id).count() == 0


def test_required_child_failure_marks_root_failed(db_session):
    """Removing failure propagation would allow a root summary to hide a required failure."""
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("required", "VE-0002")])
    child = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, child)

    _apply_node_message(
        db_session,
        run,
        child,
        type="task.failed",
        key="required-failed",
        payload={"error_code": "evidence_missing"},
    )

    root = db_session.get(models.AgentTaskNode, run.id)
    assert root.status == "failed"
    assert db_session.get(models.TaskRun, run.id).status == "failed"
    assert db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.summary", direction="outbound"
    ).count() == 0


def test_failed_dependency_cancels_direct_and_recursive_dependents(db_session):
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("a", "VE-0002"), _subtask("b", "VE-0003", depends_on=["a"]), _subtask("c", "RPA-0001", depends_on=["b"])])
    nodes = {n.client_task_key: n for n in db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id)}
    _start_node(db_session, run, nodes["a"])
    _apply_node_message(db_session, run, nodes["a"], type="task.failed", key="a-failed", payload={})
    assert [(nodes[key].status, nodes[key].error_code) for key in ("b", "c")] == [("cancelled", "dependency_failed"), ("cancelled", "dependency_failed")]


def test_optional_failed_dependency_cancels_required_dependent_and_fails_root(db_session):
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("optional", "VE-0002", required=False), _subtask("required", "VE-0003", depends_on=["optional"])])
    nodes = {n.client_task_key: n for n in db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id)}
    _start_node(db_session, run, nodes["optional"])
    _apply_node_message(db_session, run, nodes["optional"], type="task.failed", key="optional-failed", payload={})
    assert nodes["required"].status == "cancelled"
    assert db_session.get(models.TaskRun, run.id).status == "failed"


def test_initial_plus_three_follow_up_rounds_are_allowed(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("initial", "VE-0002")])
    root = db_session.get(models.AgentTaskNode, run.id)
    for round_number in range(2, 5):
        root.status = "summarizing"
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask(f"follow-{round_number}", "VE-0002")], key=f"follow-{round_number}")
        assert root.input_payload["coordination"]["round"] == round_number
    root.status = "summarizing"
    with pytest.raises(CollaborationConflict, match="max_rounds_exceeded"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("follow-5", "VE-0002")], key="follow-5")


def test_strict_delegate_requires_explicit_nonempty_capability(db_session, monkeypatch, tmp_path):
    from app.services.agentteams_collaboration import CollaborationConflict

    runtime_path = tmp_path / "strict-runtime.json"
    runtime_path.write_text(json.dumps(_strict_runtime_document(
        manager_agent_id="DT-E10281",
        manager_mxid="@dwp-twin-e10281:local",
    )), encoding="utf-8")
    monkeypatch.setattr(config, "agentteams_mode", lambda: "agentteams_strict")
    monkeypatch.setenv("DWP_AGENTTEAMS_RUNTIME_CONFIG", str(runtime_path))
    run, leader = _create_run(db_session)
    raw = _subtask("missing", "VE-0002")
    raw.pop("required_capability")
    raw["capability"] = "hr-employee-mcp"
    with pytest.raises(CollaborationConflict, match="required_capability_required"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [raw])
    raw["required_capability"] = ""
    with pytest.raises(CollaborationConflict, match="required_capability_required"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [raw], key="empty")


def test_delegate_rejects_all_optional_without_mutation(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict
    run, leader = _create_run(db_session)
    with pytest.raises(CollaborationConflict, match="required_subtask_required"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("optional", "VE-0002", required=False)])
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_safe_child_results_are_bounded_and_redacted(db_session):
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("one", "VE-0002")])
    node = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    node.output_payload = {"deep": {"url": "https://secret.example/token", "token": "no"}, "items": ["x" * 10000] * 20, "audit_ids": [7], "approval_ref": "AUD-RC-001"}
    safe = AgentTeamsCollaborationService()._safe_child_results(db_session, run.id)
    assert safe[0]["truncated"] is True
    assert safe[0]["result"]["audit_ids"] == [7]
    assert safe[0]["result"]["approval_ref"] == "AUD-RC-001"
    assert "token" not in str(safe)


def test_safe_child_results_keep_a_bounded_summary_from_result_payload(db_session):
    """Oversized worker results must not become an empty summary for the parent Agent."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("one", "VE-0002")])
    node = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    node.output_payload = {
        "result": "HR材料与培训证据已经返回。" + ("有效证据" * 400),
        "audit_ids": [17, 18],
    }

    safe = AgentTeamsCollaborationService()._safe_child_results(db_session, run.id)

    assert safe[0]["truncated"] is True
    assert safe[0]["result"]["summary"].startswith("HR材料与培训证据已经返回。")
    assert len(safe[0]["result"]["summary"]) <= 512
    assert safe[0]["result"]["audit_ids"] == [17, 18]


def test_semantic_duplicate_follow_up_is_rejected_with_a_new_client_key(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict
    run, leader = _create_run(db_session)
    first = _subtask("first", "VE-0002")
    first["instruction"] = "same evidence check"
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [first])
    root = db_session.get(models.AgentTaskNode, run.id)
    root.status = "summarizing"
    repeat = _subtask("different-key", "VE-0002")
    repeat["instruction"] = "  SAME   evidence CHECK  "
    with pytest.raises(CollaborationConflict, match="duplicate_semantic_subtask"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [repeat], key="semantic-repeat")


def test_delegate_rejects_unsafe_cross_agent_metadata_without_mutation(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    unsafe_key = _subtask("https://secret.example/task?token=top-secret", "VE-0002")
    with pytest.raises(CollaborationConflict, match="invalid_client_task_key"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [unsafe_key])

    unsafe_title = _subtask("safe-key", "VE-0002")
    unsafe_title["title"] = "https://secret.example/title"
    with pytest.raises(CollaborationConflict, match="unsafe_subtask_metadata"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [unsafe_title], key="unsafe-title")

    unsafe_capability = _subtask("safe-capability", "VE-0002")
    unsafe_capability["required_capability"] = "Bearer secret-token"
    with pytest.raises(CollaborationConflict, match="unsafe_subtask_metadata"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [unsafe_capability], key="unsafe-capability")

    root = db_session.get(models.AgentTaskNode, run.id)
    assert root.status == "planning"
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_safe_dependency_results_redact_historical_unsafe_client_key(db_session):
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [
        _subtask("predecessor", "VE-0002"),
        _subtask("dependent", "VE-0003", depends_on=["predecessor"]),
    ])
    nodes = {node.client_task_key: node for node in db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id)}
    leaked_key = "https://secret.example/task?token=top-secret"
    nodes["predecessor"].client_task_key = leaked_key
    db_session.flush()
    _start_node(db_session, run, nodes["predecessor"])
    _apply_node_message(db_session, run, nodes["predecessor"], type="task.result", key="done", payload={"result": "complete"})

    assignment = db_session.query(models.AgentMessage).filter_by(
        task_id=nodes["dependent"].id, message_type="task.assign",
    ).one()
    assert leaked_key not in json.dumps(assignment.payload)
    assert assignment.payload["dependency_results"][0]["client_task_key"] == "[redacted]"


def test_delegate_rejects_same_batch_semantic_duplicate(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    first = _subtask("first", "VE-0002")
    second = _subtask("second", "VE-0002")
    for item in (first, second):
        item["instruction"] = "Collect the same evidence"
        item["material"] = {"employee": "E-001", "scope": ["contract"]}
    with pytest.raises(CollaborationConflict, match="duplicate_semantic_subtask"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [first, second])
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0


def test_same_instruction_with_distinct_material_is_a_new_semantic_subtask(db_session):
    run, leader = _create_run(db_session)
    first = _subtask("first", "VE-0002")
    second = _subtask("second", "VE-0002")
    for item in (first, second):
        item["instruction"] = "Collect employee evidence"
    first["material"] = {"employee": "E-001", "scope": ["contract"]}
    second["material"] = {"employee": "E-002", "scope": ["contract"]}

    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [first, second])
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 2


def _too_deep_material():
    material = current = {}
    for index in range(12):
        next_value = {f"level-{index}": {}}
        current["child"] = next_value
        current = next_value[f"level-{index}"]
    return material


def _recursive_material():
    material = {}
    material["self"] = material
    return material


@pytest.mark.parametrize("material_factory", [
    _too_deep_material,
    lambda: {f"item-{index}": index for index in range(64)},
    lambda: {"text": "x" * 5000},
    lambda: {"parts": ["x" * 512 for _ in range(20)]},
    lambda: {"bad-key": object()},
    lambda: {"not-finite": float("nan")},
    lambda: {1: "non-string-key"},
    _recursive_material,
], ids=["deep", "many-items", "long-string", "byte-budget", "object", "nan", "non-string-key", "recursive"])
def test_delegate_rejects_unbounded_or_invalid_material_without_mutation(db_session, material_factory):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    before_events = db_session.query(models.AgentTaskEvent).count()
    before_outbox = db_session.query(models.AgentMessage).filter_by(direction="outbound").count()
    before_inbound = db_session.query(models.AgentMessage).filter_by(direction="inbound").count()
    subtask = _subtask("bounded-material", "VE-0002")
    subtask["material"] = material_factory()

    with pytest.raises(CollaborationConflict, match="invalid_material|material_too_large"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [subtask])

    root = db_session.get(models.AgentTaskNode, run.id)
    assert root.status == "planning"
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0
    assert db_session.query(models.AgentTaskEvent).count() == before_events
    assert db_session.query(models.AgentMessage).filter_by(direction="outbound").count() == before_outbox
    assert db_session.query(models.AgentMessage).filter_by(direction="inbound").count() == before_inbound


def test_wide_material_hits_global_budget_before_canonical_serialization(db_session, monkeypatch):
    from app.services import agentteams_collaboration as collaboration
    from app.services.agentteams_collaboration import CollaborationConflict

    # Every dict has 32 entries, every string is short, and depth is only three;
    # repeated references make the expanded traversal exceed the global budget.
    leaf = {f"leaf-{index}": "ok" for index in range(32)}
    middle = {f"middle-{index}": leaf for index in range(32)}
    wide_material = {f"root-{index}": middle for index in range(32)}
    run, leader = _create_run(db_session)
    before_events = db_session.query(models.AgentTaskEvent).count()
    before_outbox = db_session.query(models.AgentMessage).filter_by(direction="outbound").count()
    subtask = _subtask("wide-material", "VE-0002")
    subtask["material"] = wide_material

    def fail_if_full_canonicalization_is_reached(*args, **kwargs):
        raise AssertionError("material should exceed its traversal budget before json.dumps")

    monkeypatch.setattr(collaboration.json, "dumps", fail_if_full_canonicalization_is_reached)
    with pytest.raises(CollaborationConflict, match="material_too_large"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [subtask])

    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"
    assert db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 0
    assert db_session.query(models.AgentTaskEvent).count() == before_events
    assert db_session.query(models.AgentMessage).filter_by(direction="outbound").count() == before_outbox


def test_compact_json_below_byte_budget_is_not_rejected_by_shape(db_session):
    # 1 root dict + 32 lists + 256 integer leaves = 289 visited values, while
    # canonical JSON remains well below the 4 KiB material byte budget.
    material = {f"k{index}": list(range(8)) for index in range(32)}
    assert len(json.dumps(material, separators=(",", ":")).encode("utf-8")) < 4096
    run, leader = _create_run(db_session)
    subtask = _subtask("compact-material", "VE-0002")
    subtask["material"] = material

    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [subtask])

    child = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    assert child.input_payload == material


def test_locked_context_refresh_rejects_second_delegate_from_stale_session(db_session):
    from app.database import SessionLocal
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, CollaborationConflict

    run, leader = _create_run(db_session)
    matrix_sender = _matrix_sender(leader.runtime_ref)
    db_session.commit()
    stale = SessionLocal()
    current = SessionLocal()
    try:
        # Keep the pre-delegation state in this identity map.  The second call
        # must refresh the locked root before testing its transition/round state.
        assert stale.get(models.AgentTaskNode, run.id).status == "planning"
        service = AgentTeamsCollaborationService()
        first = _protocol(root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="platform", type="task.delegate", key=f"{run.id}:fresh", payload={"subtasks": [_subtask("first", "VE-0002")]})
        service.apply_message(current, matrix_sender=matrix_sender, message=first)
        current.commit()
        second = _protocol(root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="platform", type="task.delegate", key=f"{run.id}:stale-second", payload={"subtasks": [_subtask("second", "VE-0003")]})
        with pytest.raises(CollaborationConflict, match="invalid_transition"):
            service.apply_message(stale, matrix_sender=matrix_sender, message=second)
    finally:
        stale.close()
        current.close()


def test_two_prerequisites_release_a_blocked_child_once(db_session):
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [
        _subtask("a", "VE-0002"), _subtask("b", "VE-0003"),
        _subtask("c", "RPA-0001", depends_on=["a", "b"]),
    ])
    nodes = {n.client_task_key: n for n in db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id)}
    for key in ("a", "b"):
        _start_node(db_session, run, nodes[key])
        _apply_node_message(db_session, run, nodes[key], type="task.result", key=f"{key}-done", payload={"result": key})
    assert nodes["c"].status == "assigned"
    assert db_session.query(models.AgentMessage).filter_by(task_id=nodes["c"].id, message_type="task.assign").count() == 1


def test_duplicate_message_from_another_session_has_no_second_effect(db_session):
    from app.database import SessionLocal
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService
    run, leader = _create_run(db_session)
    message = _protocol(root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="platform", type="task.delegate", key=f"{run.id}:multi-session", payload={"subtasks": [_subtask("one", "VE-0002")]})
    service = AgentTeamsCollaborationService()
    service.apply_message(db_session, matrix_sender=_matrix_sender(leader.runtime_ref), message=message)
    db_session.commit()
    other = SessionLocal()
    try:
        assert service.apply_message(other, matrix_sender=_matrix_sender(leader.runtime_ref), message=message) == []
        assert other.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).count() == 1
    finally:
        other.close()


def test_task_summary_completes_root_and_task_run(db_session):
    from app.services.agentteams_collaboration import apply_message

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, policy)
    _apply_node_message(db_session, run, policy, type="task.result", key="result", payload={"result": "done"})
    root = db_session.get(models.AgentTaskNode, run.id)

    apply_message(
        db_session,
        matrix_sender=leader.runtime_ref,
        message=_protocol(
            root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="platform",
            type="task.summary", key=f"{run.id}:summary", payload={"summary": "全部完成"},
        ),
    )

    assert root.status == "completed"
    assert db_session.get(models.TaskRun, run.id).status == "completed"
    assert db_session.get(models.TaskRun, run.id).summary == "全部完成"


def test_follow_up_summary_requests_are_round_scoped_and_retry_idempotent(db_session):
    """A Manager can request a fresh summary after a follow-up delegation."""
    from app.services.agentteams_collaboration import AgentTeamsCollaborationService, apply_message

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("initial", "VE-0002")])
    initial = db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id, client_task_key="initial"
    ).one()
    _start_node(db_session, run, initial)
    _apply_node_message(db_session, run, initial, type="task.result", key="initial-result", payload={"result": "done"})

    root = db_session.get(models.AgentTaskNode, run.id)
    assert root.status == "summarizing"
    assert db_session.query(models.AgentMessage).filter_by(
        task_id=run.id,
        message_type="task.summary",
        direction="outbound",
    ).one().idempotency_key == f"{run.id}:task.summary.request:round-1"

    _delegate(
        db_session,
        run,
        "DT-E10281",
        leader.runtime_ref,
        [_subtask("follow-up", "VE-0003")],
        key="follow-up-delegate",
    )
    follow_up = db_session.query(models.AgentTaskNode).filter_by(
        parent_task_id=run.id, client_task_key="follow-up"
    ).one()
    _start_node(db_session, run, follow_up)
    _apply_node_message(db_session, run, follow_up, type="task.result", key="follow-up-result", payload={"result": "done"})

    summary_requests = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id,
        message_type="task.summary",
        direction="outbound",
    ).order_by(models.AgentMessage.created_at, models.AgentMessage.id).all()
    assert [item.idempotency_key for item in summary_requests] == [
        f"{run.id}:task.summary.request:round-1",
        f"{run.id}:task.summary.request:round-2",
    ]
    assert AgentTeamsCollaborationService()._maybe_request_parent_summary(
        db_session, follow_up, "VE-0003"
    ) == []
    assert db_session.query(models.AgentMessage).filter_by(
        task_id=run.id,
        message_type="task.summary",
        direction="outbound",
    ).count() == 2

    apply_message(
        db_session,
        matrix_sender=_matrix_sender(leader.runtime_ref),
        message=_protocol(
            root_id=run.id,
            task_id=run.id,
            sender="DT-E10281",
            receiver="platform",
            type="task.summary",
            key=f"{run.id}:final-summary",
            payload={"summary": "全部完成"},
        ),
    )

    assert root.status == "completed"
    assert db_session.get(models.TaskRun, run.id).status == "completed"


def test_plain_agent_message_does_not_change_status(db_session):
    from app.services.agentteams_collaboration import apply_message

    run, leader = _create_run(db_session)
    root = db_session.get(models.AgentTaskNode, run.id)
    before_events = db_session.query(models.AgentTaskEvent).count()

    apply_message(
        db_session,
        matrix_sender=leader.runtime_ref,
        message=_protocol(
            root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="VE-0002",
            type="agent.message", key=f"{run.id}:message", payload={"text": "收到，开始处理"},
        ),
    )

    assert root.status == "planning"
    assert db_session.get(models.TaskRun, run.id).status == "planning"
    assert db_session.query(models.AgentTaskEvent).count() == before_events + 1


def test_build_graph_and_result_are_serializable(db_session):
    from app.services.agentteams_collaboration import build_graph, build_result

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])

    graph = build_graph(db_session, run.id)
    result = build_result(db_session, run.id)

    assert graph["parent_edges"] == [{"parent_task_id": run.id, "task_id": graph["nodes"][1]["id"]}]
    assert result["status"] == "waiting_children"
    json.dumps({"graph": graph, "result": result})


def test_inbound_idempotency_key_cannot_block_summary_outbox(db_session):
    from app.services.agentteams_collaboration import apply_message

    run, leader = _create_run(db_session)
    apply_message(
        db_session,
        matrix_sender=leader.runtime_ref,
        message=_protocol(
            root_id=run.id, task_id=run.id, sender="DT-E10281", receiver="VE-0002",
            type="agent.message", key=f"{run.id}:task.summary.request", payload={"text": "collision"},
        ),
    )
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, policy)

    _apply_node_message(db_session, run, policy, type="task.result", key="result", payload={"result": "done"})

    summary_outbox = db_session.query(models.AgentMessage).filter_by(
        idempotency_key=f"{run.id}:task.summary.request:round-1", direction="outbound",
    ).one()
    assert summary_outbox.message_type == "task.summary"


def test_duplicate_runtime_ref_rejects_borrowed_root_delegation(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict

    run, leader = _create_run(db_session)
    worker = db_session.get(models.DigitalEmployee, "VE-0002")
    worker.runtime_ref = leader.runtime_ref
    db_session.flush()

    with pytest.raises(CollaborationConflict, match="matrix_sender_ambiguous"):
        _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])

    assert db_session.get(models.AgentTaskNode, run.id).status == "planning"


def test_worker_name_runtime_ref_accepts_only_configured_matrix_domain(db_session):
    from app.services.agentteams_collaboration import CollaborationConflict, apply_message

    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    worker = db_session.get(models.DigitalEmployee, "VE-0002")

    apply_message(
        db_session,
        matrix_sender=_matrix_sender(worker.runtime_ref),
        message=_protocol(
            root_id=run.id, task_id=policy.id, parent_id=run.id,
            sender="VE-0002", receiver="platform", type="task.accepted",
            key=f"{run.id}:worker-mxid-accepted", payload={},
        ),
    )

    with pytest.raises(CollaborationConflict, match="matrix_sender_mismatch"):
        apply_message(
            db_session,
            matrix_sender=f"@{worker.runtime_ref}:evil.example",
            message=_protocol(
                root_id=run.id, task_id=policy.id, parent_id=run.id,
                sender="VE-0002", receiver="platform", type="task.progress",
                key=f"{run.id}:wrong-domain", payload={"progress": 30},
            ),
        )

    assert policy.status == "accepted"


def test_assignment_payload_carries_instruction_and_complete_parent_chain(db_session):
    """Workers must receive platform-owned instruction and ancestry, not infer them from a title."""
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    child = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    assignment = db_session.query(models.AgentMessage).filter_by(
        task_id=child.id, message_type="task.assign", direction="outbound",
    ).one()

    assert assignment.payload["instruction"] == "complete policy"
    assert assignment.payload["parent_chain_agent_ids"] == ["DT-E10281"]
    # Each platform-owned outbound is a freshly persisted root snapshot, not a
    # static config catalog.  The root submit owns sequence 1; assignment is 2.
    root_submit = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.submit", direction="outbound",
    ).one()
    assert root_submit.payload["team_catalog_version"] == 1
    assert assignment.payload["team_catalog_version"] == 2
    assert assignment.payload["team_capability_catalog"]

    from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
    from app.services.agentteams_protocol import AgentProtocolMessage

    runtime = AgentRuntime(
        AgentRuntimeConfig(
            agent_id="VE-0002", role="hr", matrix_mxid="@hr:test", matrix_token="worker-only",
            room_id="!room:test", trusted_sender_mxids={"platform": "@platform:test", "DT-E10281": "@leader:test", "VE-0002": "@hr:test"},
            allowed_skill_ids={"hr-onboarding-assistant"}, state_dir=Path("tmp") / "runtime-contract",
        ),
        gateway=None, decisions=None,
    )
    context = runtime._new_worker_context(
        AgentProtocolMessage(
            version="1.0", message_id="assignment", type="task.assign", root_task_id=run.id, task_id=child.id,
            parent_task_id=run.id, trace_id=run.trace_id, sender="DT-E10281", receiver="VE-0002",
            timestamp="2026-09-04T10:00:00+08:00", idempotency_key="assignment", payload=dict(assignment.payload),
        ),
        ["hr-onboarding-assistant"],
    )
    assert context["task"]["goal"] == "complete policy"
