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


_ROLE_CHANGE_LEADER_TASKS: dict[str, dict[str, Any]] = {
    "role-change-hr": {
        "title": "HR部门岗位调整核查",
        "instruction": "组织直属HR员工核查张明的岗位调整审批、目标岗位和生效日期。",
        "assignee_agent_id": "VE-0002",
        "required_capability": "hr.role_change.coordinate",
        "children": ("role-change-hr-material", "role-change-hr-training"),
    },
    "role-change-it-plan": {
        "title": "IT部门权限计划核查",
        "instruction": "组织直属IT员工核查张明需要撤销和新增的权限。",
        "assignee_agent_id": "VE-0003",
        "required_capability": "it.role_change.plan.coordinate",
        "children": ("role-change-it-plan-account", "role-change-it-plan-device"),
    },
    "role-change-audit": {
        "title": "审计部门独立复核",
        "instruction": "组织直属审计员工独立复核张明的权限变更范围。",
        "assignee_agent_id": "VE-0005",
        "required_capability": "audit.role_change.coordinate",
        "children": ("role-change-audit-review",),
    },
    "role-change-it-activate": {
        "title": "IT部门权限激活",
        "instruction": "组织直属IT员工使用独立审计批准编号执行最终权限激活。",
        "assignee_agent_id": "VE-0003",
        "required_capability": "it.role_change.activate.coordinate",
        "children": ("role-change-it-activate-worker",),
    },
}

_ROLE_CHANGE_TASKS: dict[str, dict[str, Any]] = {
    "role-change-hr-material": {
        "title": "核查岗位调整审批与材料", "instruction": "核查张明的岗位调整审批、目标岗位和生效日期。",
        "assignee_agent_id": "VE-HR-MATERIAL", "required_capability": "hr.role_change.read",
        "skill_id": "hr-role-change-assistant", "tool_name": "agentteams.hr.lookup_role_change",
        "plugin_id": "agentteams-role-change-hr", "result": "岗位调整审批、目标岗位和生效日期已核查。",
    },
    "role-change-hr-training": {
        "title": "复核岗位培训衔接", "instruction": "复核张明岗位调整后的培训衔接。",
        "assignee_agent_id": "VE-HR-TRAINING", "required_capability": "hr.role_change.read",
        "skill_id": "hr-role-change-assistant", "tool_name": "agentteams.hr.verify_role_change",
        "plugin_id": "agentteams-role-change-hr", "result": "岗位培训衔接已核查。",
    },
    "role-change-it-plan-account": {
        "title": "核查岗位权限变更计划", "instruction": "核查张明账号权限变更计划。",
        "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.permission.plan",
        "skill_id": "it-role-change-assistant", "tool_name": "agentteams.it.lookup_permission_plan",
        "plugin_id": "agentteams-role-change-it-plan", "result": "账号权限变更计划已核查。",
    },
    "role-change-it-plan-device": {
        "title": "复核岗位设备权限计划", "instruction": "复核张明设备权限变更计划。",
        "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.permission.plan",
        "skill_id": "it-role-change-assistant", "tool_name": "agentteams.it.verify_permission_plan",
        "plugin_id": "agentteams-role-change-it-plan", "result": "设备权限变更计划已核查。",
    },
    "role-change-audit-review": {
        "title": "独立复核岗位权限范围", "instruction": "独立复核张明的权限变更范围并返回批准编号。",
        "assignee_agent_id": "VE-AUDIT-REVIEW", "required_capability": "audit.permission.review",
        "skill_id": "audit-role-change-review", "tool_name": "agentteams.audit.review_role_change",
        "plugin_id": "agentteams-role-change-audit", "result": "权限范围已完成独立审计并批准。",
    },
    "role-change-it-activate-worker": {
        "title": "执行岗位权限激活", "instruction": "使用独立审计批准编号执行张明的最终权限激活。",
        "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.permission.activate",
        "skill_id": "it-role-change-assistant",
        "tool_name": "agentteams.it.activate_role_change",
        "plugin_id": "agentteams-role-change-it-activate",
        "result": "旧岗位权限已撤销，目标岗位权限已激活。",
    },
}

_LOCAL_RUNTIME_REFS = {
    "VE-0005": "audit-leader",
    "VE-HR-MATERIAL": "hr-material-worker",
    "VE-HR-TRAINING": "hr-training-worker",
    "VE-IT-ACCOUNT": "it-account-worker",
    "VE-IT-DEVICE": "it-device-worker",
    "VE-AUDIT-REVIEW": "audit-review-worker",
}

_ONBOARDING_LEADER_TASKS: dict[str, dict[str, Any]] = {
    "onboarding-hr-department": {
        "title": "HR部门入职核查", "instruction": "组织直属HR员工核查入职材料和培训。",
        "assignee_agent_id": "VE-0002", "required_capability": "hr.onboarding.coordinate",
        "children": ("onboarding-hr-materials", "onboarding-hr-training"),
    },
    "onboarding-it-department": {
        "title": "IT部门入职核查", "instruction": "组织直属IT员工核查账号和设备。",
        "assignee_agent_id": "VE-0003", "required_capability": "it.onboarding.coordinate",
        "children": ("onboarding-it-accounts", "onboarding-it-devices"),
    },
    "onboarding-audit-review": {
        "title": "审计部门入职复核", "instruction": "组织直属审计员工复核HR与IT证据。",
        "assignee_agent_id": "VE-0005", "required_capability": "audit.onboarding.coordinate",
        "children": ("onboarding-audit-evidence",),
    },
    "onboarding-it-reconciliation": {
        "title": "IT部门证据补充核查", "instruction": "根据审计结果组织直属IT员工补充核对账号和设备证据。",
        "assignee_agent_id": "VE-0003", "required_capability": "it.onboarding.coordinate",
        "children": ("onboarding-it-accounts-follow-up", "onboarding-it-devices-follow-up"),
    },
}

_ONBOARDING_TASKS: dict[str, dict[str, Any]] = {
    "onboarding-hr-materials": {
        "title": "核查入职材料", "instruction": "核查新员工A的入职材料。",
        "assignee_agent_id": "VE-HR-MATERIAL", "required_capability": "hr.materials.read",
        "skill_id": "hr-material-specialist", "tool_name": "agentteams.hr.lookup_materials",
        "plugin_id": "agentteams-onboarding-materials", "result": "入职材料已核查。",
    },
    "onboarding-hr-training": {
        "title": "核查入职培训", "instruction": "核查新员工A的培训安排。",
        "assignee_agent_id": "VE-HR-TRAINING", "required_capability": "hr.training.read",
        "skill_id": "hr-training-specialist", "tool_name": "agentteams.hr.lookup_training",
        "plugin_id": "agentteams-onboarding-training", "result": "入职培训已核查。",
    },
    "onboarding-it-accounts": {
        "title": "核查IT账号", "instruction": "核查新员工A的账号准备状态。",
        "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.accounts.read",
        "skill_id": "it-account-specialist", "tool_name": "agentteams.it.lookup_accounts",
        "plugin_id": "agentteams-it-accounts", "result": "IT账号已核查。",
    },
    "onboarding-it-devices": {
        "title": "核查IT设备", "instruction": "核查新员工A的设备准备状态。",
        "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.devices.read",
        "skill_id": "it-device-specialist", "tool_name": "agentteams.it.lookup_devices",
        "plugin_id": "agentteams-it-devices", "result": "IT设备已核查。",
    },
    "onboarding-audit-evidence": {
        "title": "复核入职证据", "instruction": "独立复核新员工A的HR与IT证据。",
        "assignee_agent_id": "VE-AUDIT-REVIEW", "required_capability": "audit.evidence.read",
        "skill_id": "audit-evidence-review", "tool_name": "agentteams.audit.lookup_evidence",
        "plugin_id": "agentteams-onboarding-evidence", "result": "入职证据已独立复核。",
    },
    "onboarding-it-accounts-follow-up": {
        "title": "补充核查IT账号", "instruction": "根据审计结果再次核查新员工A的账号准备状态。",
        "assignee_agent_id": "VE-IT-ACCOUNT", "required_capability": "it.accounts.read",
        "skill_id": "it-account-specialist", "tool_name": "agentteams.it.lookup_accounts",
        "plugin_id": "agentteams-it-accounts", "result": "IT账号补充证据已核查。",
    },
    "onboarding-it-devices-follow-up": {
        "title": "补充核查IT设备", "instruction": "根据审计结果再次核查新员工A的设备准备状态。",
        "assignee_agent_id": "VE-IT-DEVICE", "required_capability": "it.devices.read",
        "skill_id": "it-device-specialist", "tool_name": "agentteams.it.lookup_devices",
        "plugin_id": "agentteams-it-devices", "result": "IT设备补充证据已核查。",
    },
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
        self._onboarding_roots: set[str] = set()

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
            self._onboarding_roots.add(message.root_task_id)
            return self._onboarding_manager_submit(message)

        if message.type == "task.assign":
            client_task_key = message.payload.get("client_task_key")
            if isinstance(client_task_key, str) and client_task_key in _ONBOARDING_LEADER_TASKS:
                return self._onboarding_leader_assign(message, client_task_key)
            if isinstance(client_task_key, str) and client_task_key in _ONBOARDING_TASKS:
                self._task_keys[message.task_id] = client_task_key
                return self._onboarding_worker_assign(message, client_task_key)
            if isinstance(client_task_key, str) and client_task_key in _ROLE_CHANGE_LEADER_TASKS:
                return self._role_change_leader_assign(message, client_task_key)
            if isinstance(client_task_key, str) and client_task_key in _ROLE_CHANGE_TASKS:
                self._task_keys[message.task_id] = client_task_key
                return self._role_change_worker_assign(message, client_task_key)
            return []

        if message.type == "tool.result":
            client_task_key = self._task_keys.get(message.task_id)
            if client_task_key in _ONBOARDING_TASKS:
                return self._onboarding_tool_result(message, client_task_key)
            if client_task_key in _ROLE_CHANGE_TASKS:
                return self._role_change_tool_result(message, client_task_key)
            return []

        if message.type == "task.result.submitted":
            return [
                self._response(
                    message,
                    "task.result.accepted",
                    response_number=1,
                    sender=message.receiver,
                    payload={"feedback": "本地演示结果验收通过"},
                )
            ]

        if message.type == "task.summary":
            if self._is_role_change_review(message):
                if message.task_id != message.root_task_id:
                    return self._role_change_leader_summary(message)
                current_round = message.payload.get("current_round")
                if current_round == 1:
                    return self._role_change_manager_follow_up(message)
                return self._role_change_manager_summary(message)
            if message.root_task_id in self._onboarding_roots:
                if message.task_id != message.root_task_id:
                    return self._onboarding_leader_summary(message)
                if message.payload.get("current_round") == 1:
                    return self._onboarding_manager_follow_up(message)
                if message.payload.get("current_round") == 2:
                    return self._onboarding_manager_reconciliation(message)
                return self._onboarding_manager_summary(message)
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

    def _onboarding_manager_submit(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message, response_number=1, sender="VE-0001", iteration=1,
                action="delegate", reasoning="先把HR和IT核查分别交给部门负责人。",
            ),
            self._response(
                message, "task.delegate", response_number=2, sender="VE-0001",
                payload={
                    "subtasks": self._onboarding_leader_subtasks(
                        ("onboarding-hr-department", "onboarding-it-department")
                    ),
                    "round": 1,
                },
            ),
        ]

    def _onboarding_manager_follow_up(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message, response_number=1, sender="VE-0001", iteration=2,
                action="delegate", reasoning="HR和IT部门结果返回后，再交给审计负责人独立复核。",
            ),
            self._response(
                message, "task.delegate", response_number=2, sender="VE-0001",
                payload={
                    "subtasks": self._onboarding_leader_subtasks(("onboarding-audit-review",)),
                    "round": 2,
                },
            ),
        ]

    def _onboarding_manager_summary(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message, response_number=1, sender="VE-0001", iteration=4,
                action="summarize", reasoning="三个部门负责人均已汇总直属员工证据，可以形成入职结论。",
            ),
            self._response(
                message, "task.summary", response_number=2, sender="VE-0001",
                payload={
                    "summary": "HR材料、培训、IT账号、设备与独立审计均已完成，新员工可以按计划入职。",
                    "readiness_decision": "ready",
                    "completed_items": ["HR、IT和审计部门核查均已完成"],
                    "risks": [], "unknowns": [], "next_actions": ["按计划办理入职"],
                },
            ),
        ]

    def _onboarding_manager_reconciliation(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        return [
            self._trace(
                message, response_number=1, sender="VE-0001", iteration=3,
                action="delegate", reasoning="审计结果返回后，针对IT证据缺口再向IT负责人派发一次补充核查。",
            ),
            self._response(
                message, "task.delegate", response_number=2, sender="VE-0001",
                payload={
                    "subtasks": self._onboarding_leader_subtasks(("onboarding-it-reconciliation",)),
                    "round": 3,
                },
            ),
        ]

    def _onboarding_leader_assign(
        self, message: AgentProtocolMessage, client_task_key: str,
    ) -> list[AgentProtocolMessage]:
        spec = _ONBOARDING_LEADER_TASKS[client_task_key]
        sender = str(spec["assignee_agent_id"])
        return [
            self._response(message, "task.accepted", response_number=1, sender=sender),
            self._response(
                message, "task.progress", response_number=2, sender=sender,
                payload={"progress": 10},
            ),
            self._trace(
                message, response_number=3, sender=sender, iteration=1,
                action="delegate", reasoning="负责人按直属员工职责拆分核查任务。",
            ),
            self._response(
                message, "task.delegate", response_number=4, sender=sender,
                payload={"subtasks": self._onboarding_worker_subtasks(spec["children"])},
            ),
        ]

    def _onboarding_leader_summary(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        sender = message.receiver
        return [
            self._trace(
                message, response_number=1, sender=sender, iteration=2,
                action="complete", reasoning="直属员工结果均已返回并通过验收，提交部门汇总。",
            ),
            self._response(
                message, "task.result", response_number=2, sender=sender,
                payload={"result": "直属员工证据已完成验收，部门入职核查完成。"},
            ),
        ]

    def _onboarding_worker_assign(
        self, message: AgentProtocolMessage, client_task_key: str,
    ) -> list[AgentProtocolMessage]:
        spec = _ONBOARDING_TASKS[client_task_key]
        sender = str(spec["assignee_agent_id"])
        return [
            self._response(message, "task.accepted", response_number=1, sender=sender),
            self._response(
                message, "task.progress", response_number=2, sender=sender,
                payload={"progress": 40},
            ),
            self._response(
                message, "agent.message", response_number=3, sender=sender,
                payload={"kind": "skill.selected", "selected_skill_ids": [spec["skill_id"]]},
            ),
            self._trace(
                message, response_number=4, sender=sender, iteration=1,
                action="tool", reasoning=f"执行{spec['title']}并取得可追踪证据。",
            ),
            self._response(
                message, "tool.request", response_number=5, sender=sender,
                payload={
                    "tool_name": spec["tool_name"], "plugin_id": spec["plugin_id"],
                    "action": "execute", "required_capability": spec["required_capability"],
                    "params": {"employee_ref": "新员工A"},
                },
            ),
        ]

    def _onboarding_tool_result(
        self, message: AgentProtocolMessage, client_task_key: str,
    ) -> list[AgentProtocolMessage]:
        spec = _ONBOARDING_TASKS[client_task_key]
        sender = str(spec["assignee_agent_id"])
        if message.payload.get("ok") is not True or message.payload.get("decision") != "allow":
            return [self._response(
                message, "task.failed", response_number=1, sender=sender,
                payload={"error_code": "tool_evidence_unavailable"},
            )]
        return [
            self._trace(
                message, response_number=1, sender=sender, iteration=2,
                action="complete", reasoning="Gateway已返回有效证据，向直属负责人提交结果。",
            ),
            self._response(
                message, "task.result", response_number=2, sender=sender,
                payload={
                    "result": spec["result"],
                    "audit_ids": list(message.payload.get("audit_ids") or []),
                    "evidence": dict(message.payload.get("data") or {}),
                },
            ),
        ]

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
        if client_task_key == "role-change-it-activate-worker":
            material = message.payload.get("material")
            approval_ref = (
                str(material.get("approval_ref") or "")
                if isinstance(material, Mapping)
                else ""
            )
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

    def _role_change_leader_assign(
        self, message: AgentProtocolMessage, client_task_key: str,
    ) -> list[AgentProtocolMessage]:
        spec = _ROLE_CHANGE_LEADER_TASKS[client_task_key]
        sender = str(spec["assignee_agent_id"])
        approval_ref = self._dependency_approval_ref(message.payload)
        worker_material = {"approval_ref": approval_ref} if approval_ref else {}
        return [
            self._response(message, "task.accepted", response_number=1, sender=sender),
            self._response(
                message, "task.progress", response_number=2, sender=sender,
                payload={"progress": 10},
            ),
            self._trace(
                message,
                response_number=3,
                sender=sender,
                iteration=1,
                action="delegate",
                reasoning="负责人根据直属员工能力拆分任务，并等待员工结果后再汇总。",
            ),
            self._response(
                message,
                "task.delegate",
                response_number=4,
                sender=sender,
                payload={
                    "subtasks": self._role_change_worker_subtasks(
                        spec["children"], material=worker_material,
                    )
                },
            ),
        ]

    def _role_change_leader_summary(
        self, message: AgentProtocolMessage,
    ) -> list[AgentProtocolMessage]:
        sender = message.receiver
        approval_ref = self._dependency_approval_ref({
            "dependency_results": message.payload.get("child_results", []),
        })
        result_payload = {"result": "直属员工证据已完成验收，部门任务已汇总。"}
        if approval_ref:
            result_payload["approval_ref"] = approval_ref
        return [
            self._trace(
                message,
                response_number=1,
                sender=sender,
                iteration=2,
                action="complete",
                reasoning="直属员工证据均已返回并通过验收，负责人向Manager提交部门汇总。",
            ),
            self._response(
                message,
                "task.result",
                response_number=2,
                sender=sender,
                payload=result_payload,
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
            spec = _ROLE_CHANGE_LEADER_TASKS[key]
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

    @staticmethod
    def _role_change_worker_subtasks(
        keys: tuple[str, ...], *, material: Mapping[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        return [
            {
                "client_task_key": key,
                "title": _ROLE_CHANGE_TASKS[key]["title"],
                "instruction": _ROLE_CHANGE_TASKS[key]["instruction"],
                "assignee_agent_id": _ROLE_CHANGE_TASKS[key]["assignee_agent_id"],
                "capability": _ROLE_CHANGE_TASKS[key]["required_capability"],
                "depends_on": [],
                "material": dict(material or {}),
                "required": True,
            }
            for key in keys
        ]

    @staticmethod
    def _onboarding_leader_subtasks(keys: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {
                "client_task_key": key,
                "title": _ONBOARDING_LEADER_TASKS[key]["title"],
                "instruction": _ONBOARDING_LEADER_TASKS[key]["instruction"],
                "assignee_agent_id": _ONBOARDING_LEADER_TASKS[key]["assignee_agent_id"],
                "capability": _ONBOARDING_LEADER_TASKS[key]["required_capability"],
                "depends_on": [], "material": {}, "required": True,
            }
            for key in keys
        ]

    @staticmethod
    def _onboarding_worker_subtasks(keys: tuple[str, ...]) -> list[dict[str, Any]]:
        return [
            {
                "client_task_key": key,
                "title": _ONBOARDING_TASKS[key]["title"],
                "instruction": _ONBOARDING_TASKS[key]["instruction"],
                "assignee_agent_id": _ONBOARDING_TASKS[key]["assignee_agent_id"],
                "capability": _ONBOARDING_TASKS[key]["required_capability"],
                "depends_on": [], "material": {}, "required": True,
            }
            for key in keys
        ]

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
