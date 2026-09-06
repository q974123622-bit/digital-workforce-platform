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


@pytest.mark.parametrize("link_kind", ("directory", "file"))
def test_registry_rejects_symlinked_skill_path_outside_project_root(
    tmp_path, monkeypatch, link_kind,
):
    skills_root = tmp_path / ".agents" / "skills"
    skills_root.mkdir(parents=True)
    skill_directory = skills_root / "linked-skill"
    skill_directory.mkdir()
    skill_path = skill_directory / "SKILL.md"
    skill_path.write_text(
        "---\nname: External\ndescription: Use when external\nwhenToUse: external\n---\nbody\n",
        encoding="utf-8",
    )
    symlinked_path = skill_directory if link_kind == "directory" else skill_path
    original_is_symlink = Path.is_symlink
    monkeypatch.setattr(
        Path,
        "is_symlink",
        lambda path: path == symlinked_path or original_is_symlink(path),
    )

    with pytest.raises(SkillRegistryError, match="symlinked_skill_path"):
        ProjectSkillRegistry.from_root(tmp_path)


@pytest.mark.parametrize("candidate_kind", ("directory", "file"))
def test_registry_rejects_resolved_skill_path_outside_project_root(
    tmp_path, monkeypatch, candidate_kind,
):
    skills_root = tmp_path / ".agents" / "skills"
    skill_directory = skills_root / "inside-root"
    skill_directory.mkdir(parents=True)
    skill_path = skill_directory / "SKILL.md"
    skill_path.write_text(
        "---\nname: Inside\ndescription: Use when inside\nwhenToUse: inside\n---\nbody\n",
        encoding="utf-8",
    )
    external_path = tmp_path / "outside-root"
    external_path.mkdir()
    escaped_path = skill_directory if candidate_kind == "directory" else skill_path
    original_resolve = Path.resolve

    def resolve_with_escape(path, strict=False):
        if path == escaped_path:
            return external_path
        return original_resolve(path, strict=strict)

    monkeypatch.setattr(Path, "resolve", resolve_with_escape)

    with pytest.raises(SkillRegistryError, match="skill_path_outside_root"):
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


def test_it_skills_define_one_audit_delegate_for_missing_mfa_evidence(repo_root):
    """IT must delegate a proven MFA evidence gap once, then use the child result terminally."""
    registry = ProjectSkillRegistry.from_root(repo_root)
    collaboration, it_service = registry.load([
        "employee-collaboration",
        "it-service-assistant",
    ])

    assert "自己的 Tool 证据" in collaboration.content
    assert "特定证据缺口" in collaboration.content
    assert "独立复核能力" in collaboration.content
    assert "不得重复委派" in collaboration.content
    assert "Complete 或 Fail" in collaboration.content
    assert "MFA确认缺失" in it_service.content
    assert "audit.evidence.read" in it_service.content
    assert "Delegate" in it_service.content
    assert "仅复核该证据缺口" in it_service.content
    assert "不得重复委派" in it_service.content
    assert "Complete 或 Fail" in it_service.content
    assert "VE-" not in collaboration.content
    assert "VE-" not in it_service.content


def test_onboarding_skills_keep_first_round_parallel_and_audit_evidence_independent(
    repo_root,
):
    """First-round HR/IT work must stay parallel; audit verifies evidence with its own Tool."""
    registry = ProjectSkillRegistry.from_root(repo_root)
    readiness, hr, audit = registry.load([
        "agentteams-onboarding-readiness",
        "hr-onboarding-assistant",
        "audit-evidence-review",
    ])

    assert "Manager" in readiness.content
    assert "首轮" in readiness.content
    assert "同一批次" in readiness.content
    assert "HR" in readiness.content and "IT" in readiness.content
    assert "无依赖" in readiness.content
    assert "首轮不得派发 Audit" in readiness.content
    assert "不得将 HR→Audit 作为默认演示路径" in hr.content
    assert "获授权的 Audit Tool" in audit.content
    assert "MFA 证据" in audit.content
    assert "不能只采信 IT 的文字" in audit.content
    assert "VE-" not in readiness.content
    assert "VE-" not in hr.content
    assert "VE-" not in audit.content


def test_readiness_skill_stops_repeating_a_direct_workers_covered_audit_gap(repo_root):
    """A confirmed missing fact with integrated audit evidence is terminal, not a new IT round."""
    readiness = ProjectSkillRegistry.from_root(repo_root).load([
        "agentteams-onboarding-readiness",
    ])[0]

    assert "直接 Worker 已整合的嵌套审计结论" in readiness.content
    assert "足够的最终证据" in readiness.content
    assert "证据确认缺失" in readiness.content
    assert "not ready/incomplete" in readiness.content
    assert "不得为了让缺失项变成 ready 再重复派发" in readiness.content
    assert "尚未调查" in readiness.content
    assert "新独立事实问题" in readiness.content


@pytest.mark.parametrize(
    ("skill_id", "required_terms", "forbidden_terms"),
    [
        (
            "agentteams-manager-coordination",
            ("粗粒度", "当前团队", "直接子", "证据", "有界", "首轮", "一手证据", "后续复核"),
            ("VE-",),
        ),
        (
            "agentteams-onboarding-readiness",
            ("证据类别", "完成条件", "未知", "首轮", "HR", "IT", "二次复核"),
            ("VE-",),
        ),
        (
            "employee-collaboration",
            ("Tool", "Ask", "Delegate", "Complete", "Fail", "父链", "能力目录", "等待子任务结果"),
            ("VE-", "Handoff"),
        ),
        (
            "hr-onboarding-assistant",
            ("Tool", "证据", "未知", "先", "Delegate", "审计能力", "子任务结果"),
            ("VE-",),
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
