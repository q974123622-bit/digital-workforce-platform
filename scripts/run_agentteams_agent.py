"""Launch exactly one strict Matrix AgentTeams Agent process."""

from __future__ import annotations

import argparse
import json
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
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        agent = data["agents"][role]
        matrix_url, room_id = data["matrix_url"], data["room_id"]
    except (OSError, ValueError, KeyError, TypeError):
        raise ValueError("invalid_runtime_config") from None
    token = agent.get("token")
    token_env = agent.get("token_env")
    if token is None and isinstance(token_env, str) and token_env:
        token = environment.get(token_env)
    if not all(isinstance(value, str) and value for value in (matrix_url, room_id, agent.get("agent_id"), agent.get("mxid"), token)):
        raise ValueError("invalid_runtime_config")
    if not agent["agent_id"] or not agent["mxid"].startswith("@"):
        raise ValueError("invalid_runtime_config")
    trusted = {"platform": _platform_mxid(data)}
    for candidate_role, candidate in data["agents"].items():
        if candidate_role not in _ROLES or not isinstance(candidate, dict) or not isinstance(candidate.get("agent_id"), str) or not candidate.get("agent_id") or not isinstance(candidate.get("mxid"), str) or not candidate["mxid"].startswith("@"):
            raise ValueError("invalid_runtime_config")
        trusted[candidate["agent_id"]] = candidate["mxid"]
    allowed = set(agent.get("allowed_skill_ids") or _ROLE_SKILLS[role])
    if role == "manager":
        allowed.add("agentteams-onboarding-readiness")
    config = AgentRuntimeConfig(
        agent_id=agent["agent_id"], role=role, matrix_mxid=agent["mxid"], matrix_token=token, room_id=room_id,
        trusted_sender_mxids=trusted, allowed_skill_ids=allowed, team_catalog=_team_catalog(data),
        safe_tool_catalog=list(agent.get("safe_tool_catalog") or []), state_dir=ROOT / ".agentteams-runtime" / "state",
    )
    return config, matrix_url


def _platform_mxid(data: dict) -> str:
    platform = data.get("platform")
    mxid = platform.get("mxid") if isinstance(platform, dict) else data.get("platform_mxid")
    if not isinstance(mxid, str) or not mxid.startswith("@"):
        raise ValueError("invalid_runtime_config")
    return mxid


def _team_catalog(data: dict) -> dict:
    raw = data.get("team_catalog", {})
    if not isinstance(raw, dict):
        raise ValueError("invalid_runtime_config")
    return raw


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
