"""Read/write API for persisted AgentTeams collaboration tasks.

The API deliberately reads the local durable collaboration state.  It never
uses a transport message as the query source of truth, and it only permits task
submission after the selected mode's readiness checks have succeeded.
"""

from __future__ import annotations

from datetime import datetime, timezone
import hmac
import json
import os
from pathlib import Path
import stat
from threading import Lock
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from pydantic import ValidationError

from .. import models, schemas
from ..database import get_db
from ..services import config
from ..services.agentteams_collaboration import (
    AgentTeamsCollaborationService,
    CollaborationConflict,
    matrix_user_id_for_runtime,
)
from ..services.agentteams_gateway import AgentTeamsGateway
from ..services.agentteams_agent_runtime import heartbeat_signature
from ..services.agentteams_decisions import (
    AgentDecisionEngine,
    AgentDecisionError,
    TeamSelectionContext,
)
from ..services.agentteams_database_collaboration import (
    DATABASE_TEAM_ID,
    enqueue_database_collaboration_task,
)
from ..services.agentteams_database_scenarios import DatabaseScenarioCatalog
from ..services.agentteams_evaluation import AgentTeamsEvaluator
from ..services.agentteams_runtime_config import default_runtime_config_path, load_strict_runtime_config
from ..services.agentteams_skills import ProjectSkillRegistry
from ..services.agentteams_team_selection import build_manager_team_catalog
from ..services.llm import DeepSeekProvider, LLMUnavailableError
from ..services.agentteams_local_scenario import (
    LOCAL_DEMO_INSTRUCTION,
    LOCAL_DEMO_LEADER_ID,
    LOCAL_DEMO_MATERIAL,
    LOCAL_DEMO_TEAM_ID,
    LOCAL_DEMO_TITLE,
    LOCAL_DEMO_WORKER_IDS,
)


router = APIRouter(prefix="/agentteams-collaboration", tags=["agentteams-collaboration"])

_TERMINAL_STATUSES = {
    "completed", "failed", "timed_out", "cancelled", "revision_requested"
}
_LOCAL_DEMO_TEAM_ID = LOCAL_DEMO_TEAM_ID
_LOCAL_DEMO_LEADER_ID = LOCAL_DEMO_LEADER_ID
_LOCAL_DEMO_WORKER_IDS = LOCAL_DEMO_WORKER_IDS
_EVALUATION_LOCKS_GUARD = Lock()
_EVALUATION_LOCKS: dict[str, Lock] = {}
_PROJECT_ROOT = Path(__file__).resolve().parents[3]


def _evaluation_lock(task_id: str) -> Lock:
    """Serialize one task's model call in this demo backend process."""
    with _EVALUATION_LOCKS_GUARD:
        return _EVALUATION_LOCKS.setdefault(task_id, Lock())


def _configured(value: str | None) -> bool:
    return bool(value and value.strip())


def _worker_health(db: Session) -> list[dict]:
    """Report stored worker bindings without claiming their runtimes are live."""
    workers = list(
        db.scalars(
            select(models.DigitalEmployee)
            .where(models.DigitalEmployee.runtime_type == "agentteams")
            .order_by(models.DigitalEmployee.employee_no)
        )
    )
    result = []
    for worker in workers:
        runtime_ref = (worker.runtime_ref or "").strip()
        matrix_user_id = matrix_user_id_for_runtime(runtime_ref) or ""
        if not runtime_ref:
            status = "missing_runtime_ref"
        elif not matrix_user_id:
            status = "invalid_matrix_user_id"
        else:
            status = "configured"
        result.append({
            "agent_id": worker.employee_no,
            "matrix_user_id": matrix_user_id,
            # A database binding is not proof that its remote AgentTeams worker
            # is connected; keep that distinction visible to API consumers.
            "status": status,
        })
    return result


def _normalise_health(raw_health: dict) -> dict:
    """Derive safe readiness facts rather than trusting a caller-provided flag.

    ``inspect_environment`` normally returns these facts itself, but both the
    public health endpoint and submission gate pass through this function.  It
    therefore remains impossible for a test double or future caller to bypass
    the submission gate by merely setting ``ready_for_demo`` to ``True``.
    """
    # The process configuration is authoritative. A caller-provided mode must
    # never downgrade strict readiness into the credential-free local branch.
    mode = config.agentteams_mode()
    raw_workers = raw_health.get("workers")
    workers = []
    if isinstance(raw_workers, list):
        for raw_worker in raw_workers:
            if not isinstance(raw_worker, dict):
                continue
            agent_id = str(raw_worker.get("agent_id") or "").strip()
            matrix_user_id = str(raw_worker.get("matrix_user_id") or "").strip()
            raw_status = str(raw_worker.get("status") or "").strip()
            is_exact_mxid = (
                matrix_user_id.startswith("@")
                and matrix_user_id_for_runtime(matrix_user_id) == matrix_user_id
            )
            # The local transport has no Matrix session.  Its readiness is
            # established by the fixed, persisted Team membership below, so
            # it must not reject otherwise valid local workers because the
            # database happens to hold a strict-mode Matrix binding.
            identity_is_valid = (
                agent_id in _LOCAL_DEMO_WORKER_IDS
                if mode == "local_demo"
                else is_exact_mxid
            )
            if raw_status in {"configured", "ready"} and agent_id and identity_is_valid:
                status = "ready" if mode == "agentteams_strict" and raw_status == "ready" else "configured"
            elif (
                mode == "agentteams_strict"
                and agent_id
                and identity_is_valid
                and raw_status in {"stale_heartbeat", "unavailable", "invalid_runtime_binding"}
            ):
                status = raw_status
            elif not raw_worker.get("matrix_user_id"):
                status = "missing_runtime_ref"
            else:
                status = "invalid_matrix_user_id"
            workers.append({
                "agent_id": agent_id,
                "matrix_user_id": matrix_user_id,
                "status": status,
                **({"last_heartbeat_at": raw_worker["last_heartbeat_at"]}
                   if isinstance(raw_worker.get("last_heartbeat_at"), str) else {}),
            })

    enabled = raw_health.get("enabled") is True
    matrix = str(raw_health.get("matrix") or "not_configured")
    room = str(raw_health.get("room") or "not_configured")
    raw_manager = str(raw_health.get("manager") or "not_configured")
    if mode == "local_demo":
        manager = "configured" if raw_manager in {"configured", "ready"} else "not_configured"
    elif raw_manager in {"ready", "configured", "stale_heartbeat", "unavailable", "invalid_runtime_binding"}:
        manager = raw_manager
    else:
        manager = "not_configured"
    platform_team = "ready" if raw_health.get("platform_team") == "ready" else "missing"

    configured_workers = [
        worker for worker in workers
        if worker["status"] == "configured"
    ]
    worker_ids = {worker["agent_id"] for worker in configured_workers}
    worker_mxids = {worker["matrix_user_id"] for worker in configured_workers}
    workers_ready = (
        _LOCAL_DEMO_WORKER_IDS <= worker_ids
        if mode == "local_demo"
        else raw_health.get("strict_ready") is True
    )

    reasons = []
    if not enabled:
        reasons.append("AgentTeams协作功能未启用")
    if mode == "agentteams_strict" and matrix != "ready":
        reasons.append("Matrix登录未成功")
    if mode == "agentteams_strict" and room != "ready":
        reasons.append("配置的Matrix房间未加入")
    manager_expected_status = "configured" if mode == "local_demo" else "ready"
    if manager != manager_expected_status:
        if mode == "local_demo":
            reasons.append("本地Demo指定Leader未就绪")
        elif manager == "stale_heartbeat":
            reasons.append("Manager未运行或心跳已过期")
        elif manager == "invalid_runtime_binding":
            reasons.append("Manager平台绑定与运行配置不一致")
        elif manager == "unavailable":
            reasons.append("Manager的Matrix登录或运行状态不可用")
        elif manager == "configured":
            reasons.append("Manager仅完成配置，尚未通过运行验证")
        else:
            reasons.append("Manager MXID未配置")
    if platform_team != "ready":
        reasons.append(
            "本地Demo seed团队不存在"
            if mode == "local_demo"
            else "平台至少需要一个Team"
        )
    if not workers_ready:
        if mode == "local_demo":
            reasons.append("本地Demo团队至少需要两名指定Worker")
        else:
            strict_worker_reasons = []
            for worker in workers:
                agent_id = worker["agent_id"] or "未知Worker"
                status = worker["status"]
                if status == "stale_heartbeat":
                    strict_worker_reasons.append(f"{agent_id}未运行或心跳已过期")
                elif status == "invalid_runtime_binding":
                    strict_worker_reasons.append(f"{agent_id}平台绑定与运行配置不一致")
                elif status == "unavailable":
                    strict_worker_reasons.append(f"{agent_id}的Matrix登录或运行状态不可用")
                elif status == "missing_runtime_ref":
                    strict_worker_reasons.append(f"{agent_id}缺少运行时绑定")
                elif status == "invalid_matrix_user_id":
                    strict_worker_reasons.append(f"{agent_id}的Matrix ID无效")
            reasons.extend(strict_worker_reasons or ["严格模式Worker尚未全部就绪"])

    return {
        "enabled": enabled,
        "ready_for_demo": not reasons,
        "reason": "；".join(reasons),
        "mode": mode,
        "transport": "local" if mode == "local_demo" else "matrix",
        "matrix": "not_connected" if mode == "local_demo" else matrix,
        "room": room,
        # Presence is the only Manager fact available here; it is not a
        # remote connection assertion.
        "manager": manager,
        # Health has no user-selected team.  This is intentionally a system
        # check; the submitted team remains validated by create_task.
        "platform_team": platform_team,
        "workers": workers,
        "strict_ready": raw_health.get("strict_ready") is True,
        "configured_team_id": str(raw_health.get("configured_team_id") or ""),
    }


def _local_demo_health(db: Session) -> dict:
    """Build local readiness only from canonical persisted seed records."""
    enabled = config.agentteams_collaboration_enabled()
    team = db.get(models.Team, _LOCAL_DEMO_TEAM_ID)
    member_ids = set()
    if team is not None:
        member_ids = set(db.scalars(
            select(models.TeamMember.employee_id).where(
                models.TeamMember.team_id == team.id
            )
        ))
    leader = db.get(models.DigitalEmployee, _LOCAL_DEMO_LEADER_ID)
    leader_ready = (
        team is not None
        and team.leader_employee_id == _LOCAL_DEMO_LEADER_ID
        and leader is not None
        and leader.status == "active"
        and _LOCAL_DEMO_LEADER_ID in member_ids
    )
    workers = []
    for worker_id in sorted(_LOCAL_DEMO_WORKER_IDS):
        worker = db.get(models.DigitalEmployee, worker_id)
        worker_ready = (
            worker is not None
            and worker.status == "active"
            and worker_id in member_ids
        )
        workers.append({
            "agent_id": worker_id,
            "matrix_user_id": "",
            "status": "configured" if worker_ready else "missing",
        })
    return _normalise_health({
        "enabled": enabled,
        "matrix": "not_connected",
        "room": "local",
        "manager": "configured" if leader_ready else "not_configured",
        "platform_team": "ready" if team is not None else "missing",
        "workers": workers,
    })


def _heartbeat_max_age_seconds() -> int:
    try:
        return max(5, min(int(os.environ.get("DWP_AGENTTEAMS_HEARTBEAT_MAX_AGE_SECONDS", "30")), 300))
    except ValueError:
        return 30


def _state_heartbeat(state_dir: Path, identity, *, now: datetime) -> str | None:
    """Read one bounded regular state file; malformed/old state is not live."""
    path = state_dir / f"{identity.agent_id}.json"
    try:
        if path.is_symlink():
            return None
        metadata = path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 16_384:
            return None
        value = json.loads(path.read_text(encoding="utf-8"))
        heartbeat = value.get("heartbeat_at") if isinstance(value, dict) else None
        if not isinstance(value, dict) or value.get("agent_id") != identity.agent_id or value.get("mxid") != identity.mxid:
            return None
        signature = value.get("heartbeat_signature")
        expected = heartbeat_signature(identity.agent_id, identity.mxid, heartbeat, identity.token) if isinstance(heartbeat, str) else ""
        if not isinstance(signature, str) or not hmac.compare_digest(signature, expected):
            return None
        parsed = datetime.fromisoformat(heartbeat) if isinstance(heartbeat, str) else None
        if parsed is None or parsed.tzinfo is None:
            return None
        age = (now - parsed.astimezone(timezone.utc)).total_seconds()
        if age < -5 or age > _heartbeat_max_age_seconds():
            return None
        return parsed.astimezone(timezone.utc).isoformat()
    except (OSError, ValueError, TypeError):
        return None


def _strict_environment(db: Session, enabled: bool) -> dict:
    """Verify five independent configured identities without exposing credentials."""
    try:
        runtime = load_strict_runtime_config()
    except ValueError:
        return _normalise_health({"enabled": enabled, "matrix": "not_configured", "room": "not_configured", "manager": "not_configured", "platform_team": "missing", "workers": [], "strict_ready": False})
    expected_members = {runtime.platform.mxid, *(identity.mxid for identity in runtime.agents.values())}
    verified: dict[str, bool] = {}
    members: set[str] = set()
    gateways = []
    try:
        identities = [("platform", runtime.platform), *runtime.agents.items()]
        for role, identity in identities:
            gateway = AgentTeamsGateway(base_url=runtime.matrix_url, matrix_token=identity.token)
            gateways.append((role, gateway))
            verified[role] = gateway.whoami() == identity.mxid
        platform_gateway = next(gateway for role, gateway in gateways if role == "platform")
        members = platform_gateway.joined_members(runtime.room_id)
    except Exception:
        verified = {}
        members = set()
    finally:
        for _role, gateway in gateways:
            try:
                gateway.close()
            except Exception:
                pass
    team = db.get(models.Team, runtime.team_id)
    member_ids = set(db.scalars(select(models.TeamMember.employee_id).where(models.TeamMember.team_id == runtime.team_id))) if team is not None else set()
    state_root = default_runtime_config_path().parent / "state"
    now = datetime.now(timezone.utc)
    workers = []
    team_bound = team is not None and team.leader_employee_id == runtime.agents["manager"].agent_id
    for role, identity in runtime.agents.items():
        employee = db.get(models.DigitalEmployee, identity.agent_id)
        bound = bool(employee and employee.status == "active" and employee.runtime_type == "agentteams" and identity.agent_id in member_ids and matrix_user_id_for_runtime(employee.runtime_ref) == identity.mxid)
        heartbeat = _state_heartbeat(state_root, identity, now=now)
        ready = bool(verified.get(role) and bound and heartbeat)
        if ready:
            status = "ready"
        elif not verified.get(role):
            status = "unavailable"
        elif not bound:
            status = "invalid_runtime_binding"
        else:
            status = "stale_heartbeat"
        team_bound = team_bound and bound
        workers.append({"agent_id": identity.agent_id, "matrix_user_id": identity.mxid, "status": status, **({"last_heartbeat_at": heartbeat} if heartbeat else {})})
    strict_ready = bool(enabled and verified.get("platform") and all(verified.get(role) for role in runtime.agents) and expected_members <= members and team_bound and all(item["status"] == "ready" for item in workers))
    manager_worker = next(
        (item for item in workers if item["agent_id"] == runtime.agents["manager"].agent_id),
        None,
    )
    manager_status = manager_worker["status"] if manager_worker is not None else "not_configured"
    return _normalise_health({"enabled": enabled, "matrix": "ready" if verified.get("platform") else "unavailable", "room": "ready" if expected_members <= members else "unavailable", "manager": manager_status, "platform_team": "ready" if team_bound else "missing", "workers": workers, "strict_ready": strict_ready, "configured_team_id": runtime.team_id})


def inspect_environment(db: Session) -> dict:
    """Perform a bounded, truthful readiness check for task submission.

    Local mode checks only its fixed seeded team and protocol identities. Strict
    mode returns Matrix/room ``ready`` only after the gateway's joined-room check
    succeeds; Manager and worker values remain configuration facts rather than
    claims that their remote runtimes are live.
    """
    enabled = config.agentteams_collaboration_enabled()
    mode = config.agentteams_mode()
    if mode == "local_demo":
        return _local_demo_health(db)
    return _strict_environment(db, enabled)


def _service() -> AgentTeamsCollaborationService:
    return AgentTeamsCollaborationService(
        max_depth=config.agentteams_max_depth(),
        max_children=config.agentteams_max_children(),
    )


def _team_selection_engine() -> AgentDecisionEngine:
    api_key = config.get("DEEPSEEK_API_KEY_MANAGER") or config.get("DEEPSEEK_API_KEY")
    return AgentDecisionEngine(
        DeepSeekProvider(api_key=api_key),
        ProjectSkillRegistry.from_root(_PROJECT_ROOT),
    )


def _build_evaluator() -> AgentTeamsEvaluator:
    api_key = config.get("DEEPSEEK_API_KEY_MANAGER") or config.get("DEEPSEEK_API_KEY")
    return AgentTeamsEvaluator(DeepSeekProvider(api_key=api_key))


def _evaluation_demo_scenario(root: models.AgentTaskNode, selected_skill_ids: list[str]) -> tuple[str, str]:
    input_payload = root.input_payload if isinstance(root.input_payload, dict) else {}
    # ``coordination`` is generated by the platform. Any other root input key
    # came from task material and is outside the outbound Demo evaluation boundary.
    if set(input_payload) - {"coordination"}:
        raise HTTPException(status_code=422, detail="仅支持不含用户材料的AgentTeams虚构演示任务评测")
    selected = set(selected_skill_ids)
    if (
        root.title == "岗位调整评估"
        and root.instruction == "评估张明的岗位调整是否可以按期完成。"
        and selected == {"agentteams-role-change-readiness"}
    ):
        return "role_change", "评估演示员工的岗位调整是否可以按期完成"
    if (
        root.title == LOCAL_DEMO_TITLE
        and root.instruction == LOCAL_DEMO_INSTRUCTION
        and selected == {"agentteams-onboarding-readiness"}
    ):
        return "onboarding", "评估演示员工是否可以按期入职"
    raise HTTPException(status_code=422, detail="仅支持已登记的AgentTeams虚构演示任务评测")


def _require_actor(db: Session, actor_no: str) -> models.HumanEmployee:
    """Require the same trusted human caller identity used by workplace APIs."""
    actor = db.get(models.HumanEmployee, actor_no)
    if actor is None:
        raise HTTPException(status_code=404, detail="员工不存在")
    if actor.status != "active":
        raise HTTPException(status_code=403, detail="员工当前未启用")
    return actor


def _node_out(node: models.AgentTaskNode) -> schemas.AgentTaskNodeOut:
    return schemas.AgentTaskNodeOut.model_validate(node)


def _task_nodes(db: Session, root_task_id: str) -> list[models.AgentTaskNode]:
    return list(
        db.scalars(
            select(models.AgentTaskNode)
            .where(models.AgentTaskNode.root_task_id == root_task_id)
            .order_by(models.AgentTaskNode.depth, models.AgentTaskNode.created_at, models.AgentTaskNode.id)
        )
    )


def _require_root_task(db: Session, task_id: str) -> tuple[models.TaskRun, models.AgentTaskNode]:
    """Reject children, unrelated TaskRuns, and non-AgentTeams roots alike."""
    run = db.get(models.TaskRun, task_id)
    root = db.get(models.AgentTaskNode, task_id)
    if (
        run is None
        or run.source != "agentteams"
        or root is None
        or root.id != task_id
        or root.root_task_id != task_id
        or root.parent_task_id is not None
    ):
        raise HTTPException(status_code=404, detail="协作任务不存在")
    return run, root


def _require_task_access(
    db: Session, task_id: str, actor_no: str
) -> tuple[models.HumanEmployee, models.TaskRun, models.AgentTaskNode]:
    actor = _require_actor(db, actor_no)
    run, root = _require_root_task(db, task_id)
    if run.owner_human_no != actor.employee_no:
        raise HTTPException(status_code=403, detail="无权访问该协作任务")
    return actor, run, root


def _task_out(
    db: Session, run: models.TaskRun, root: models.AgentTaskNode | None = None
) -> schemas.AgentCollaborationTaskOut:
    root = root or _require_root_task(db, run.id)[1]
    latest_event_id = db.scalar(
        select(func.max(models.AgentTaskEvent.id)).where(models.AgentTaskEvent.root_task_id == run.id)
    ) or 0
    return schemas.AgentCollaborationTaskOut(
        id=run.id,
        team_id=run.team_id,
        trace_id=run.trace_id,
        title=root.title,
        instruction=root.instruction,
        status=root.status,
        source="agentteams",
        summary=run.summary,
        nodes=[_node_out(node) for node in _task_nodes(db, run.id)],
        latest_event_id=latest_event_id,
        created_at=run.created_at,
    )


def _require_ready(db: Session, *, team_id: str) -> None:
    if team_id == DATABASE_TEAM_ID:
        team = db.get(models.Team, DATABASE_TEAM_ID)
        member_count = db.scalar(
            select(func.count(models.TeamMember.id)).where(
                models.TeamMember.team_id == DATABASE_TEAM_ID
            )
        ) or 0
        if config.agentteams_collaboration_enabled() and team is not None and member_count == 6:
            return
        raise HTTPException(
            status_code=503,
            detail={"message": "数据库 AgentTeams 场景未就绪"},
        )
    health = (
        _local_demo_health(db)
        if config.agentteams_mode() == "local_demo"
        else _normalise_health(inspect_environment(db))
    )
    if health.get("ready_for_demo") is not True or (
        config.agentteams_mode() == "agentteams_strict"
        and health.get("configured_team_id") != team_id
    ):
        raise HTTPException(
            status_code=503,
            detail={"message": "AgentTeams 协作环境未就绪", "health": health},
        )


def _require_supported_local_scenario(
    team_id: str,
    leader_agent_id: str,
    title: str,
    instruction: str,
    material: dict,
) -> None:
    if config.agentteams_mode() != "local_demo" or team_id == DATABASE_TEAM_ID:
        return
    if (team_id, leader_agent_id) != (_LOCAL_DEMO_TEAM_ID, _LOCAL_DEMO_LEADER_ID):
        raise HTTPException(
            status_code=409,
            detail=(
                "local_demo仅支持固定场景："
                f"{_LOCAL_DEMO_TEAM_ID} / {_LOCAL_DEMO_LEADER_ID}"
            ),
        )
    if (
        title != LOCAL_DEMO_TITLE
        or instruction != LOCAL_DEMO_INSTRUCTION
        or material != LOCAL_DEMO_MATERIAL
    ):
        raise HTTPException(
            status_code=409,
            detail="local_demo仅支持固定入职演示剧本；输入不驱动通用Agent推理",
        )


def _commit_or_conflict(db: Session, action) -> models.TaskRun:
    try:
        run = action()
        db.commit()
        return run
    except CollaborationConflict as exc:
        db.rollback()
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/health", response_model=schemas.AgentCollaborationHealthOut)
def health(db: Session = Depends(get_db)):
    return _normalise_health(inspect_environment(db))


@router.post("/tasks", response_model=schemas.AgentCollaborationTaskOut, status_code=201)
def create_task(
    payload: schemas.AgentCollaborationTaskCreateIn,
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    actor = _require_actor(db, actor_no)
    if (payload.team_id is None) != (payload.leader_agent_id is None):
        raise HTTPException(status_code=422, detail="team_id和leader_agent_id必须同时提供")
    selection_mode = "manual"
    selection_reason = "调用方显式指定团队。"
    team_id = payload.team_id
    leader_agent_id = payload.leader_agent_id
    if team_id is None:
        catalog = build_manager_team_catalog(db)
        if not catalog:
            raise HTTPException(status_code=409, detail="没有可用的AgentTeams团队")
        # The credential-free local demo has one checked-in business script.
        # Keep it deterministic while the strict/general path uses the Manager model.
        fixed_local_candidate = next(
            (item for item in catalog if item["team_id"] == _LOCAL_DEMO_TEAM_ID),
            None,
        )
        if (
            config.agentteams_mode() == "local_demo"
            and payload.title == LOCAL_DEMO_TITLE
            and payload.instruction == LOCAL_DEMO_INSTRUCTION
            and payload.material == LOCAL_DEMO_MATERIAL
            and fixed_local_candidate is not None
        ):
            team_id = fixed_local_candidate["team_id"]
            leader_agent_id = fixed_local_candidate["leader_agent_id"]
            selection_reason = "本地演示任务与预置入职团队职责匹配。"
        else:
            try:
                decision = _team_selection_engine().select_team(TeamSelectionContext(
                    user_goal=f"{payload.title}\n{payload.instruction}",
                    team_catalog=catalog,
                ))
            except (AgentDecisionError, LLMUnavailableError) as exc:
                raise HTTPException(status_code=503, detail="Manager团队选择失败") from exc
            if decision.action != "delegate":
                raise HTTPException(status_code=409, detail=decision.reason)
            team_id = decision.team_id
            leader_agent_id = decision.leader_agent_id
            selection_reason = decision.reason
        selection_mode = "manager"
    assert team_id is not None and leader_agent_id is not None
    team = db.get(models.Team, team_id)
    if team is None:
        raise HTTPException(status_code=404, detail="团队不存在")
    if leader_agent_id != team.leader_employee_id:
        raise HTTPException(status_code=403, detail="协作任务必须使用团队指定负责人")
    database_scenario_id = ""
    if team_id == DATABASE_TEAM_ID:
        database_scenario_id = str(payload.material.get("scenario_id") or "").strip()
        try:
            DatabaseScenarioCatalog.from_root(_PROJECT_ROOT).get(database_scenario_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="请选择有效的数据库诊断场景") from None
    _require_supported_local_scenario(
        team_id,
        leader_agent_id,
        payload.title,
        payload.instruction,
        payload.material,
    )
    _require_ready(db, team_id=team_id)
    service = (
        AgentTeamsCollaborationService(max_children=5, capability_catalog_source="database")
        if team_id == DATABASE_TEAM_ID
        else _service()
    )

    def create_for_actor() -> models.TaskRun:
        run = service.create_task(
            db,
            team_id=team_id,
            leader_agent_id=leader_agent_id,
            title=payload.title,
            instruction=payload.instruction,
            material=payload.material,
            owner_human_no=actor.employee_no,
            thinking_enabled=payload.thinking_enabled,
            team_selection=(
                {
                    "team_id": team_id,
                    "leader_agent_id": leader_agent_id,
                    "reason": selection_reason,
                    "selection_mode": selection_mode,
                }
                if selection_mode == "manager"
                else None
            ),
        )
        return run

    run = _commit_or_conflict(db, create_for_actor)
    if team_id == DATABASE_TEAM_ID:
        enqueue_database_collaboration_task(run.id, database_scenario_id)
    return _task_out(db, run)


@router.get("/tasks", response_model=list[schemas.AgentCollaborationTaskOut])
def list_tasks(actor_no: str = Query(...), db: Session = Depends(get_db)):
    actor = _require_actor(db, actor_no)
    runs = list(
        db.scalars(
            select(models.TaskRun)
            .where(
                models.TaskRun.source == "agentteams",
                models.TaskRun.owner_human_no == actor.employee_no,
            )
            .order_by(models.TaskRun.created_at.desc(), models.TaskRun.id.desc())
        )
    )
    return [_task_out(db, run) for run in runs]


@router.get("/tasks/{task_id}", response_model=schemas.AgentCollaborationTaskOut)
def get_task(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    _, run, root = _require_task_access(db, task_id, actor_no)
    return _task_out(db, run, root)


@router.get("/tasks/{task_id}/graph", response_model=schemas.AgentTaskGraphOut)
def get_graph(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    _require_task_access(db, task_id, actor_no)
    graph = _service().build_graph(db, task_id)
    return schemas.AgentTaskGraphOut(
        current_round=graph["current_round"],
        selected_skill_ids=graph["selected_skill_ids"],
        nodes=[_node_out(node) for node in _task_nodes(db, task_id)],
        parent_edges=graph["parent_edges"],
        dependency_edges=graph["dependency_edges"],
        communication_edges=graph["communication_edges"],
    )


@router.get("/tasks/{task_id}/events", response_model=list[schemas.AgentTaskEventOut])
def get_events(
    task_id: str,
    after_event_id: int | None = Query(default=None, ge=0),
    actor_no: str = Query(...),
    db: Session = Depends(get_db),
):
    _require_task_access(db, task_id, actor_no)
    statement = select(models.AgentTaskEvent).where(models.AgentTaskEvent.root_task_id == task_id)
    if after_event_id is not None:
        statement = statement.where(models.AgentTaskEvent.id > after_event_id)
    return list(db.scalars(statement.order_by(models.AgentTaskEvent.id)))


@router.get("/tasks/{task_id}/messages", response_model=list[schemas.AgentMessageOut])
def get_messages(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    _require_task_access(db, task_id, actor_no)
    return list(
        db.scalars(
            select(models.AgentMessage)
            .where(models.AgentMessage.root_task_id == task_id)
            .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
        )
    )


@router.get("/tasks/{task_id}/result", response_model=schemas.AgentFinalResultOut)
def get_result(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    _, run, root = _require_task_access(db, task_id, actor_no)
    return schemas.AgentFinalResultOut(
        task_id=run.id,
        status=root.status,
        summary=run.summary,
        node_results=[_node_out(node) for node in _task_nodes(db, run.id)],
    )


@router.post("/tasks/{task_id}/evaluation", response_model=schemas.AgentTaskEvaluationOut)
def evaluate_task(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    actor, run, root = _require_task_access(db, task_id, actor_no)
    if run.status not in _TERMINAL_STATUSES or root.status not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="协作任务结束后才能评测")

    with _evaluation_lock(task_id):
        cached = db.scalar(
            select(models.AgentTaskEvent)
            .where(
                models.AgentTaskEvent.root_task_id == task_id,
                models.AgentTaskEvent.event_type == "task.evaluation.completed",
            )
            .order_by(models.AgentTaskEvent.event_seq.desc())
        )
        if cached is not None:
            try:
                return schemas.AgentTaskEvaluationOut.model_validate(cached.detail)
            except ValidationError:
                # Ignore a corrupt historical cache entry and rebuild from the
                # persisted collaboration facts below.
                pass

        graph = _service().build_graph(db, task_id)
        scenario_id, safe_goal = _evaluation_demo_scenario(root, graph["selected_skill_ids"])
        nodes = _task_nodes(db, task_id)
        dependencies = list(db.scalars(
            select(models.AgentTaskDependency)
            .where(models.AgentTaskDependency.root_task_id == task_id)
            .order_by(models.AgentTaskDependency.id)
        ))
        messages = list(db.scalars(
            select(models.AgentMessage)
            .where(models.AgentMessage.root_task_id == task_id)
            .order_by(models.AgentMessage.created_at, models.AgentMessage.id)
        ))
        events = list(db.scalars(
            select(models.AgentTaskEvent)
            .where(models.AgentTaskEvent.root_task_id == task_id)
            .order_by(models.AgentTaskEvent.event_seq, models.AgentTaskEvent.id)
        ))
        evaluation = _build_evaluator().evaluate(
            task={
                "scenario_id": scenario_id,
                "goal": safe_goal,
                "selected_skill_ids": graph["selected_skill_ids"],
            },
            nodes=nodes,
            dependencies=dependencies,
            messages=messages,
            events=events,
            summary=run.summary,
        )
        validated_model = schemas.AgentTaskEvaluationOut.model_validate(evaluation)
        validated = validated_model.model_dump(mode="json")
        completed = validated_model.model_status == "completed"
        event_type = "task.evaluation.completed" if completed else "task.evaluation.partial"
        next_seq = db.scalar(
            select(func.max(models.AgentTaskEvent.event_seq)).where(
                models.AgentTaskEvent.root_task_id == task_id
            )
        ) or 0
        db.add(models.AgentTaskEvent(
            root_task_id=task_id,
            task_id=task_id,
            event_seq=next_seq + 1,
            event_type=event_type,
            actor_agent_id=actor.employee_no,
            target_agent_id=root.assignee_agent_id,
            summary="task evaluation completed" if completed else "task rule evaluation completed",
            detail=validated,
        ))
        try:
            db.commit()
        except IntegrityError:
            db.rollback()
            winner = db.scalar(
                select(models.AgentTaskEvent)
                .where(
                    models.AgentTaskEvent.root_task_id == task_id,
                    models.AgentTaskEvent.event_type == "task.evaluation.completed",
                )
                .order_by(models.AgentTaskEvent.event_seq.desc())
            )
            if winner is not None:
                return schemas.AgentTaskEvaluationOut.model_validate(winner.detail)
            raise HTTPException(status_code=409, detail="评测记录发生并发冲突，请重试")
        return schemas.AgentTaskEvaluationOut.model_validate(validated)


@router.post("/tasks/{task_id}/cancel", response_model=schemas.AgentCollaborationTaskOut)
def cancel_task(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    actor, run, root = _require_task_access(db, task_id, actor_no)
    if run.status in _TERMINAL_STATUSES or root.status in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="终态协作任务不能取消")

    active_nodes = [node for node in _task_nodes(db, task_id) if node.status not in _TERMINAL_STATUSES]
    if not active_nodes:
        raise HTTPException(status_code=409, detail="终态协作任务不能取消")
    now = datetime.now()
    for node in active_nodes:
        node.status = "cancelled"
        node.finished_at = now
        db.add(models.AgentMessage(
            id=f"AM-{uuid4().hex}",
            root_task_id=task_id,
            task_id=node.id,
            parent_task_id=node.parent_task_id,
            message_type="task.cancel",
            sender_agent_id="platform",
            receiver_agent_id=node.assignee_agent_id,
            body="task cancelled",
            payload={"reason": "cancelled_by_user"},
            matrix_room_id=config.get(config.AGENTTEAMS_ROOM_ID) or "",
            matrix_event_id=None,
            idempotency_key=f"{task_id}:task.cancel:{node.id}",
            direction="outbound",
            delivery_status="pending",
        ))
    run.status = "cancelled"
    next_seq = db.scalar(
        select(func.max(models.AgentTaskEvent.event_seq)).where(
            models.AgentTaskEvent.root_task_id == task_id
        )
    ) or 0
    db.add(models.AgentTaskEvent(
        root_task_id=task_id,
        task_id=task_id,
        event_seq=next_seq + 1,
        event_type="task.cancelled",
        actor_agent_id=actor.employee_no,
        target_agent_id=root.assignee_agent_id,
        summary="task cancelled by user",
        detail={"cancelled_nodes": [node.id for node in active_nodes], "actor_no": actor.employee_no},
    ))
    db.commit()
    return _task_out(db, run, root)


@router.post("/tasks/{task_id}/retry", response_model=schemas.AgentCollaborationTaskOut)
def retry_task(task_id: str, actor_no: str = Query(...), db: Session = Depends(get_db)):
    actor, run, root = _require_task_access(db, task_id, actor_no)
    if run.status == "completed" or root.status == "completed":
        raise HTTPException(status_code=409, detail="已完成协作任务不能重试")
    if run.status not in _TERMINAL_STATUSES or root.status not in _TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="仅终态协作任务可以重试")

    _require_supported_local_scenario(
        run.team_id,
        root.assignee_agent_id,
        root.title,
        root.instruction,
        dict(root.input_payload or {}),
    )
    _require_ready(db, team_id=run.team_id)
    database_scenario_id = ""
    if run.team_id == DATABASE_TEAM_ID:
        database_scenario_id = str((root.input_payload or {}).get("scenario_id") or "").strip()
        try:
            DatabaseScenarioCatalog.from_root(_PROJECT_ROOT).get(database_scenario_id)
        except ValueError:
            raise HTTPException(status_code=422, detail="数据库诊断场景已失效") from None
    service = (
        AgentTeamsCollaborationService(max_children=5, capability_catalog_source="database")
        if run.team_id == DATABASE_TEAM_ID
        else _service()
    )

    def retry_for_actor() -> models.TaskRun:
        retry_run = service.create_task(
            db,
            team_id=run.team_id,
            leader_agent_id=root.assignee_agent_id,
            title=root.title,
            instruction=root.instruction,
            material=dict(root.input_payload or {}),
            owner_human_no=actor.employee_no,
        )
        return retry_run

    retry_run = _commit_or_conflict(db, retry_for_actor)
    if retry_run.team_id == DATABASE_TEAM_ID:
        enqueue_database_collaboration_task(retry_run.id, database_scenario_id)
    return _task_out(db, retry_run)
