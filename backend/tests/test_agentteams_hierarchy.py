from __future__ import annotations

import pytest

from app.services.agentteams_hierarchy import TeamHierarchy, TeamHierarchyError


def _valid_relationships() -> dict[str, dict[str, object]]:
    return {
        "M": {"role": "Manager", "reports_to": [], "direct_reports": ["L1", "L2"]},
        "L1": {"role": "HR Leader", "reports_to": ["M"], "direct_reports": ["W1", "W2"]},
        "L2": {"role": "IT Leader", "reports_to": ["M"], "direct_reports": ["W3"]},
        "W1": {"role": "HR Worker A", "reports_to": ["L1"], "direct_reports": []},
        "W2": {"role": "HR Worker B", "reports_to": ["L1"], "direct_reports": []},
        "W3": {"role": "IT Worker", "reports_to": ["L2"], "direct_reports": []},
    }


def _build(relationships: dict[str, dict[str, object]] | None = None) -> TeamHierarchy:
    value = relationships or _valid_relationships()
    return TeamHierarchy.from_relationships(
        manager_id="M", member_ids=set(value), relationships=value,
    )


def test_builds_manager_leader_worker_levels() -> None:
    hierarchy = _build()
    assert hierarchy.level_of("M") == "manager"
    assert hierarchy.level_of("L1") == "leader"
    assert hierarchy.level_of("W1") == "worker"
    assert hierarchy.direct_reports_of("M") == ("L1", "L2")
    assert hierarchy.direct_reports_of("L1") == ("W1", "W2")


@pytest.mark.parametrize(
    "mutate",
    [
        lambda graph: graph["M"]["direct_reports"].append("UNKNOWN"),
        lambda graph: graph["M"].update(direct_reports=["L1", "L2", "W1"]),
        lambda graph: graph["L1"].update(direct_reports=[]),
        lambda graph: graph["W1"].update(direct_reports=["M"]),
        lambda graph: graph["W1"].update(reports_to=["L1", "L2"]),
        lambda graph: graph["W1"].update(reports_to=[]),
    ],
)
def test_rejects_non_three_level_topologies(mutate) -> None:
    relationships = _valid_relationships()
    mutate(relationships)
    with pytest.raises(TeamHierarchyError, match="^invalid_team_hierarchy$"):
        TeamHierarchy.from_relationships(
            manager_id="M", member_ids=set(_valid_relationships()), relationships=relationships,
        )


def test_rejects_relationship_member_set_mismatch() -> None:
    with pytest.raises(TeamHierarchyError, match="^invalid_team_hierarchy$"):
        TeamHierarchy.from_relationships(
            manager_id="M",
            member_ids={*_valid_relationships(), "EXTRA"},
            relationships=_valid_relationships(),
        )


@pytest.mark.parametrize(
    ("delegator", "assignees", "code"),
    [
        ("M", ["W1"], "manager_must_delegate_to_leader"),
        ("L1", ["W3"], "leader_must_delegate_to_worker"),
        ("W1", ["W2"], "worker_cannot_delegate"),
    ],
)
def test_rejects_cross_level_or_cross_department_delegation(
    delegator: str, assignees: list[str], code: str,
) -> None:
    with pytest.raises(TeamHierarchyError, match=f"^{code}$"):
        _build().validate_delegation(delegator, assignees)


def test_accepts_only_direct_next_level_delegation() -> None:
    hierarchy = _build()
    hierarchy.validate_delegation("M", ["L1", "L2"])
    hierarchy.validate_delegation("L1", ["W1", "W2"])


def test_projects_only_self_parent_and_direct_reports_with_capabilities() -> None:
    catalog = {
        "M": {"capabilities": ["manage"]},
        "L1": {"capabilities": ["coordinate.hr"]},
        "L2": {"capabilities": ["coordinate.it"]},
        "W1": {"capabilities": ["hr.materials"]},
        "W2": {"capabilities": ["hr.training"]},
        "W3": {"capabilities": ["it.account"]},
    }
    assert _build().project_for("L1", catalog) == {
        "self": {"agent_id": "L1", "role": "HR Leader", "level": "leader"},
        "reports_to": ["M"],
        "direct_reports": [
            {"agent_id": "W1", "role": "HR Worker A", "capabilities": ["hr.materials"]},
            {"agent_id": "W2", "role": "HR Worker B", "capabilities": ["hr.training"]},
        ],
    }


def test_allows_distinct_tasks_for_the_same_direct_report() -> None:
    _build().validate_delegation("L1", ["W1", "W1"])
