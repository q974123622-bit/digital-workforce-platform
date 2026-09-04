from pathlib import Path

import pytest

from app.services.agentteams_skills import ProjectSkill, ProjectSkillRegistry, SkillRegistryError


@pytest.fixture()
def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_registry_returns_summary_before_full_content(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)

    item = next(
        item
        for item in registry.catalog()
        if item["skill_id"] == "agentteams-onboarding-readiness"
    )

    assert set(item) == {"skill_id", "name", "description", "whenToUse"}
    assert item["description"]
    assert item["whenToUse"]
    loaded = registry.load([item["skill_id"]])[0]
    assert loaded.content
    assert loaded.content != item["description"]


def test_registry_rejects_path_traversal(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)

    with pytest.raises(SkillRegistryError, match="unknown_skill"):
        registry.load(["../backend/.env"])


def test_catalog_is_sorted_and_can_be_limited_to_allowed_ids(repo_root):
    registry = ProjectSkillRegistry.from_root(repo_root)

    catalog = registry.catalog({"it-service-assistant", "audit-evidence-review"})

    assert [item["skill_id"] for item in catalog] == [
        "audit-evidence-review",
        "it-service-assistant",
    ]


def test_registry_rejects_skill_file_below_the_single_directory_level(tmp_path):
    skills_root = tmp_path / ".agents" / "skills"
    first = skills_root / "duplicate-skill" / "SKILL.md"
    first.parent.mkdir(parents=True)
    first.write_text(
        "---\nname: First\ndescription: Use when first\nwhenToUse: first\n---\nbody\n",
        encoding="utf-8",
    )
    duplicate = skills_root / "nested" / "duplicate-skill" / "SKILL.md"
    duplicate.parent.mkdir(parents=True)
    duplicate.write_text(
        "---\nname: Duplicate\ndescription: Use when duplicate\nwhenToUse: duplicate\n---\nbody\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillRegistryError, match="invalid_skill_depth"):
        ProjectSkillRegistry.from_root(tmp_path)


def test_registry_rejects_duplicate_skill_ids():
    skill = ProjectSkill(
        skill_id="duplicate-skill",
        name="Duplicate",
        description="Use when testing duplicate skills",
        when_to_use="testing duplicate skills",
        content="skill body",
        path=Path("duplicate/SKILL.md"),
    )

    with pytest.raises(SkillRegistryError, match="duplicate_skill"):
        ProjectSkillRegistry([skill, skill])


def test_registry_requires_each_frontmatter_field(tmp_path):
    path = tmp_path / ".agents" / "skills" / "incomplete" / "SKILL.md"
    path.parent.mkdir(parents=True)
    path.write_text(
        "---\nname: Incomplete\ndescription: Use when incomplete\n---\nbody\n",
        encoding="utf-8",
    )

    with pytest.raises(SkillRegistryError, match="missing_frontmatter:whenToUse"):
        ProjectSkillRegistry.from_root(tmp_path)


@pytest.mark.parametrize(
    ("skill_id", "required_terms", "forbidden_terms"),
    [
        (
            "agentteams-manager-coordination",
            ("粗粒度", "当前团队", "直接子", "证据", "有界"),
            (),
        ),
        (
            "agentteams-onboarding-readiness",
            ("证据类别", "完成条件", "未知"),
            ("VE-",),
        ),
        (
            "employee-collaboration",
            ("Tool", "Ask", "Delegate", "Handoff", "父链"),
            (),
        ),
        (
            "hr-onboarding-assistant",
            ("Tool", "证据", "未知"),
            (),
        ),
        (
            "it-service-assistant",
            ("Tool", "证据", "未知"),
            (),
        ),
        (
            "audit-evidence-review",
            ("Tool", "证据", "未知"),
            (),
        ),
    ],
)
def test_each_project_skill_exposes_required_operational_invariants(
    repo_root, skill_id, required_terms, forbidden_terms,
):
    registry = ProjectSkillRegistry.from_root(repo_root)

    skill = registry.load([skill_id])[0]

    assert skill.name
    assert skill.description.startswith("Use when")
    assert skill.when_to_use
    assert len(skill.content) >= 160
    assert all(term in skill.content for term in required_terms)
    assert all(term not in skill.content for term in forbidden_terms)
