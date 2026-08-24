"""Harness-first 能力执行：Harness 驱动数字员工，Adapter 是受控工具调用。"""

from __future__ import annotations

import json
from dataclasses import dataclass

from .. import models
from . import config
from .adapters import run_adapter
from .capability_contract import plugin_contract
from .runtime_adapter import DockerHarnessRuntimeAdapter, HarnessExecutionContext, RuntimeAdapter


@dataclass(frozen=True)
class CapabilityExecution:
    data: dict
    runtime_mode: str  # harness | demo_adapter
    tool_name: str
    tool_type: str
    context_id: str
    runtime_summary: str = ""


def _harness_prompt(
    plugin: models.Plugin,
    params: dict,
    context: HarnessExecutionContext,
) -> str:
    visible_params = {key: value for key, value in params.items() if not key.startswith("_")}
    return (
        "你是数字员工平台中的独立数字员工执行实例。"
        "平台已经完成身份解析和 Policy 授权。你负责理解任务、结合协作结论形成执行计划，"
        "然后请求平台调用下方唯一声明的受控工具。不要声称工具已经执行，不要调用其他资源。\n\n"
        f"【Task ID】{context.task_id}\n"
        f"【员工工号】{context.employee_id}\n"
        f"【员工姓名】{context.employee_name}\n"
        f"【员工人设】{context.role_prompt}\n"
        f"【岗位职责】{context.responsibility}\n"
        f"【用户任务】{context.request}\n"
        f"【当前子任务】{context.subtask}\n"
        f"【AgentTeams 协作结论】{context.collaboration_summary or '无补充结论，按既定角色计划执行'}\n"
        f"【允许使用的工具】{plugin.name}（{plugin.id} / {plugin.type}）\n"
        f"【工具参数】{json.dumps(visible_params, ensure_ascii=False)}\n\n"
        "请用简洁中文输出：任务理解、将调用的工具、关键参数和风险提示。"
    )


def execute_capability(
    plugin: models.Plugin,
    params: dict,
    *,
    trace_id: str,
    context: HarnessExecutionContext,
    runtime: RuntimeAdapter | None = None,
) -> CapabilityExecution:
    """先运行员工 Harness，再由平台调用一次已授权 Adapter 工具。"""
    contract = plugin_contract(plugin)
    if not contract.ready:
        raise RuntimeError("；".join(contract.issues or ["能力契约未就绪"]))

    harness_enabled = runtime is not None or config.get("DWP_HARNESS_ENABLED") == "1"
    runtime_summary = ""
    runtime_mode = "demo_adapter"
    if harness_enabled:
        active_runtime = runtime or DockerHarnessRuntimeAdapter()
        result = active_runtime.run(
            employee_id=context.employee_id,
            task_prompt=_harness_prompt(plugin, params, context),
            trace_id=trace_id,
            context=context,
        )
        if result.ok:
            runtime_mode = "harness"
            runtime_summary = result.result
        else:
            runtime_summary = result.result or "Harness 不可用，已进入 Demo Adapter 降级"

    # 受控工具桥：Adapter 在整条授权链中只调用一次。
    return CapabilityExecution(
        data=run_adapter(plugin, params),
        runtime_mode=runtime_mode,
        tool_name=plugin.name,
        tool_type=plugin.type,
        context_id=context.context_id,
        runtime_summary=runtime_summary,
    )
