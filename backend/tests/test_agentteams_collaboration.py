from app import models


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
