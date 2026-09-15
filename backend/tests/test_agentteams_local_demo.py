import pytest

from app.services import config
from app.services.agentteams_local_demo import LocalDemoTransport
from app.services.agentteams_protocol import (
    AgentProtocolMessage,
    parse_protocol_lines,
    render_protocol_line,
)
from app.services.agentteams_role_change_scenario import (
    ROLE_CHANGE_FIRST_ROUND_CAPABILITIES,
    ROLE_CHANGE_SECOND_ROUND_CAPABILITIES,
    ROLE_CHANGE_SKILL_ID,
)


ROOM_ID = "local-demo"
ROOT_ID = "AT-ROOT-1"
TRACE_ID = "TRACE-1"
TIMESTAMP = "2026-09-02T10:00:00+08:00"


def _message(
    message_type: str,
    *,
    task_id: str = ROOT_ID,
    parent_task_id: str | None = None,
    sender: str = "platform",
    receiver: str = "VE-0001",
    key: str | None = None,
    payload: dict | None = None,
) -> AgentProtocolMessage:
    return AgentProtocolMessage(
        version="1.0",
        message_id=f"MSG-{message_type}-{task_id}",
        type=message_type,
        root_task_id=ROOT_ID,
        task_id=task_id,
        parent_task_id=parent_task_id,
        trace_id=TRACE_ID,
        sender=sender,
        receiver=receiver,
        timestamp=TIMESTAMP,
        idempotency_key=key or f"{task_id}:{message_type}",
        payload=payload or {},
    )


def _send(transport: LocalDemoTransport, message: AgentProtocolMessage) -> str:
    return transport.send_protocol_message(
        ROOM_ID,
        message.type,
        render_protocol_line(message),
    )


def _messages(batch) -> list[AgentProtocolMessage]:
    return [parse_protocol_lines(event.body)[0] for event in batch.events]


def test_submit_enqueues_manager_delegate_with_preserved_context(monkeypatch):
    monkeypatch.setenv(config.AGENTTEAMS_MATRIX_DOMAIN, "demo.test")
    transport = LocalDemoTransport()

    event_id = _send(transport, _message("task.submit", payload={"material": {"name": "李四"}}))
    batch = transport.sync_messages(None, {ROOM_ID})
    messages = _messages(batch)

    assert event_id.startswith("local-out-")
    assert [message.type for message in messages] == ["agent.message", "task.delegate"]
    assert batch.next_cursor.startswith("local-")
    assert batch.next_cursor.endswith("-2")
    assert batch.events[0].event_id.startswith("local-in-")
    assert batch.events[0].sender == "@dwp-ve-0001:demo.test"
    assert (
        messages[-1].root_task_id,
        messages[-1].task_id,
        messages[-1].parent_task_id,
        messages[-1].trace_id,
    ) == (ROOT_ID, ROOT_ID, None, TRACE_ID)
    assert messages[-1].sender == "VE-0001"
    assert messages[-1].receiver == "platform"
    assert [item["client_task_key"] for item in messages[-1].payload["subtasks"]] == [
        "onboarding-hr-department",
        "onboarding-it-department",
    ]
    assert [item["assignee_agent_id"] for item in messages[-1].payload["subtasks"]] == [
        "VE-0002",
        "VE-0003",
    ]


def test_submit_emits_shared_role_change_skill_trace_and_first_round():
    transport = LocalDemoTransport()

    _send(transport, _message("task.submit", payload={
        "title": "岗位调整评估",
        "instruction": "评估张明的岗位调整是否可以按期完成。",
    }))
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == [
        "agent.message",
        "agent.message",
        "task.delegate",
    ]
    assert messages[0].payload == {
        "kind": "skill.selected",
        "selected_skill_ids": [ROLE_CHANGE_SKILL_ID],
    }
    assert messages[1].payload["kind"] == "decision.trace"
    assert messages[1].payload["reasoning_segments"]
    assert messages[2].payload["round"] == 1
    assert {
        item["capability"] for item in messages[2].payload["subtasks"]
    } == ROLE_CHANGE_FIRST_ROUND_CAPABILITIES


def test_root_summary_after_round_one_delegates_a_second_manager_round():
    transport = LocalDemoTransport()
    request = _message(
        "task.summary",
        payload={
            "request": "task.summary",
            "current_round": 1,
            "child_results": [
                {"client_task_key": "role-change-hr", "result": "HR证据已返回"},
                {"client_task_key": "role-change-it-plan", "result": "IT证据已返回"},
            ],
        },
    )

    _send(transport, request)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == ["agent.message", "task.delegate"]
    assert messages[0].payload["kind"] == "decision.trace"
    assert messages[0].payload["action"] == "delegate"
    assert messages[1].payload["round"] == 2
    assert {
        item["capability"] for item in messages[1].payload["subtasks"]
    } == ROLE_CHANGE_SECOND_ROUND_CAPABILITIES
    by_capability = {
        item["capability"]: item for item in messages[1].payload["subtasks"]
    }
    assert by_capability["audit.role_change.coordinate"]["depends_on"] == []
    assert by_capability["it.role_change.activate.coordinate"]["depends_on"] == [
        by_capability["audit.role_change.coordinate"]["client_task_key"]
    ]


def test_retried_outbound_is_idempotent_and_cursor_does_not_replay():
    transport = LocalDemoTransport()
    submit = _message("task.submit")

    first_event_id = _send(transport, submit)
    second_event_id = _send(transport, submit)
    first_batch = transport.sync_messages(None, {ROOM_ID})
    replay_batch = transport.sync_messages(first_batch.next_cursor, {ROOM_ID})

    assert first_event_id == second_event_id
    assert first_event_id.startswith("local-out-")
    assert len(first_batch.events) == 2
    assert replay_batch.events == []
    assert replay_batch.next_cursor == first_batch.next_cursor


def test_new_transport_does_not_skip_new_task_behind_old_cursor_or_reuse_event_ids():
    first = LocalDemoTransport()
    first_submit = _message("task.submit", key="first-root:task.submit")
    first_outbound_id = _send(first, first_submit)
    first_batch = first.sync_messages(None, {ROOM_ID})

    restarted = LocalDemoTransport()
    second_submit = _message(
        "task.submit",
        task_id="AT-ROOT-2",
        key="second-root:task.submit",
    ).model_copy(
        update={
            "root_task_id": "AT-ROOT-2",
            "trace_id": "TRACE-2",
        }
    )
    second_outbound_id = _send(restarted, second_submit)
    second_batch = restarted.sync_messages(first_batch.next_cursor, {ROOM_ID})

    assert len(second_batch.events) == 2
    assert second_outbound_id != first_outbound_id
    assert second_batch.events[0].event_id != first_batch.events[0].event_id


def test_restarted_transport_reconstructs_stable_events_for_same_outbound():
    submit = _message("task.submit", key="restart-safe:task.submit")
    first = LocalDemoTransport()
    first_outbound_id = _send(first, submit)
    first_batch = first.sync_messages(None, {ROOM_ID})

    restarted = LocalDemoTransport()
    restarted_outbound_id = _send(restarted, submit)
    restarted_batch = restarted.sync_messages(None, {ROOM_ID})

    assert restarted_outbound_id == first_outbound_id
    assert restarted_batch.events[0].event_id == first_batch.events[0].event_id
    assert _messages(restarted_batch) == _messages(first_batch)


def test_hr_leader_assignment_delegates_only_to_its_two_workers(monkeypatch):
    monkeypatch.setenv(config.AGENTTEAMS_MATRIX_DOMAIN, "demo.test")
    transport = LocalDemoTransport()
    assignment = _message(
        "task.assign",
        task_id="AT-HR",
        parent_task_id=ROOT_ID,
        sender="VE-0001",
        receiver="VE-0002",
        payload={"client_task_key": "onboarding-hr-department"},
    )

    _send(transport, assignment)
    batch = transport.sync_messages(None, {ROOM_ID})
    messages = _messages(batch)

    assert [message.type for message in messages] == [
        "task.accepted",
        "task.progress",
        "agent.message",
        "task.delegate",
    ]
    assert len({event.event_id for event in batch.events}) == 4
    assert all(event.event_id.startswith("local-in-") for event in batch.events)
    assert batch.next_cursor.startswith("local-")
    assert batch.next_cursor.endswith("-4")
    assert {event.sender for event in batch.events} == {"@dwp-ve-0002:demo.test"}
    assert all(
        (
            message.root_task_id,
            message.task_id,
            message.parent_task_id,
            message.trace_id,
        )
        == (ROOT_ID, "AT-HR", ROOT_ID, TRACE_ID)
        for message in messages
    )
    assert messages[2].payload["kind"] == "decision.trace"
    assert {
        item["assignee_agent_id"] for item in messages[3].payload["subtasks"]
    } == {"VE-HR-MATERIAL", "VE-HR-TRAINING"}


def test_each_generated_response_has_unique_protocol_identity():
    transport = LocalDemoTransport()
    assignment = _message(
        "task.assign",
        task_id="AT-HR",
        parent_task_id=ROOT_ID,
        sender="VE-0001",
        receiver="VE-0002",
        payload={"client_task_key": "onboarding-hr-department"},
    )

    _send(transport, assignment)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert len({message.message_id for message in messages}) == len(messages)
    assert len({message.idempotency_key for message in messages}) == len(messages)


def test_it_worker_assignment_requests_gateway_tool_without_delegating():
    transport = LocalDemoTransport()
    assignment = _message(
        "task.assign",
        task_id="AT-IT",
        parent_task_id="AT-IT-LEADER",
        sender="VE-0003",
        receiver="VE-IT-ACCOUNT",
        payload={"client_task_key": "onboarding-it-accounts"},
    )

    _send(transport, assignment)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == [
        "task.accepted",
        "task.progress",
        "agent.message",
        "agent.message",
        "tool.request",
    ]
    assert messages[-1].receiver == "platform"
    assert messages[-1].payload == {
        "tool_name": "agentteams.it.lookup_accounts",
        "plugin_id": "agentteams-it-accounts",
        "action": "execute",
        "required_capability": "it.accounts.read",
        "params": {"employee_ref": "新员工A"},
    }


def test_audit_leader_assignment_delegates_to_audit_worker():
    transport = LocalDemoTransport()
    assignment = _message(
        "task.assign",
        task_id="AT-AUDIT",
        parent_task_id=ROOT_ID,
        sender="VE-0001",
        receiver="VE-0005",
        payload={"client_task_key": "onboarding-audit-review"},
    )

    _send(transport, assignment)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == [
        "task.accepted",
        "task.progress",
        "agent.message",
        "task.delegate",
    ]
    assert messages[-1].payload["subtasks"][0]["assignee_agent_id"] == "VE-AUDIT-REVIEW"


def test_tool_result_completes_it_account_task():
    transport = LocalDemoTransport()
    assignment = _message(
        "task.assign", task_id="AT-IT", parent_task_id="AT-IT-LEADER",
        sender="VE-0003", receiver="VE-IT-ACCOUNT",
        payload={"client_task_key": "onboarding-it-accounts"},
    )
    _send(transport, assignment)
    cursor = transport.sync_messages(None, {ROOM_ID}).next_cursor
    tool_result = _message(
        "tool.result",
        task_id="AT-IT",
        parent_task_id="AT-IT-LEADER",
        receiver="VE-IT-ACCOUNT",
        payload={"ok": True, "decision": "allow", "audit_ids": ["AUD-1"]},
    )

    _send(transport, tool_result)
    messages = _messages(transport.sync_messages(cursor, {ROOM_ID}))

    assert [message.type for message in messages] == ["agent.message", "task.result"]
    assert messages[-1].sender == "VE-IT-ACCOUNT"
    assert messages[-1].payload["audit_ids"] == ["AUD-1"]


def test_submitted_result_is_accepted_by_the_receiving_coordinator():
    transport = LocalDemoTransport()
    review_request = _message(
        "task.result.submitted",
        task_id="AT-WORKER",
        parent_task_id="AT-LEADER",
        receiver="VE-0002",
        payload={
            "title": "核查材料",
            "assignee_agent_id": "VE-0003",
            "result": {"result": "材料已核查"},
        },
    )

    _send(transport, review_request)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == ["task.result.accepted"]
    assert messages[0].sender == "VE-0002"
    assert messages[0].receiver == "platform"
    assert messages[0].payload["feedback"] == "本地演示结果验收通过"


@pytest.mark.parametrize(
    ("task_id", "parent_task_id", "receiver", "expected_sender", "expected_type", "phrases"),
    [
        ("AT-HR", ROOT_ID, "VE-0001", "VE-0001", "task.result", ("材料", "IT")),
        (ROOT_ID, None, "VE-0001", "VE-0001", "task.summary", ("已完成事项", "风险", "下一步")),
    ],
)
def test_summary_request_returns_deterministic_chinese_summary(
    task_id, parent_task_id, receiver, expected_sender, expected_type, phrases
):
    transport = LocalDemoTransport()
    request = _message(
        "task.summary",
        task_id=task_id,
        parent_task_id=parent_task_id,
        receiver=receiver,
        payload={"request": "task.summary"},
    )

    _send(transport, request)
    messages = _messages(transport.sync_messages(None, {ROOM_ID}))

    assert [message.type for message in messages] == [expected_type]
    assert messages[0].sender == expected_sender
    assert all(phrase in messages[0].payload["summary"] for phrase in phrases)


def test_malformed_protocol_fails_without_advancing_state():
    transport = LocalDemoTransport()

    with pytest.raises(ValueError, match="invalid_local_demo_protocol"):
        transport.send_protocol_message(ROOM_ID, "bad", "DWP_EVENT {not-json}")

    submit = _message("task.submit")
    assert _send(transport, submit) == _send(LocalDemoTransport(), submit)
    batch = transport.sync_messages(None, {ROOM_ID})
    assert len(batch.events) == 2
    assert batch.next_cursor.endswith("-2")


def test_unknown_outbound_type_has_explicit_no_response_and_empty_cursor_is_stable():
    transport = LocalDemoTransport()

    outbound_id = _send(
        transport,
        _message("task.progress", sender="VE-0002", receiver="platform"),
    )
    first_batch = transport.sync_messages(None, {ROOM_ID})
    second_batch = transport.sync_messages(first_batch.next_cursor, {ROOM_ID})

    assert first_batch.events == []
    assert outbound_id.startswith("local-out-")
    assert first_batch.next_cursor.endswith("-0")
    assert second_batch.events == []
    assert second_batch.next_cursor == first_batch.next_cursor


def test_cancel_stops_later_work_for_the_same_root():
    transport = LocalDemoTransport()
    cancel = _message("task.cancel", receiver="VE-0001")
    assignment = _message(
        "task.assign",
        task_id="AT-HR",
        parent_task_id=ROOT_ID,
        sender="VE-0001",
        receiver="VE-0002",
        payload={"client_task_key": "hr-policy"},
    )

    _send(transport, cancel)
    _send(transport, assignment)
    batch = transport.sync_messages(None, {ROOM_ID})

    assert batch.events == []
    assert batch.next_cursor.endswith("-0")
