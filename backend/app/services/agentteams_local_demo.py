"""Deterministic in-process AgentTeams-compatible transport for local demos.

This module speaks only the existing ``DWP_EVENT`` protocol.  It has no
database dependency and performs no Matrix or other network operations.
"""

from __future__ import annotations

import hashlib
from uuid import uuid4

from . import config
from .agentteams_gateway import MatrixEvent, MatrixSyncBatch
from .agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line


class LocalDemoTransport:
    """Stateful deterministic scenario engine behind the transport boundary."""

    mode = "local_demo"

    def __init__(self) -> None:
        self._outbound_ids: dict[str, str] = {}
        self._events: list[MatrixEvent] = []
        self._cancelled_roots: set[str] = set()
        self._cursor_generation = uuid4().hex

    def send_protocol_message(
        self,
        room_id: str,
        text: str,
        protocol_line: str,
    ) -> str:
        del text
        try:
            messages = parse_protocol_lines(protocol_line)
        except Exception as exc:
            raise ValueError("invalid_local_demo_protocol") from exc
        if len(messages) != 1:
            raise ValueError("invalid_local_demo_protocol")

        message = messages[0]
        prior_event_id = self._outbound_ids.get(message.idempotency_key)
        if prior_event_id is not None:
            return prior_event_id

        outbound_event_id = f"local-out-{self._stable_id(message.idempotency_key)}"
        self._outbound_ids[message.idempotency_key] = outbound_event_id

        if message.type == "task.cancel":
            self._cancelled_roots.add(message.root_task_id)
            return outbound_event_id
        if message.root_task_id in self._cancelled_roots:
            return outbound_event_id

        for response in self._responses(message):
            self._enqueue(room_id, response)
        return outbound_event_id

    def sync_messages(
        self,
        since: str | None,
        room_ids: set[str],
    ) -> MatrixSyncBatch:
        cursor = self._parse_cursor(since)
        events = [
            event
            for index, event in enumerate(self._events, start=1)
            if index > cursor and event.room_id in room_ids
        ]
        return MatrixSyncBatch(
            events=events,
            next_cursor=f"local-{self._cursor_generation}-{len(self._events)}",
        )

    def close(self) -> None:
        return None

    def _parse_cursor(self, cursor: str | None) -> int:
        if cursor is None:
            return 0
        prefix, separator, number = cursor.rpartition("-")
        expected_prefix = f"local-{self._cursor_generation}"
        if not separator or not number.isdigit() or not prefix.startswith("local-"):
            raise ValueError("invalid_local_demo_cursor")
        if prefix != expected_prefix:
            return 0
        return int(number)

    def _enqueue(self, room_id: str, message: AgentProtocolMessage) -> None:
        sequence = len(self._events) + 1
        self._events.append(
            MatrixEvent(
                event_id=f"local-in-{self._stable_id(message.idempotency_key)}",
                room_id=room_id,
                sender=self._matrix_sender(message.sender),
                body=render_protocol_line(message),
                ts=sequence,
            )
        )

    @staticmethod
    def _stable_id(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    @staticmethod
    def _matrix_sender(agent_id: str) -> str:
        worker = f"dwp-{agent_id.lower()}"
        return f"@{worker}:{config.agentteams_matrix_domain()}"

    def _responses(self, message: AgentProtocolMessage) -> list[AgentProtocolMessage]:
        if message.type == "task.submit":
            return [
                self._response(
                    message,
                    "task.delegate",
                    response_number=1,
                    sender="VE-0001",
                    payload={
                        "subtasks": [
                            {
                                "client_task_key": "hr-policy",
                                "title": "核查入职制度与材料",
                                "instruction": "核查新员工入职制度和材料完整性",
                                "assignee_agent_id": "VE-0002",
                                "capability": "hr.policy.read",
                                "depends_on": [],
                                "material": dict(message.payload.get("material") or {}),
                                "required": True,
                            },
                            {
                                "client_task_key": "it-account",
                                "title": "准备 IT 账号",
                                "instruction": "查询工作记录并准备 IT 账号开通信息",
                                "assignee_agent_id": "VE-0003",
                                "capability": "it.account.prepare",
                                "depends_on": [],
                                "material": dict(message.payload.get("material") or {}),
                                "required": True,
                            },
                        ]
                    },
                )
            ]

        if message.type == "task.assign":
            client_task_key = message.payload.get("client_task_key")
            if client_task_key == "hr-policy":
                return [
                    self._response(
                        message,
                        "task.accepted",
                        response_number=1,
                        sender="VE-0002",
                    ),
                    self._response(
                        message,
                        "task.progress",
                        response_number=2,
                        sender="VE-0002",
                        payload={"progress": 40},
                    ),
                    self._response(
                        message,
                        "agent.message",
                        response_number=3,
                        sender="VE-0002",
                        receiver="VE-0003",
                        payload={"text": "请协助确认入职账号与设备准备情况。"},
                    ),
                    self._response(
                        message,
                        "task.delegate",
                        response_number=4,
                        sender="VE-0002",
                        payload={
                            "subtasks": [
                                {
                                    "client_task_key": "hr-it-check",
                                    "title": "复核 HR 与 IT 准备项",
                                    "instruction": "确认入职材料对应的 IT 准备项",
                                    "assignee_agent_id": "VE-0003",
                                    "capability": "it.onboarding.check",
                                    "depends_on": [],
                                    "material": {},
                                    "required": True,
                                }
                            ]
                        },
                    ),
                ]
            if client_task_key == "it-account":
                return [
                    self._response(
                        message,
                        "task.accepted",
                        response_number=1,
                        sender="VE-0003",
                    ),
                    self._response(
                        message,
                        "task.progress",
                        response_number=2,
                        sender="VE-0003",
                        payload={"progress": 60},
                    ),
                    self._response(
                        message,
                        "tool.request",
                        response_number=3,
                        sender="VE-0003",
                        payload={
                            "plugin_id": "adp-onboarding",
                            "action": "execute",
                            "params": {"employee_name": "李四"},
                        },
                    ),
                ]
            if client_task_key == "hr-it-check":
                return [
                    self._response(
                        message,
                        "task.accepted",
                        response_number=1,
                        sender="VE-0003",
                    ),
                    self._response(
                        message,
                        "task.progress",
                        response_number=2,
                        sender="VE-0003",
                        payload={"progress": 80},
                    ),
                    self._response(
                        message,
                        "task.result",
                        response_number=3,
                        sender="VE-0003",
                        payload={"summary": "IT 账号与设备准备项已完成复核。"},
                    ),
                ]
            return []

        if message.type == "tool.result":
            return [
                self._response(
                    message,
                    "task.result",
                    response_number=1,
                    sender="VE-0003",
                    payload={
                        "summary": "IT 账号准备信息已核查。",
                        "decision": message.payload.get("decision", "failed"),
                        "audit_ids": list(message.payload.get("audit_ids") or []),
                    },
                )
            ]

        if message.type == "task.summary":
            if message.task_id == message.root_task_id:
                summary = (
                    "已完成事项：入职材料、制度和 IT 账号准备均已核查；"
                    "风险：正式开通前仍需确认审批状态；"
                    "下一步：由负责人复核后执行账号开通。"
                )
                response_type = "task.summary"
            else:
                summary = "材料检查已完成，并已纳入 IT 澄清与复核结果。"
                # A non-root parent is owned by its assignee.  The actual
                # Worker Runtime returns its completed result to platform;
                # platform then requests the root Manager's final summary.
                # Keep this offline transport compatible with that state
                # machine instead of impersonating the parent coordinator.
                response_type = "task.result"
            return [
                self._response(
                    message,
                    response_type,
                    response_number=1,
                    sender=message.receiver,
                    payload={"summary": summary},
                )
            ]

        return []

    def _response(
        self,
        request: AgentProtocolMessage,
        message_type: str,
        *,
        response_number: int,
        sender: str,
        receiver: str = "platform",
        payload: dict | None = None,
    ) -> AgentProtocolMessage:
        request_id = self._stable_id(request.idempotency_key)
        return AgentProtocolMessage(
            version="1.0",
            message_id=f"LOCAL-MSG-{request_id}-{response_number}",
            type=message_type,
            root_task_id=request.root_task_id,
            task_id=request.task_id,
            parent_task_id=request.parent_task_id,
            trace_id=request.trace_id,
            sender=sender,
            receiver=receiver,
            timestamp=request.timestamp,
            idempotency_key=(
                f"local:{request.idempotency_key}:{response_number}:{message_type}"
            ),
            payload=payload or {},
        )
