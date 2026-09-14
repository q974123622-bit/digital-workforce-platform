from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

MESSAGE_TYPES = Literal[
    "task.submit",
    "task.delegate",
    "task.assign",
    "task.accepted",
    "task.progress",
    "agent.message",
    "tool.request",
    "tool.result",
    "task.result",
    "task.result.submitted",
    "task.result.accepted",
    "task.result.rejected",
    "task.failed",
    "task.summary",
    "task.cancel",
]
PREFIX = "DWP_EVENT "


class AgentProtocolMessage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal["1.0"]
    message_id: str = Field(min_length=1)
    type: MESSAGE_TYPES
    root_task_id: str = Field(min_length=1)
    task_id: str = Field(min_length=1)
    parent_task_id: str | None = None
    trace_id: str = Field(min_length=1)
    sender: str = Field(min_length=1)
    receiver: str = Field(min_length=1)
    timestamp: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    payload: dict[str, Any] = Field(default_factory=dict)


def render_protocol_line(message: AgentProtocolMessage) -> str:
    return PREFIX + message.model_dump_json()


def parse_protocol_lines(body: str) -> list[AgentProtocolMessage]:
    parsed: list[AgentProtocolMessage] = []
    for line in body.splitlines():
        if line.startswith(PREFIX):
            parsed.append(AgentProtocolMessage.model_validate(json.loads(line[len(PREFIX) :])))
    return parsed
