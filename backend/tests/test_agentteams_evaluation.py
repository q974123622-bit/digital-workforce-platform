from __future__ import annotations

from copy import deepcopy

from app.services.agentteams_evaluation import AgentTeamsEvaluator, evaluate_rules
from app.services.llm import LLMProvider, LLMUnavailableError


def _completed_collaboration():
    nodes = [
        {"id": "root", "client_task_key": "root", "title": "岗位调整评估", "instruction": "评估演示员工岗位调整", "capability": "coordinate", "parent_task_id": None, "assignee_agent_id": "VE-0001", "required": True, "status": "completed"},
        {"id": "hr", "client_task_key": "hr-check", "title": "核查HR记录", "instruction": "核查审批和目标岗位", "capability": "hr.role_change.read", "parent_task_id": "root", "assignee_agent_id": "VE-0002", "required": True, "status": "completed"},
        {"id": "it-plan", "client_task_key": "it-plan", "title": "核查权限计划", "instruction": "核查权限变更计划", "capability": "it.permission.read", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "required": True, "status": "completed"},
        {"id": "audit", "client_task_key": "audit-review", "title": "审计权限范围", "instruction": "独立复核权限范围", "capability": "audit.permission.review", "parent_task_id": "root", "assignee_agent_id": "VE-0005", "required": True, "status": "completed"},
        {"id": "it-activate", "client_task_key": "it-activate", "title": "激活最终权限", "instruction": "根据审计结论激活权限", "capability": "it.permission.activate", "parent_task_id": "root", "assignee_agent_id": "VE-0003", "required": True, "status": "completed"},
    ]
    dependencies = [{"task_id": "it-activate", "depends_on_task_id": "audit"}]
    messages = [
        {"id": "m1", "task_id": "hr", "message_type": "task.assign", "sender_agent_id": "VE-0001", "receiver_agent_id": "VE-0002", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:00"},
        {"id": "m2", "task_id": "it-plan", "message_type": "task.assign", "sender_agent_id": "VE-0001", "receiver_agent_id": "VE-0003", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:01"},
        {"id": "m3", "idempotency_key": "hr:req", "task_id": "hr", "message_type": "tool.request", "sender_agent_id": "VE-0002", "receiver_agent_id": "platform", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:02", "payload": {"tool_name": "agentteams.hr.lookup_role_change"}},
        {"id": "m4", "idempotency_key": "hr:result:placeholder", "task_id": "hr", "message_type": "tool.result", "sender_agent_id": "platform", "receiver_agent_id": "VE-0002", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:03", "payload": {"decision": "allow", "audit_ids": [89]}},
        {"id": "m5", "idempotency_key": "it:req", "task_id": "it-plan", "message_type": "tool.request", "sender_agent_id": "VE-0003", "receiver_agent_id": "platform", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:04", "payload": {"tool_name": "agentteams.it.lookup_permission_plan"}},
        {"id": "m6", "idempotency_key": "it:result:placeholder", "task_id": "it-plan", "message_type": "tool.result", "sender_agent_id": "platform", "receiver_agent_id": "VE-0003", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:05", "payload": {"decision": "allow", "audit_ids": [90]}},
        {"id": "m7", "task_id": "hr", "message_type": "task.summary", "sender_agent_id": "VE-0002", "receiver_agent_id": "platform", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:06"},
        {"id": "m8", "task_id": "audit", "message_type": "task.assign", "sender_agent_id": "VE-0001", "receiver_agent_id": "VE-0005", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:07"},
        {"id": "m9", "idempotency_key": "audit:req", "task_id": "audit", "message_type": "tool.request", "sender_agent_id": "VE-0005", "receiver_agent_id": "platform", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:08", "payload": {"tool_name": "agentteams.audit.review_role_change"}},
        {"id": "m10", "idempotency_key": "audit:result:placeholder", "task_id": "audit", "message_type": "tool.result", "sender_agent_id": "platform", "receiver_agent_id": "VE-0005", "delivery_status": "delivered", "created_at": "2026-09-07T09:00:09", "payload": {"decision": "allow", "audit_ids": [91]}},
    ]
    # The real gateway result key is derived from the request key.
    import hashlib
    for request, result in ((messages[2], messages[3]), (messages[4], messages[5]), (messages[8], messages[9])):
        digest = hashlib.sha256(request["idempotency_key"].encode()).hexdigest()[:32]
        result["idempotency_key"] = f'{request["task_id"]}:tool.result:{digest}'
    events = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1, "client_task_keys": ["hr-check", "it-plan"]}},
        {"event_seq": 2, "event_type": "task.summarizing", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 1}},
        {"event_seq": 3, "event_type": "task.delegate", "task_id": "root", "actor_agent_id": "VE-0001", "detail": {"round": 2, "client_task_keys": ["audit-review", "it-activate"]}},
    ]
    return nodes, dependencies, messages, events


def test_rules_score_completed_parallel_serial_follow_up_and_audited_tools():
    nodes, dependencies, messages, events = _completed_collaboration()

    result = evaluate_rules(nodes, dependencies, messages, "岗位调整可以按期完成。", events)

    assert result.score == 60
    assert result.max_score == 60
    assert [item.score for item in result.dimensions] == [10, 10, 15, 15, 10]
    assert result.deductions == []


def test_rules_correlate_persisted_inbound_tool_request_keys():
    nodes, dependencies, messages, events = _completed_collaboration()
    for request in (item for item in messages if item["message_type"] == "tool.request"):
        protocol_key = request["idempotency_key"]
        tool_payload = request["payload"]
        request["idempotency_key"] = f"in:{protocol_key}"
        request["payload"] = {
            "version": "1.0",
            "idempotency_key": protocol_key,
            "payload": tool_payload,
        }

    result = evaluate_rules(nodes, dependencies, messages, "岗位调整可以按期完成。", events)

    tool_governance = next(item for item in result.dimensions if item.key == "tool_governance")
    assert tool_governance.score == 15
    assert tool_governance.reason == "3/3次Tool请求具有Gateway结果和审计编号。"


def test_rules_deduct_missing_audit_and_undelivered_message():
    nodes, dependencies, messages, events = _completed_collaboration()
    messages[3]["payload"] = {"decision": "allow", "audit_ids": []}
    messages[1]["delivery_status"] = "failed"

    result = evaluate_rules(nodes, dependencies, messages, "岗位调整可以按期完成。", events)

    assert result.score < 60
    assert any("审计" in item for item in result.deductions)
    assert any("消息" in item for item in result.deductions)


class FakeLLM(LLMProvider):
    def __init__(self, outputs):
        self.outputs = list(outputs)
        self.calls = []

    def structured_output(self, messages, schema):
        self.calls.append((messages, schema))
        value = self.outputs.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def chat(self, messages, tools=None):
        raise AssertionError("evaluation must use structured output")

    def tool_call(self, messages, tools):
        raise AssertionError("evaluation must use structured output")


MODEL_RESULT = {
    "dimensions": [
        {"key": "decomposition", "label": "任务拆解", "score": 9, "max_score": 10, "reason": "任务边界清楚。"},
        {"key": "routing", "label": "Agent选择", "score": 9, "max_score": 10, "reason": "职责与能力匹配。"},
        {"key": "follow_up", "label": "后续判断", "score": 8, "max_score": 10, "reason": "收到结果后继续派发审计。"},
        {"key": "final_answer", "label": "最终答案", "score": 8, "max_score": 10, "reason": "结论和证据完整。"},
    ],
    "strengths": ["并行收集证据后继续派发"],
    "deductions": ["最终结论可以更精简"],
    "verdict": "协作过程完整，结果可信。",
}


def test_model_evaluation_returns_validated_total_without_credentials_in_prompt():
    nodes, dependencies, messages, events = _completed_collaboration()
    provider = FakeLLM([MODEL_RESULT])

    result = AgentTeamsEvaluator(provider).evaluate(
        task={"scenario_id": "role_change", "goal": "评估演示员工的岗位调整是否可以按期完成", "selected_skill_ids": ["agentteams-role-change-readiness"]},
        nodes=nodes, dependencies=dependencies, messages=messages,
        summary="岗位调整可以按期完成。", events=events,
    )

    assert result["rule_score"] == 60
    assert result["model_status"] == "completed"
    assert result["model_score"] == 34
    assert result["total_score"] == 94
    prompt = str(provider.calls)
    assert "token" not in prompt.lower()
    assert "api_key" not in prompt.lower()
    assert '"dimensions"' in prompt
    assert "核查HR记录" in prompt
    assert "HR岗位调整核查Agent" in prompt


def test_model_validation_retries_once_then_keeps_rule_score_when_unavailable():
    nodes, dependencies, messages, events = _completed_collaboration()
    invalid = deepcopy(MODEL_RESULT)
    invalid["dimensions"][0]["score"] = 99
    provider = FakeLLM([invalid, LLMUnavailableError("offline")])

    result = AgentTeamsEvaluator(provider).evaluate(
        task={"scenario_id": "role_change", "goal": "评估演示员工的岗位调整是否可以按期完成", "selected_skill_ids": []},
        nodes=nodes, dependencies=dependencies, messages=messages,
        summary="岗位调整可以按期完成。", events=events,
    )

    assert len(provider.calls) == 2
    assert result["rule_score"] == 60
    assert result["model_status"] == "unavailable"
    assert result["model_score"] is None
    assert result["total_score"] is None


def test_sensitive_looking_summary_is_never_sent_to_model():
    nodes, dependencies, messages, _events = _completed_collaboration()
    provider = FakeLLM([MODEL_RESULT])

    result = AgentTeamsEvaluator(provider).evaluate(
        task={"scenario_id": "role_change", "goal": "评估演示员工的岗位调整是否可以按期完成", "selected_skill_ids": ["agentteams-role-change-readiness"]},
        nodes=nodes, dependencies=dependencies, messages=messages,
        summary="token=should-never-leave-the-server",
    )

    assert provider.calls == []
    assert result["model_status"] == "unavailable"


def test_rules_do_not_infer_parallel_or_follow_up_from_unrelated_messages():
    nodes, dependencies, messages, _events = _completed_collaboration()
    unrelated = [
        {"event_seq": 1, "event_type": "task.delegate", "task_id": "it-plan", "actor_agent_id": "VE-0003", "detail": {}},
    ]

    result = evaluate_rules(nodes, dependencies, messages, "岗位调整可以按期完成。", unrelated)

    orchestration = next(item for item in result.dimensions if item.key == "orchestration")
    assert orchestration.score == 5
    assert "串行依赖" in orchestration.reason
    assert "并行分支" not in orchestration.reason
    assert "继续派发" not in orchestration.reason


def test_common_pii_in_summary_is_never_sent_to_model():
    nodes, dependencies, messages, _events = _completed_collaboration()
    provider = FakeLLM([MODEL_RESULT])

    result = AgentTeamsEvaluator(provider).evaluate(
        task={"scenario_id": "role_change", "goal": "评估演示员工", "selected_skill_ids": ["agentteams-role-change-readiness"]},
        nodes=nodes, dependencies=dependencies, messages=messages,
        summary="员工邮箱 test@example.com，手机 13800138000。",
    )

    assert provider.calls == []
    assert result["model_status"] == "unavailable"
    assert result["total_score"] is None
