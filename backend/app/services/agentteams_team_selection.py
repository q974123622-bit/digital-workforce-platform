"""Safe Team directory exposed to the AgentTeams Manager selection step.

AgentScope AgentTeams asks the Manager to inspect Teams before choosing an
individual Worker.  This adapter projects the platform's SQL resources into
that boundary while keeping runtime credentials and endpoints out of prompts.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models


def build_manager_team_catalog(db: Session) -> list[dict]:
    """Return only Teams with one active, explicitly registered Leader."""
    teams = list(db.scalars(select(models.Team).order_by(models.Team.id)))
    catalog: list[dict] = []
    for team in teams:
        memberships = list(db.scalars(
            select(models.TeamMember)
            .where(models.TeamMember.team_id == team.id)
            .order_by(models.TeamMember.id)
        ))
        leader = db.get(models.DigitalEmployee, team.leader_employee_id)
        if (
            leader is None
            or leader.status != "active"
            or leader.runtime_type != "agentteams"
            or not (leader.runtime_ref or "").strip()
            or not any(
                member.employee_id == leader.employee_no and member.role == "leader"
                for member in memberships
            )
        ):
            continue

        workers = []
        capability_text = {team.description.strip()} if team.description.strip() else set()
        for membership in memberships:
            employee = db.get(models.DigitalEmployee, membership.employee_id)
            if employee is None or employee.status != "active":
                continue
            if employee.role_prompt.strip():
                capability_text.add(employee.role_prompt.strip()[:256])
            if membership.employee_id == leader.employee_no:
                continue
            workers.append({
                "agent_id": employee.employee_no,
                "name": employee.name,
                "role": membership.role,
            })

        grants = list(db.scalars(
            select(models.TeamPluginGrant).where(
                models.TeamPluginGrant.team_id == team.id,
                models.TeamPluginGrant.decision_mode.in_(("allow", "approval")),
            )
        ))
        for grant in grants:
            plugin = db.get(models.Plugin, grant.plugin_id)
            if plugin is None or plugin.status != "active":
                continue
            capability = "：".join(
                item for item in (plugin.name, plugin.description) if item
            ).strip()[:256]
            if capability:
                capability_text.add(capability)

        catalog.append({
            "team_id": team.id,
            "name": team.name,
            "description": team.description,
            "leader_agent_id": leader.employee_no,
            "leader_name": leader.name,
            "workers": workers,
            "capabilities": sorted(capability_text),
        })
    return catalog
