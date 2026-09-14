"""Launch exactly one strict Matrix AgentTeams Agent process."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
import sys
import threading
from typing import Mapping

ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
if str(BACKEND) not in sys.path:
    sys.path.insert(0, str(BACKEND))

from app.services.agentteams_agent_runtime import AgentRuntime, AgentRuntimeConfig
from app.services.agentteams_decisions import AgentDecisionEngine
from app.services.agentteams_gateway import AgentTeamsGateway
from app.services.agentteams_runtime_config import default_runtime_config_path, load_strict_runtime_config
from app.services.agentteams_skills import ProjectSkillRegistry
from app.services import config as service_config
from app.services.llm import DeepSeekProvider


_ROLES = {
    "manager", "hr", "it", "audit",
    "hr_material", "hr_training", "it_account", "it_device",
    "db_write", "db_lock", "db_vacuum", "db_index", "db_query",
}
_ROLE_SKILLS = {
    "manager": {
        "agentteams-onboarding-readiness",
        "agentteams-role-change-readiness",
        "agentteams-database-diagnosis",
    },
    "hr": {"hr-onboarding-assistant", "hr-role-change-assistant"},
    "it": {"it-service-assistant", "it-role-change-assistant"},
    "audit": {"audit-evidence-review", "audit-role-change-review"},
    "hr_material": {"hr-material-specialist"},
    "hr_training": {"hr-training-specialist"},
    "it_account": {"it-account-specialist"},
    "it_device": {"it-device-specialist"},
    "db_write": {"db-write-investigation"},
    "db_lock": {"db-lock-investigation"},
    "db_vacuum": {"db-vacuum-investigation"},
    "db_index": {"db-index-investigation"},
    "db_query": {"db-query-investigation"},
}
_ROLE_KEY_ENVIRONMENTS = {
    "manager": "DEEPSEEK_API_KEY_MANAGER",
    "hr": "DEEPSEEK_API_KEY_HR",
    "it": "DEEPSEEK_API_KEY_IT",
    "audit": "DEEPSEEK_API_KEY_AUDIT",
    "hr_material": "DEEPSEEK_API_KEY_HR",
    "hr_training": "DEEPSEEK_API_KEY_HR",
    "it_account": "DEEPSEEK_API_KEY_IT",
    "it_device": "DEEPSEEK_API_KEY_IT",
    "db_write": "DEEPSEEK_API_KEY_DB_WRITE",
    "db_lock": "DEEPSEEK_API_KEY_DB_LOCK",
    "db_vacuum": "DEEPSEEK_API_KEY_DB_VACUUM",
    "db_index": "DEEPSEEK_API_KEY_DB_INDEX",
    "db_query": "DEEPSEEK_API_KEY_DB_QUERY",
}


def role_skill_ids(role: str) -> set[str]:
    try:
        return set(_ROLE_SKILLS[role])
    except KeyError:
        raise ValueError("invalid_runtime_config") from None


def resolve_role_api_key(role: str, env: Mapping[str, str] | None = None) -> str:
    """Resolve only this process's role-specific key; no shared-key fallback."""
    environment = os.environ if env is None else env
    variable = _ROLE_KEY_ENVIRONMENTS.get(role)
    value = environment.get(variable, "") if variable else ""
    if not isinstance(value, str) or not value.strip():
        raise ValueError("deepseek_key_missing")
    return value.strip()


def load_runtime_config(role: str, path: Path | None = None, *, env: dict[str, str] | None = None) -> tuple[AgentRuntimeConfig, str]:
    environment = os.environ if env is None else env
    if role not in _ROLES:
        raise ValueError("invalid_runtime_config")
    resolve_role_api_key(role, environment)
    authority = default_runtime_config_path(environment)
    selected = authority if path is None else Path(path)
    if os.path.normcase(os.path.abspath(selected)) != os.path.normcase(os.path.abspath(authority)):
        raise ValueError("invalid_runtime_config")
    runtime = load_strict_runtime_config(selected, environ=environment)
    agent = runtime.agents[role]
    trusted = {"platform": runtime.platform.mxid}
    trusted.update({identity.agent_id: identity.mxid for identity in runtime.agents.values()})
    config = AgentRuntimeConfig(
        agent_id=agent.agent_id, role=role, matrix_mxid=agent.mxid, matrix_token=agent.token, room_id=runtime.room_id,
        trusted_sender_mxids=trusted, allowed_skill_ids=set(agent.allowed_skill_ids), team_catalog=runtime.team_catalog,
        safe_tool_catalog=list(agent.safe_tool_catalog), state_dir=selected.parent / "state",
    )
    return config, runtime.matrix_url


def build_decision_engine(api_key: str, root: Path = ROOT) -> AgentDecisionEngine:
    """Build the strict AgentTeams decision engine with trace-capable thinking enabled."""
    return build_decision_engines(api_key, root)[0]


def build_decision_engines(
    api_key: str, root: Path = ROOT
) -> tuple[AgentDecisionEngine, AgentDecisionEngine]:
    """Build isolated engines so each task can choose its model thinking mode."""
    model = service_config.agentteams_worker_model()
    skills = ProjectSkillRegistry.from_root(root)
    thinking_engine = AgentDecisionEngine(
        DeepSeekProvider(api_key=api_key, model=model, thinking=True),
        skills,
    )
    non_thinking_engine = AgentDecisionEngine(
        DeepSeekProvider(
            api_key=api_key,
            model=model,
            thinking=False,
            reasoning_effort="low",
        ),
        skills,
    )
    return thinking_engine, non_thinking_engine


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one independent Matrix AgentTeams Agent")
    parser.add_argument("--role", choices=sorted(_ROLES), required=True)
    parser.add_argument("--config", type=Path, default=None)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args(argv)
    try:
        api_key = resolve_role_api_key(args.role)
        config, matrix_url = load_runtime_config(args.role, args.config)
        thinking_engine, non_thinking_engine = build_decision_engines(api_key)
        runtime = AgentRuntime(
            config, AgentTeamsGateway(base_url=matrix_url, matrix_token=config.matrix_token),
            thinking_engine,
            non_thinking_decisions=non_thinking_engine,
        )
    except ValueError as exc:
        print(str(exc) if str(exc) in {"invalid_runtime_config", "deepseek_key_missing"} else "invalid_runtime_config", file=sys.stderr)
        return 2
    try:
        runtime.initialize_matrix_cursor()
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
