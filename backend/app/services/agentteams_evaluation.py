"""Deterministic and model-assisted evaluation for persisted AgentTeams runs."""

from __future__ import annotations

from datetime import datetime, timezone
import hashlib
import json
import re
from typing import Any, Iterable, Literal, Mapping, Sequence

from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from .llm import LLMProvider, LLMUnavailableError


def _value(item: object, key: str, default: Any = None) -> Any:
    if isinstance(item, Mapping):
        return item.get(key, default)
    return getattr(item, key, default)


def _tool_request_protocol_key(request: object) -> str:
    payload = _value(request, "payload", {})
    if isinstance(payload, Mapping):
        protocol_key = payload.get("idempotency_key")
        if isinstance(protocol_key, str) and protocol_key:
            return protocol_key
    persisted_key = str(_value(request, "idempotency_key", ""))
    return persisted_key.removeprefix("in:")


class _StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class EvaluationDimension(_StrictModel):
    key: str = Field(min_length=1, max_length=40)
    label: str = Field(min_length=1, max_length=40)
    score: int = Field(ge=0)
    max_score: int = Field(gt=0)
    reason: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def score_does_not_exceed_maximum(self) -> "EvaluationDimension":
        if self.score > self.max_score:
            raise ValueError("score_exceeds_maximum")
        return self


class RuleEvaluation(_StrictModel):
    score: int = Field(ge=0, le=60)
    max_score: Literal[60] = 60
    dimensions: list[EvaluationDimension] = Field(min_length=5, max_length=5)
    deductions: list[str] = Field(default_factory=list, max_length=10)


_MODEL_KEYS = {"decomposition", "routing", "follow_up", "final_answer"}
_EVALUATION_SCENARIOS = {"onboarding", "role_change"}
_SENSITIVE_TEXT = re.compile(
    r"(?:\b(?:token|secret|password|api[_-]?key)\s*[:=]|\bsk-[A-Za-z0-9_-]{12,}|https?://|"
    r"@[A-Za-z0-9._=-]+:[A-Za-z0-9.-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"(?<!\d)1[3-9]\d{9}(?!\d)|(?<!\d)\d{17}[0-9Xx](?!\d))",
    re.IGNORECASE,
)

_SCENARIO_METADATA: dict[str, dict[str, Any]] = {
    "role_change": {
        "manager_skill": {
            "id": "agentteams-role-change-readiness",
            "name": "岗位调整就绪评估",
            "description": "指导Manager先并行核查HR审批与IT权限计划，再根据结果派发审计复核和受控权限激活。",
        },
        "agents": [
            {"id": "VE-0001", "role": "Manager Agent", "capabilities": ["任务拆解", "选择Agent", "阶段复核", "最终汇总"]},
            {"id": "VE-0002", "role": "HR岗位调整核查Agent", "capabilities": ["岗位审批核查", "目标岗位核查", "材料完整性核查"]},
            {"id": "VE-0003", "role": "IT权限执行Agent", "capabilities": ["权限计划核查", "权限激活"]},
            {"id": "VE-0005", "role": "审计复核Agent", "capabilities": ["权限范围复核", "职责分离检查"]},
        ],
    },
    "onboarding": {
        "manager_skill": {
            "id": "agentteams-onboarding-readiness",
            "name": "新员工入职就绪评估",
            "description": "指导Manager并行收集HR与IT证据，并在发现证据缺口时继续派发审计复核。",
        },
        "agents": [
            {"id": "VE-0001", "role": "Manager Agent", "capabilities": ["任务拆解", "选择Agent", "阶段复核", "最终汇总"]},
            {"id": "VE-0002", "role": "HR入职核查Agent", "capabilities": ["身份与合同材料核查"]},
            {"id": "VE-0003", "role": "IT就绪核查Agent", "capabilities": ["账号与设备状态核查"]},
            {"id": "VE-0005", "role": "审计复核Agent", "capabilities": ["缺失证据独立复核"]},
        ],
    },
}


class ModelEvaluation(_StrictModel):
    dimensions: list[EvaluationDimension] = Field(min_length=4, max_length=4)
    strengths: list[str] = Field(default_factory=list, max_length=6)
    deductions: list[str] = Field(default_factory=list, max_length=6)
    verdict: str = Field(min_length=1, max_length=240)

    @model_validator(mode="after")
    def dimensions_match_contract(self) -> "ModelEvaluation":
        if {item.key for item in self.dimensions} != _MODEL_KEYS:
            raise ValueError("invalid_dimension_keys")
        if any(item.max_score != 10 for item in self.dimensions):
            raise ValueError("invalid_dimension_maximum")
        return self

    @property
    def score(self) -> int:
        return sum(item.score for item in self.dimensions)


def _dimension(key: str, label: str, score: int, maximum: int, reason: str) -> EvaluationDimension:
    return EvaluationDimension(key=key, label=label, score=score, max_score=maximum, reason=reason)


def _ordered_messages(messages: Iterable[object]) -> list[object]:
    return sorted(messages, key=lambda item: str(_value(item, "created_at", "")))


def evaluate_rules(
    nodes: Sequence[object],
    dependencies: Sequence[object],
    messages: Sequence[object],
    summary: str,
    events: Sequence[object] = (),
) -> RuleEvaluation:
    """Score observable collaboration facts without model judgment."""
    dimensions: list[EvaluationDimension] = []
    deductions: list[str] = []
    required_nodes = [node for node in nodes if bool(_value(node, "required", True))]
    completed = bool(summary.strip()) and bool(required_nodes) and all(
        _value(node, "status") == "completed" for node in required_nodes
    )
    completion_score = 10 if completed else 0
    dimensions.append(_dimension(
        "completion", "任务完成", completion_score, 10,
        "必需任务和最终汇总均已完成。" if completed else "存在未完成的必需任务或缺少最终汇总。",
    ))
    if completion_score < 10:
        deductions.append("任务未形成完整终态结果。")

    children = [node for node in nodes if _value(node, "parent_task_id")]
    child_assignees = {str(_value(node, "assignee_agent_id", "")) for node in children if _value(node, "assignee_agent_id")}
    delegation_score = 10 if len(children) >= 2 and len(child_assignees) >= 2 else 5 if children else 0
    dimensions.append(_dimension(
        "delegation", "拆解与派发", delegation_score, 10,
        "任务已拆给至少两名不同职责的Agent。" if delegation_score == 10
        else "任务拆解或跨Agent派发不足。",
    ))
    if delegation_score < 10:
        deductions.append("任务拆解或跨Agent派发不足。")

    dependency_pairs = {
        (str(_value(edge, "depends_on_task_id", "")), str(_value(edge, "task_id", "")))
        for edge in dependencies
    }
    nodes_by_key = {
        str(_value(node, "client_task_key", "")): node
        for node in children if _value(node, "client_task_key")
    }
    ordered_events = sorted(events, key=lambda item: int(_value(item, "event_seq", 0) or 0))
    delegate_events = [item for item in ordered_events if _value(item, "event_type") == "task.delegate"]
    has_parallel = False
    for event in delegate_events:
        detail = _value(event, "detail", {})
        keys = detail.get("client_task_keys", []) if isinstance(detail, Mapping) else []
        delegated = [nodes_by_key[key] for key in keys if isinstance(key, str) and key in nodes_by_key]
        ids = [str(_value(node, "id", "")) for node in delegated]
        parents = {str(_value(node, "parent_task_id", "")) for node in delegated}
        if len(ids) >= 2 and len(parents) == 1 and any(
            (left, right) not in dependency_pairs and (right, left) not in dependency_pairs
            for index, left in enumerate(ids) for right in ids[index + 1:]
        ):
            has_parallel = True
            break
    has_serial = bool(dependency_pairs)
    root = next((node for node in nodes if not _value(node, "parent_task_id")), None)
    root_id = str(_value(root, "id", ""))
    manager_id = str(_value(root, "assignee_agent_id", ""))
    has_follow_up = False
    for index, event in enumerate(ordered_events):
        detail = _value(event, "detail", {})
        round_number = detail.get("round") if isinstance(detail, Mapping) else None
        if (
            _value(event, "event_type") == "task.delegate"
            and _value(event, "task_id") == root_id
            and _value(event, "actor_agent_id") == manager_id
            and isinstance(round_number, int) and round_number >= 2
            and any(
                _value(previous, "event_type") in {"task.summarizing", "task.summary", "task.completed"}
                for previous in ordered_events[:index]
            )
        ):
            has_follow_up = True
            break
    orchestration_score = 5 * sum((has_parallel, has_serial, has_follow_up))
    orchestration_parts = [
        label for present, label in (
            (has_parallel, "并行分支"), (has_serial, "串行依赖"), (has_follow_up, "收到结果后的继续派发"),
        ) if present
    ]
    dimensions.append(_dimension(
        "orchestration", "协作编排", orchestration_score, 15,
        "、".join(orchestration_parts) + "均有持久化证据。" if orchestration_parts else "未发现并行、串行或后续派发证据。",
    ))
    if orchestration_score < 15:
        deductions.append("并行、串行或后续派发证据不完整。")

    requests = [item for item in messages if _value(item, "message_type") == "tool.request"]
    results_by_key: dict[str, object] = {}
    for item in messages:
        if _value(item, "message_type") == "tool.result":
            results_by_key[str(_value(item, "idempotency_key", ""))] = item
    governed = 0
    for request in requests:
        request_key = _tool_request_protocol_key(request)
        digest = hashlib.sha256(request_key.encode("utf-8", errors="ignore")).hexdigest()[:32]
        result_key = f'{_value(request, "task_id", "")}:tool.result:{digest}'
        result = results_by_key.get(result_key)
        payload = _value(result, "payload", {}) if result is not None else {}
        audit_ids = payload.get("audit_ids", []) if isinstance(payload, Mapping) else []
        decision = payload.get("decision") if isinstance(payload, Mapping) else None
        request_time = str(_value(request, "created_at", ""))
        result_time = str(_value(result, "created_at", "")) if result is not None else ""
        if result is not None and result_time >= request_time and decision in {"allow", "deny", "failed"} and any(
            isinstance(value, int) and value > 0 for value in audit_ids if isinstance(audit_ids, list)
        ):
            governed += 1
    tool_score = round(15 * governed / len(requests)) if requests else 0
    dimensions.append(_dimension(
        "tool_governance", "Tool治理", tool_score, 15,
        f"{governed}/{len(requests)}次Tool请求具有Gateway结果和审计编号。" if requests else "未发现Tool调用记录。",
    ))
    if tool_score < 15:
        deductions.append("部分Tool调用缺少对应Gateway结果或审计编号。")

    delivered_states = {"delivered", "received", "processed", "sent"}
    failed_messages = [item for item in messages if str(_value(item, "delivery_status", "")) not in delivered_states]
    node_status = {str(_value(node, "id", "")): str(_value(node, "status", "")) for node in nodes}
    dependencies_satisfied = all(
        source in node_status and target in node_status
        and (node_status[target] != "completed" or node_status[source] == "completed")
        for source, target in dependency_pairs
    )
    communication_score = 10 if messages and not failed_messages and dependencies_satisfied else 5 if messages else 0
    dimensions.append(_dimension(
        "communication", "消息与依赖", communication_score, 10,
        "消息均已送达，已完成任务满足依赖关系。" if communication_score == 10
        else "存在未送达消息或未满足的任务依赖。",
    ))
    if communication_score < 10:
        deductions.append("存在未送达消息或未满足的任务依赖。")

    return RuleEvaluation(
        score=sum(item.score for item in dimensions),
        dimensions=dimensions,
        deductions=deductions,
    )


def _public_context(
    task: Mapping[str, Any], nodes: Sequence[object], dependencies: Sequence[object],
    messages: Sequence[object], summary: str,
) -> dict[str, Any]:
    scenario_id = str(task.get("scenario_id") or "")
    return {
        "task": {
            "scenario_id": scenario_id[:40],
            "goal": str(task.get("goal") or "")[:160],
            "selected_skill_ids": [str(item)[:128] for item in task.get("selected_skill_ids", []) if isinstance(item, str)][:8],
        },
        "scenario_contract": _SCENARIO_METADATA.get(scenario_id, {}),
        "nodes": [{
            "id": str(_value(node, "id", ""))[:100],
            "client_task_key": str(_value(node, "client_task_key", ""))[:100],
            "title": str(_value(node, "title", ""))[:120],
            "instruction": str(_value(node, "instruction", ""))[:240],
            "required_capability": str(_value(node, "capability", ""))[:128],
            "parent_task_id": str(_value(node, "parent_task_id", "") or "")[:100],
            "assignee_agent_id": str(_value(node, "assignee_agent_id", ""))[:80],
            "status": str(_value(node, "status", ""))[:40],
        } for node in nodes[:30]],
        "dependencies": [{
            "depends_on_task_id": str(_value(edge, "depends_on_task_id", ""))[:100],
            "task_id": str(_value(edge, "task_id", ""))[:100],
        } for edge in dependencies[:40]],
        "messages": [{
            "task_id": str(_value(item, "task_id", ""))[:100],
            "type": str(_value(item, "message_type", ""))[:60],
            "sender": str(_value(item, "sender_agent_id", ""))[:80],
            "receiver": str(_value(item, "receiver_agent_id", ""))[:80],
            "delivery_status": str(_value(item, "delivery_status", ""))[:40],
            "tool_name": str((_value(item, "payload", {}) or {}).get("tool_name", ""))[:128]
            if isinstance(_value(item, "payload", {}), Mapping) else "",
            "gateway_decision": str((_value(item, "payload", {}) or {}).get("decision", ""))[:20]
            if isinstance(_value(item, "payload", {}), Mapping) else "",
            "has_audit_record": bool((_value(item, "payload", {}) or {}).get("audit_ids"))
            if isinstance(_value(item, "payload", {}), Mapping) else False,
        } for item in _ordered_messages(messages)[:80]],
        "final_summary": summary[:1000],
    }


class AgentTeamsEvaluator:
    def __init__(self, provider: LLMProvider):
        self.provider = provider

    def evaluate(
        self, *, task: Mapping[str, Any], nodes: Sequence[object],
        dependencies: Sequence[object], messages: Sequence[object], summary: str,
        events: Sequence[object] = (),
    ) -> dict[str, Any]:
        rules = evaluate_rules(nodes, dependencies, messages, summary, events)
        scenario_id = str(task.get("scenario_id") or "")
        context = _public_context(task, nodes, dependencies, messages, summary)
        serialized_context = json.dumps(context, ensure_ascii=False)
        model_context_allowed = scenario_id in _EVALUATION_SCENARIOS and not _SENSITIVE_TEXT.search(serialized_context)
        if not model_context_allowed:
            context = {}
        model_result: ModelEvaluation | None = None
        for attempt in range(2 if model_context_allowed else 0):
            feedback = "" if attempt == 0 else "上一次输出未通过结构或分值校验，请严格按Schema重新评分。"
            try:
                raw = self.provider.structured_output([
                    {
                        "role": "system",
                        "source": "demo",
                        "content": (
                            "你是独立的多Agent协作评测器。只根据给定的虚构Demo证据评分，不补造事实。"
                            "四个维度各10分：任务拆解、Agent选择、后续判断、最终答案。理由使用简体中文。"
                            "必须只返回一个JSON对象，结构为："
                            '{"dimensions":['
                            '{"key":"decomposition","label":"任务拆解","score":0,"max_score":10,"reason":"理由"},'
                            '{"key":"routing","label":"Agent选择","score":0,"max_score":10,"reason":"理由"},'
                            '{"key":"follow_up","label":"后续判断","score":0,"max_score":10,"reason":"理由"},'
                            '{"key":"final_answer","label":"最终答案","score":0,"max_score":10,"reason":"理由"}],'
                            '"strengths":[],"deductions":[],"verdict":"结论"}。'
                            "四个key、label和max_score必须保持不变，score必须是0到10的整数，不得增加字段。"
                        ),
                    },
                    {
                        "role": "user", "source": "demo",
                        "content": json.dumps({"evidence": context, "feedback": feedback}, ensure_ascii=False),
                    },
                ], ModelEvaluation.model_json_schema())
                model_result = ModelEvaluation.model_validate(raw)
                break
            except (ValidationError, ValueError, LLMUnavailableError):
                continue

        result: dict[str, Any] = {
            "version": "agentteams-evaluation-v1",
            "evaluated_at": datetime.now(timezone.utc).isoformat(),
            "rule_score": rules.score,
            "rule_max_score": rules.max_score,
            "rule_dimensions": [item.model_dump() for item in rules.dimensions],
            "rule_deductions": rules.deductions,
        }
        if model_result is None:
            result.update({
                "model_status": "unavailable", "model_score": None,
                "model_max_score": 40, "model_dimensions": [],
                "model_strengths": [], "model_deductions": ["大模型评测暂不可用。"],
                "model_verdict": "", "total_score": None, "total_max_score": 100,
            })
            return result
        result.update({
            "model_status": "completed", "model_score": model_result.score,
            "model_max_score": 40,
            "model_dimensions": [item.model_dump() for item in model_result.dimensions],
            "model_strengths": model_result.strengths,
            "model_deductions": model_result.deductions,
            "model_verdict": model_result.verdict,
            "total_score": rules.score + model_result.score,
            "total_max_score": 100,
        })
        return result
