"""Deterministic in-process AgentTeams-compatible transport for local demos.

This module speaks only the existing ``DWP_EVENT`` protocol.  It has no
database dependency and performs no Matrix or other network operations.
"""

from __future__ import annotations

import hashlib
from typing import Any, Mapping
from uuid import uuid4

from . import config
from .agentteams_gateway import MatrixEvent, MatrixSyncBatch
from .agentteams_protocol import AgentProtocolMessage, parse_protocol_lines, render_protocol_line
from .agentteams_role_change_scenario import (
    ROLE_CHANGE_APPROVAL_REF,
    ROLE_CHANGE_EMPLOYEE_REF,
    ROLE_CHANGE_FIRST_ROUND_CAPABILITIES,
    ROLE_CHANGE_INSTRUCTION,
    ROLE_CHANGE_SECOND_ROUND_CAPABILITIES,
    ROLE_CHANGE_SKILL_ID,
    ROLE_CHANGE_TITLE,
)


_ROLE_CHANGE_TASKS: dict[str, dict[str, Any]] = {
    "role-change-hr": {
        "title": "核查岗位调整审批与材料",
        "instruction": "核查张明的岗位调整审批、目标岗位和生效日期。",
        "assignee_agent_id": "VE-0002",
        "required_capability": "hr.role_change.read",
        "skill_id": "hr-role-change-assistant",
        "tool_name": "agentteams.hr.lookup_role_change",
        "plugin_id": "agentteams-role-change-hr",
        "result": "岗位调整审批、目标岗位和生效日期已核查。",
    },
    "role-change-it-plan": {
        "title": "核查岗位权限变更计划",
        "instruction": "核查张明需要撤销和新增的权限，并确认账号变更条件。",
        "assignee_agent_id": "VE-0003",
        "required_capability": "it.permission.plan",
        "skill_id": "it-role-change-assistant",
        "tool_name": "agentteams.it.lookup_permission_plan",
        "plugin_id": "agentteams-role-change-it-plan",
        "result": "权限变更计划已核查，进入独立审计复核。",
    },
    "role-change-audit": {
        "title": "独立复核岗位权限范围",
        "instruction": "独立复核张明的权限变更范围并返回批准编号。",
        "assignee_agent_id": "VE-0005",
        "required_capability": "audit.permission.review",
        "skill_id": "audit-role-change-review",
        "tool_name": "agentteams.audit.review_role_change",
        "plugin_id": "agentteams-role-change-audit",
        "result": "权限范围已完成独立审计并批准。",
    },
    "role-change-it-activate": {
        "title": "执行岗位权限激活",
        "instruction": "使用独立审计批准编号执行张明的最终权限激活。",
        "assignee_agent_id": "VE-0003",
        "required_capability": "it.permission.activate",
        "skill_id": "it-role-change-assistant",
        "tool_name": "agentteams.it.activate_role_change",
        "plugin_id": "agentteams-role-change-it-activate",
        "result": "旧岗位权限已撤销，目标岗位权限已激活。",
    },
}

_LOCAL_RUNTIME_REFS = {
    "VE-0005": "audit-worker",
}


class LocalDemoTransport:
    """Stateful deterministic scenario engine behind the transport boundary."""

    mode = "local_demo"

    def __init__(self) -> None:
        self._outbound_ids: dict[str, str] = {}
        self._events: list[MatrixEvent] = []
        self._cancelled_roots: set[str] = set()
        self._cursor_generation = uuid4().hex
        self._task_keys: dict[str, str] = {}
        self._role_change_roots: set[str] = set()

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
        worker = _LOCAL_RUNTIME_REFS.get(agent_id, f"dwp-{agent_id.lower()}")
        return f"@{worker}:{config.agentteams_matrix_domain()}"

    def _responses(self, message: AgentProtocolMessage) -> list[AgentProtocolMessage]:
        if message.type == "task.submit":
            if self._is_role_change_submit(message):
                self._role_change_roots.add(message.root_task_id)
                return self._role_change_manager_submit(message)
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
            if isinstance(client_task_key, str) and client_task_key in _ROLE_CHANGE_TASKS:
                self._task_keys[message.task_id] = client_task_key
                return self._role_change_worker_assign(message, client_task_key)
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
            client_task_key = self._task_keys.get(message.task_id)
            if client_task_key in _ROLE_CHANGE_TASKS:
                return self._role_change_tool_result(message, client_task_key)
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
            if self._is_role_change_review(message):
                current_round = message.payload.get("current_round")
                if current_round == 1:
                    return self._role_change_manager_follow_up(message)
                return self._role_change_manager_summary(message)
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

    @staticmethod
    def _is_role_change_submit(message: AgentProtocolMessage) -> bool:
        return (
            message.payload.get("title") == ROLE_CHANGE_TITLE
            and message.payload.get("instruction") == ROLE_CHANGE_INSTRUCTION
        )

    def _is_role_change_review(self, message: AgentProtocolMessage) -> bool:
        if message.root_task_id in self._role_change_roots:
            return True
        child_results = message.payload.get("child_results")
        if not isinstance(child_results, list):
            return False
        return any(
            isinstance(item, Mapping)
            and str(item.get("client_task_key") or "").startswith("role-change-")
            for item in child_results
        )

    def _role_change_manager_submit(
        self, message: AgentProtocolMessage
    ) -> list[AgentProtocolMessage]:
        return [
            self._response(
                message,
                "agent.message",
                response_number=1,
                sender="VE-0001",
                payload={
                    "kind": "skill.selected",
                    "selected_skill_ids": [ROLE_CHANGE_SKILL_ID],
                },
            ),
            self._trace(
                message,
                response_number=2,
                sender="VE-0001",
                iteration=1,
                action="delegate",
                reasoning="岗位调整需要先并行取得HR审批证据和IT权限计划，再由Manager复核。",
            ),
            self._response(
                message,
                "task.delegate",
                response_number=3,
                sender="VE-0001",
                payload={
                    "subtasks": self._role_change_subtasks(
                        ("role-change-hr", "role-change-it-plan")
                    ),
                    "selected_skill_ids": [ROLE_CHANGE_SKILL_ID],
                    "round": 1,
                },
            ),
        ]

    def _role_change_manager_follow_up(
        self, message: AgentProtocolMessage
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message,
                response_number=1,
                sender="VE-0001",
                iteration=2,
                action="delegate",
                reasoning="第一轮确认权限激活仍需独立审计，因此补派审计复核，并让IT激活等待审计批准。",
            ),
            self._response(
                message,
                "task.delegate",
                response_number=2,
                sender="VE-0001",
                payload={
                    "subtasks": self._role_change_subtasks(
                        ("role-change-audit", "role-change-it-activate"),
                        activation_depends_on_audit=True,
                    ),
                    "selected_skill_ids": [ROLE_CHANGE_SKILL_ID],
                    "round": 2,
                },
            ),
        ]

    def _role_change_manager_summary(
        self, message: AgentProtocolMessage
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message,
                response_number=1,
                sender="VE-0001",
                iteration=3,
                action="summarize",
                reasoning="HR、IT计划、独立审计和最终激活证据均已返回，可以形成岗位调整结论。",
            ),
            self._response(
                message,
                "task.summary",
                response_number=2,
                sender="VE-0001",
                payload={
                    "summary": "岗位调整审批、权限计划、独立审计和权限激活均已完成，张明的岗位调整可以按期完成。",
                    "readiness_decision": "ready",
                    "completed_items": [
                        "岗位调整审批与材料已核查",
                        "权限变更计划已确认",
                        "独立审计已批准",
                        "目标岗位权限已激活",
                    ],
                    "risks": [],
                    "unknowns": [],
                    "next_actions": ["按计划在生效日期完成岗位交接"],
                },
            ),
        ]

    def _role_change_worker_assign(
        self, message: AgentProtocolMessage, client_task_key: str
    ) -> list[AgentProtocolMessage]:
        spec = _ROLE_CHANGE_TASKS[client_task_key]
        params = {"employee_ref": ROLE_CHANGE_EMPLOYEE_REF}
        if client_task_key == "role-change-it-activate":
            approval_ref = self._dependency_approval_ref(message.payload)
            if approval_ref:
                params["approval_ref"] = approval_ref
        sender = str(spec["assignee_agent_id"])
        return [
            self._response(message, "task.accepted", response_number=1, sender=sender),
            self._response(
                message,
                "task.progress",
                response_number=2,
                sender=sender,
                payload={"progress": 40},
            ),
            self._response(
                message,
                "agent.message",
                response_number=3,
                sender=sender,
                payload={
                    "kind": "skill.selected",
                    "selected_skill_ids": [spec["skill_id"]],
                },
            ),
            self._trace(
                message,
                response_number=4,
                sender=sender,
                iteration=1,
                action="tool",
                reasoning=f"当前任务需要{spec['title']}，先通过Gateway取得对应证据。",
            ),
            self._response(
                message,
                "tool.request",
                response_number=5,
                sender=sender,
                payload={
                    "tool_name": spec["tool_name"],
                    "plugin_id": spec["plugin_id"],
                    "action": "execute",
                    "required_capability": spec["required_capability"],
                    "params": params,
                },
            ),
        ]

    def _role_change_tool_result(
        self, message: AgentProtocolMessage, client_task_key: str
    ) -> list[AgentProtocolMessage]:
        spec = _ROLE_CHANGE_TASKS[client_task_key]
        sender = str(spec["assignee_agent_id"])
        succeeded = message.payload.get("ok") is True and message.payload.get("decision") == "allow"
        if not succeeded:
            return [
                self._trace(
                    message,
                    response_number=1,
                    sender=sender,
                    iteration=2,
                    action="fail",
                    reasoning="Gateway未返回允许执行的有效证据，本节点不能报告成功。",
                ),
                self._response(
                    message,
                    "task.failed",
                    response_number=2,
                    sender=sender,
                    payload={"error_code": "tool_evidence_unavailable"},
                ),
            ]
        data = message.payload.get("data")
        result_payload: dict[str, Any] = {
            "result": spec["result"],
            "audit_ids": list(message.payload.get("audit_ids") or []),
        }
        if isinstance(data, Mapping):
            result_payload["evidence"] = dict(data)
            approval_ref = data.get("approval_ref")
            if isinstance(approval_ref, str) and approval_ref:
                result_payload["approval_ref"] = approval_ref
        return [
            self._trace(
                message,
                response_number=1,
                sender=sender,
                iteration=2,
                action="complete",
                reasoning="Gateway已返回允许执行的审计证据，可以向Manager提交本节点结果。",
            ),
            self._response(
                message,
                "task.result",
                response_number=2,
                sender=sender,
                payload=result_payload,
            ),
        ]

    @staticmethod
    def _dependency_approval_ref(payload: Mapping[str, Any]) -> str:
        dependency_results = payload.get("dependency_results")
        if not isinstance(dependency_results, list):
            return ""
        for dependency in dependency_results:
            if not isinstance(dependency, Mapping):
                continue
            result = dependency.get("result")
            if not isinstance(result, Mapping):
                continue
            approval_ref = result.get("approval_ref")
            if approval_ref == ROLE_CHANGE_APPROVAL_REF:
                return approval_ref
        return ""

    @staticmethod
    def _role_change_subtasks(
        keys: tuple[str, ...], *, activation_depends_on_audit: bool = False
    ) -> list[dict[str, Any]]:
        subtasks: list[dict[str, Any]] = []
        for key in keys:
            spec = _ROLE_CHANGE_TASKS[key]
            depends_on = (
                ["role-change-audit"]
                if activation_depends_on_audit and key == "role-change-it-activate"
                else []
            )
            subtasks.append({
                "client_task_key": key,
                "title": spec["title"],
                "instruction": spec["instruction"],
                "assignee_agent_id": spec["assignee_agent_id"],
                "capability": spec["required_capability"],
                "depends_on": depends_on,
                "material": {},
                "required": True,
            })
        return subtasks

    def _trace(
        self,
        request: AgentProtocolMessage,
        *,
        response_number: int,
        sender: str,
        iteration: int,
        action: str,
        reasoning: str,
    ) -> AgentProtocolMessage:
        return self._response(
            request,
            "agent.message",
            response_number=response_number,
            sender=sender,
            payload={
                "kind": "decision.trace",
                "stage": "decision",
                "iteration": iteration,
                "action": action,
                "reasoning_segments": [reasoning],
            },
        )

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
