import pytest
from pydantic import ValidationError

from app.services.agentteams_protocol import (
    AgentProtocolMessage,
    parse_protocol_lines,
    render_protocol_line,
)


def test_round_trip_dwp_event():
    message = AgentProtocolMessage(
        version="1.0",
        message_id="MSG-1",
        type="task.progress",
        root_task_id="TASK-1",
        task_id="TASK-2",
        parent_task_id="TASK-1",
        trace_id="TRACE-1",
        sender="worker-research",
        receiver="manager",
        timestamp="2026-08-31T15:00:00+08:00",
        idempotency_key="TASK-2:task.progress:1",
        payload={"progress": 60, "summary": "资料整理完成"},
    )
    assert parse_protocol_lines(render_protocol_line(message)) == [message]


def test_plain_text_is_not_a_state_event():
    assert parse_protocol_lines("我已经完成任务") == []


@pytest.mark.parametrize(
    "message_type",
    ["task.result.submitted", "task.result.accepted", "task.result.rejected"],
)
def test_result_review_message_types_are_part_of_the_protocol(message_type):
    message = AgentProtocolMessage(
        version="1.0",
        message_id=f"MSG-{message_type}",
        type=message_type,
        root_task_id="TASK-1",
        task_id="TASK-2",
        parent_task_id="TASK-1",
        trace_id="TRACE-1",
        sender="leader",
        receiver="platform",
        timestamp="2026-09-14T12:00:00+08:00",
        idempotency_key=f"TASK-2:{message_type}:1",
        payload={},
    )

    assert parse_protocol_lines(render_protocol_line(message)) == [message]


def test_rejects_unknown_message_type():
    with pytest.raises(ValidationError):
        AgentProtocolMessage(
            version="1.0", message_id="MSG-2", type="task.magic",
            root_task_id="TASK-1", task_id="TASK-1", trace_id="TRACE-1",
            sender="worker", receiver="manager",
            timestamp="2026-08-31T15:00:00+08:00",
            idempotency_key="TASK-1:task.magic:1", payload={},
        )
