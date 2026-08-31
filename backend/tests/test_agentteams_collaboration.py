import json

import pytest

from app import models
from app.services.agentteams_protocol import AgentProtocolMessage


def _enable_test_leader(db_session):
    """Seed twins need a Matrix reference and explicit team membership for protocol tests."""
    leader = db_session.get(models.DigitalEmployee, "DT-E10281")
    leader.runtime_ref = "@dwp-twin-e10281:local"
    db_session.add(models.TeamMember(
        team_id="TEAM-ONBOARD", employee_id=leader.employee_no, role="member",
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


def _create_run(db_session):
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
        ),
        leader,
    )


def _delegate(db_session, run, sender, matrix_sender, subtasks, key="delegate-1", parent=None):
    from app.services.agentteams_collaboration import apply_message

    return apply_message(
        db_session,
        matrix_sender=matrix_sender,
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
    return {
        "client_task_key": key,
        "title": f"{key} task",
        "instruction": f"complete {key}",
        "assignee_agent_id": assignee,
        "depends_on": depends_on or [],
        "required": required,
    }


def _apply_node_message(db_session, run, node, *, type, key, payload):
    from app.services.agentteams_collaboration import apply_message

    employee = db_session.get(models.DigitalEmployee, node.assignee_agent_id)
    return apply_message(
        db_session,
        matrix_sender=employee.runtime_ref,
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
    )

    root = db_session.get(models.AgentTaskNode, run.id)
    outbox = db_session.query(models.AgentMessage).filter_by(task_id=run.id).all()
    assert root.status == "planning"
    assert len(outbox) == 1
    assert outbox[0].message_type == "task.submit"
    assert outbox[0].delivery_status == "pending"


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
                    "depends_on": [],
                },
                {
                    "client_task_key": "account", "title": "账号开通",
                    "instruction": "开通账号", "assignee_agent_id": "VE-0003",
                    "depends_on": ["policy"],
                },
            ],
        },
    )

    apply_message(db_session, matrix_sender=leader.runtime_ref, message=message)

    children = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).all()
    assert {child.client_task_key for child in children} == {"policy", "account"}
    assert db_session.get(models.AgentTaskNode, run.id).status == "waiting_children"


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
    run, leader = _create_run(db_session)
    _delegate(db_session, run, "DT-E10281", leader.runtime_ref, [_subtask("policy", "VE-0002")])
    policy = db_session.query(models.AgentTaskNode).filter_by(parent_task_id=run.id).one()
    _start_node(db_session, run, policy)

    _apply_node_message(
        db_session, run, policy, type="task.result", key="result", payload={"result": "制度已核查"},
    )

    root = db_session.get(models.AgentTaskNode, run.id)
    summary_request = db_session.query(models.AgentMessage).filter_by(
        task_id=run.id, message_type="task.summary", direction="outbound",
    ).one()
    assert root.status == "summarizing"
    assert summary_request.receiver_agent_id == "DT-E10281"
    assert summary_request.delivery_status == "pending"


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
