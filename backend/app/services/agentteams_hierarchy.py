"""One fail-closed Manager -> Leader -> Worker team topology contract."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Collection, Literal, Mapping, Sequence


HierarchyLevel = Literal["manager", "leader", "worker"]


class TeamHierarchyError(RuntimeError):
    """Expose one bounded hierarchy error code without leaking team data."""


@dataclass(frozen=True)
class TeamHierarchy:
    manager_id: str
    _roles: Mapping[str, str]
    _parents: Mapping[str, str]
    _reports: Mapping[str, tuple[str, ...]]
    _levels: Mapping[str, HierarchyLevel]

    @classmethod
    def from_relationships(
        cls,
        *,
        manager_id: str,
        member_ids: Collection[str],
        relationships: Mapping[str, Mapping[str, object]],
    ) -> "TeamHierarchy":
        members = set(member_ids)
        if (
            not manager_id
            or manager_id not in members
            or set(relationships) != members
            or len(members) < 3
        ):
            raise TeamHierarchyError("invalid_team_hierarchy")

        roles: dict[str, str] = {}
        reports_to: dict[str, tuple[str, ...]] = {}
        direct_reports: dict[str, tuple[str, ...]] = {}
        for agent_id, raw in relationships.items():
            if not isinstance(raw, Mapping):
                raise TeamHierarchyError("invalid_team_hierarchy")
            role = raw.get("role")
            parents = _agent_id_tuple(raw.get("reports_to"))
            children = _agent_id_tuple(raw.get("direct_reports"))
            if (
                not isinstance(agent_id, str)
                or not agent_id
                or not isinstance(role, str)
                or not role.strip()
                or len(role) > 128
                or parents is None
                or children is None
                or agent_id in parents
                or agent_id in children
                or not set(parents).issubset(members)
                or not set(children).issubset(members)
            ):
                raise TeamHierarchyError("invalid_team_hierarchy")
            roles[agent_id] = role.strip()
            reports_to[agent_id] = parents
            direct_reports[agent_id] = children

        leaders = direct_reports[manager_id]
        if reports_to[manager_id] or not leaders:
            raise TeamHierarchyError("invalid_team_hierarchy")

        leader_set = set(leaders)
        worker_parent: dict[str, str] = {}
        for leader_id in leaders:
            if reports_to[leader_id] != (manager_id,) or not direct_reports[leader_id]:
                raise TeamHierarchyError("invalid_team_hierarchy")
            for worker_id in direct_reports[leader_id]:
                if worker_id in leader_set or worker_id in worker_parent:
                    raise TeamHierarchyError("invalid_team_hierarchy")
                worker_parent[worker_id] = leader_id

        worker_set = set(worker_parent)
        if members != {manager_id, *leader_set, *worker_set}:
            raise TeamHierarchyError("invalid_team_hierarchy")
        for worker_id, leader_id in worker_parent.items():
            if reports_to[worker_id] != (leader_id,) or direct_reports[worker_id]:
                raise TeamHierarchyError("invalid_team_hierarchy")

        for supervisor_id, children in direct_reports.items():
            for child_id in children:
                if reports_to[child_id] != (supervisor_id,):
                    raise TeamHierarchyError("invalid_team_hierarchy")

        levels: dict[str, HierarchyLevel] = {manager_id: "manager"}
        levels.update({agent_id: "leader" for agent_id in leaders})
        levels.update({agent_id: "worker" for agent_id in worker_set})
        parents = {
            agent_id: parent_ids[0]
            for agent_id, parent_ids in reports_to.items()
            if parent_ids
        }
        return cls(
            manager_id=manager_id,
            _roles=MappingProxyType(roles),
            _parents=MappingProxyType(parents),
            _reports=MappingProxyType(direct_reports),
            _levels=MappingProxyType(levels),
        )

    @property
    def member_ids(self) -> frozenset[str]:
        return frozenset(self._levels)

    def level_of(self, agent_id: str) -> HierarchyLevel:
        try:
            return self._levels[agent_id]
        except KeyError:
            raise TeamHierarchyError("invalid_team_hierarchy") from None

    def direct_reports_of(self, agent_id: str) -> tuple[str, ...]:
        try:
            return self._reports[agent_id]
        except KeyError:
            raise TeamHierarchyError("invalid_team_hierarchy") from None

    def validate_delegation(self, delegator_id: str, assignee_ids: Sequence[str]) -> None:
        if not assignee_ids or len(set(assignee_ids)) != len(assignee_ids):
            raise TeamHierarchyError("invalid_team_hierarchy")
        level = self.level_of(delegator_id)
        if level == "worker":
            raise TeamHierarchyError("worker_cannot_delegate")
        allowed = set(self.direct_reports_of(delegator_id))
        if any(assignee_id not in allowed for assignee_id in assignee_ids):
            code = (
                "manager_must_delegate_to_leader"
                if level == "manager"
                else "leader_must_delegate_to_worker"
            )
            raise TeamHierarchyError(code)

    def project_for(
        self,
        agent_id: str,
        capability_catalog: Mapping[str, Mapping[str, object]],
    ) -> dict[str, object]:
        level = self.level_of(agent_id)
        parent = self._parents.get(agent_id)
        reports: list[dict[str, object]] = []
        for report_id in self.direct_reports_of(agent_id):
            raw_capabilities = capability_catalog.get(report_id, {}).get("capabilities", [])
            if not isinstance(raw_capabilities, (list, tuple)):
                raise TeamHierarchyError("invalid_team_hierarchy")
            capabilities = [
                item
                for item in raw_capabilities
                if isinstance(item, str) and item and len(item) <= 128
            ]
            if len(capabilities) != len(raw_capabilities) or len(capabilities) > 32:
                raise TeamHierarchyError("invalid_team_hierarchy")
            reports.append(
                {
                    "agent_id": report_id,
                    "role": self._roles[report_id],
                    "capabilities": capabilities,
                }
            )
        return {
            "self": {
                "agent_id": agent_id,
                "role": self._roles[agent_id],
                "level": level,
            },
            "reports_to": [parent] if parent is not None else [],
            "direct_reports": reports,
        }


def _agent_id_tuple(value: object) -> tuple[str, ...] | None:
    if not isinstance(value, (list, tuple)) or len(value) > 32:
        return None
    if any(not isinstance(item, str) or not item or len(item) > 80 for item in value):
        return None
    normalized = tuple(value)
    if len(set(normalized)) != len(normalized):
        return None
    return normalized
