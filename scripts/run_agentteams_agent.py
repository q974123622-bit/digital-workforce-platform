"""Launch exactly one strict Matrix AgentTeams Agent process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
from app.services.agentteams_decisions import AgentDecisionEngine
from app.services.agentteams_gateway import AgentTeamsGateway
from app.services.agentteams_runtime_config import load_strict_runtime_config
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services.llm import DeepSeekProvider


_ROLES = {"manager", "hr", "it", "audit"}
_ROLE_SKILLS = {
    "manager": {"agentteams-onboarding-readiness"},
    "hr": {"hr-onboarding-assistant"}, "it": {"it-service-assistant"}, "audit": {"audit-evidence-review"},
}


def load_runtime_config(role: str, path: Path, *, env: dict[str, str] | None = None) -> tuple[AgentRuntimeConfig, str]:
    environment = os.environ if env is None else env
    if role not in _ROLES:
        raise ValueError("invalid_runtime_config")
    if not environment.get("DEEPSEEK_API_KEY"):
        raise ValueError("deepseek_key_missing")
    runtime = load_strict_runtime_config(path, environ=environment)
    agent = runtime.agents[role]
    trusted = {"platform": runtime.platform.mxid}
    trusted.update({identity.agent_id: identity.mxid for identity in runtime.agents.values()})
    allowed = set(_ROLE_SKILLS[role])
    if role == "manager":
        allowed.add("agentteams-onboarding-readiness")
    config = AgentRuntimeConfig(
        agent_id=agent.agent_id, role=role, matrix_mxid=agent.mxid, matrix_token=agent.token, room_id=runtime.room_id,
        trusted_sender_mxids=trusted, allowed_skill_ids=allowed, team_catalog=runtime.team_catalog,
        safe_tool_catalog=[], state_dir=path.parent / "state",
    )
    return config, runtime.matrix_url


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one independent Matrix AgentTeams Agent")
    parser.add_argument("--role", choices=sorted(_ROLES), required=True)
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        config, matrix_url = load_runtime_config(args.role, args.config)
        runtime = AgentRuntime(
            config, AgentTeamsGateway(base_url=matrix_url, matrix_token=config.matrix_token),
            AgentDecisionEngine(DeepSeekProvider(), ProjectSkillRegistry.from_root(ROOT)),
        )
    except ValueError as exc:
        print(str(exc) if str(exc) in {"invalid_runtime_config", "deepseek_key_missing"} else "invalid_runtime_config", file=sys.stderr)
        return 2
    try:
        if args.once:
            runtime.run_once()
        else:
            runtime.run_forever(threading.Event())
    except KeyboardInterrupt:
        return 0
    finally:
        runtime.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
