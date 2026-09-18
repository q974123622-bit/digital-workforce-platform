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


def test_department_coordinators_delegate_once_to_two_direct_specialists(repo_root):
    """A coordinate task fans out once to the two specialists in the relationship graph."""
    registry = ProjectSkillRegistry.from_root(repo_root)
    hr, it = registry.load([
        "hr-onboarding-assistant",
        "it-service-assistant",
    ])

    for skill in (hr, it):
        assert "coordinate" in skill.content
        assert "team_relationship_graph" in skill.content
        assert "direct_reports" in skill.content
        assert "同一批次" in skill.content
        assert "无依赖" in skill.content
        assert "两名直属专员" in skill.content
        assert "仅委派一次" in skill.content
        assert "child_results" in skill.content
        assert "部门汇总" in skill.content
        assert "audit.evidence.read" not in skill.content
        assert "VE-" not in skill.content
    assert "hr.materials.read" in hr.content
    assert "hr.training.read" in hr.content
    assert "it.accounts.read" in it.content
    assert "it.devices.read" in it.content


def test_onboarding_skills_keep_first_round_parallel_and_audit_evidence_independent(
    repo_root,
):
    """The Manager waits for both departments, then requests independent audit evidence."""
    registry = ProjectSkillRegistry.from_root(repo_root)
    readiness, audit = registry.load([
        "agentteams-onboarding-readiness",
        "audit-evidence-review",
    ])

    assert "Manager" in readiness.content
    assert "首轮" in readiness.content
    assert "同一批次" in readiness.content
    assert "HR" in readiness.content and "IT" in readiness.content
    assert "无依赖" in readiness.content
    assert "首轮不得派发 Audit" in readiness.content
    assert "hr.onboarding.coordinate" in readiness.content
    assert "it.onboarding.coordinate" in readiness.content
    assert "两个部门汇总" in readiness.content
    assert "第二轮" in readiness.content
    assert "audit.evidence.read" in readiness.content
    assert "审计返回后" in readiness.content
    assert "总结" in readiness.content
    assert "获授权的 Audit Tool" in audit.content
    assert "MFA 证据" in audit.content
    assert "不能只采信 IT 的文字" in audit.content
    assert "VE-" not in readiness.content
    assert "VE-" not in audit.content


def test_readiness_skill_uses_audit_then_targeted_department_follow_up(repo_root):
    """Audit stays Manager-owned and is followed by one gap-driven department recheck."""
    readiness = ProjectSkillRegistry.from_root(repo_root).load([
        "agentteams-onboarding-readiness",
    ])[0]

    assert "Manager 大模型" in readiness.content
    assert "只派发一个" in readiness.content
    assert "不得由 HR 或 IT 负责人嵌套派发" in readiness.content
    assert "不得重复派发审计" in readiness.content
    assert "审计子任务完成" in readiness.content
    assert "第三轮" in readiness.content
    assert "定向补查" in readiness.content
    assert "只选择确有证据缺口" in readiness.content
    assert "第四轮" in readiness.content
    assert "ready、not ready 或 incomplete" in readiness.content


@pytest.mark.parametrize(
    ("skill_id", "capability", "tool_name"),
    [
        ("hr-material-specialist", "hr.materials.read", "agentteams.hr.lookup_materials"),
        ("hr-training-specialist", "hr.training.read", "agentteams.hr.lookup_training"),
        ("it-account-specialist", "it.accounts.read", "agentteams.it.lookup_accounts"),
        ("it-device-specialist", "it.devices.read", "agentteams.it.lookup_devices"),
    ],
)
def test_leaf_specialists_call_their_own_tool_before_completing(
    repo_root, skill_id, capability, tool_name,
):
    """Each leaf specialist obtains role-owned Tool evidence before a terminal result."""
    skill = ProjectSkillRegistry.from_root(repo_root).load([skill_id])[0]

    assert skill.description.startswith("Use when")
    assert capability in skill.content
    assert tool_name in skill.content
    assert "先调用" in skill.content
    assert "Tool 证据" in skill.content
    assert "未知" in skill.content
    assert "Complete" in skill.content
    assert "不得 Delegate" in skill.content
    assert "VE-" not in skill.content


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
            ("coordinate", "team_relationship_graph", "direct_reports", "Delegate", "child_results", "部门汇总"),
            ("VE-",),
        ),
        (
            "it-service-assistant",
            ("coordinate", "team_relationship_graph", "direct_reports", "Delegate", "child_results", "部门汇总"),
            ("VE-",),
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


@pytest.mark.parametrize(
    ("skill_id", "required_terms"),
    [
        ("agentteams-expense-reimbursement", ("财务负责人", "审计负责人", "AUD-EXP-001", "付款")),
        ("finance-expense-coordination", ("direct_reports", "发票", "预算", "付款", "Leader")),
        ("expense-invoice-verification", ("finance.expense.claim.read", "agentteams.finance.verify_claim", "Complete")),
        ("expense-budget-verification", ("finance.expense.budget.read", "agentteams.finance.verify_budget", "Complete")),
        ("expense-payment-preparation", ("finance.expense.payment.create", "AUD-EXP-001", "模拟付款单")),
        ("audit-expense-review", ("audit.expense.review", "agentteams.audit.review_expense", "AUD-EXP-001")),
    ],
)
def test_expense_skills_encode_hierarchy_and_evidence_contract(repo_root, skill_id, required_terms):
    skill = ProjectSkillRegistry.from_root(repo_root).load([skill_id])[0]

    assert skill.description.startswith("Use when")
    assert skill.when_to_use
    assert all(term in skill.content for term in required_terms)


def test_expense_manager_skill_separates_blocking_evidence_from_optional_details(repo_root):
    """Optional business details must not overturn a fully evidenced demo decision."""
    skill = ProjectSkillRegistry.from_root(repo_root).load(["agentteams-expense-reimbursement"])[0]

    assert "阻断性证据" in skill.content
    assert "非阻断补充项" in skill.content
    assert "结论必须判定为通过" in skill.content
    assert "不得仅因" in skill.content
    assert "收款方名称" in skill.content
    assert "发票代码" in skill.content


@pytest.mark.parametrize(
    "skill_id",
    (
        "hr-onboarding-assistant",
        "hr-role-change-assistant",
        "it-service-assistant",
        "it-role-change-assistant",
        "audit-evidence-review",
        "audit-role-change-review",
        "audit-expense-review",
        "finance-expense-coordination",
    ),
)
def test_business_leader_skills_preserve_worker_owned_evidence_without_duplicate_delegation(
    repo_root, skill_id,
):
    """Leader summaries identify child evidence without treating their own Tool silence as a gap."""
    skill = ProjectSkillRegistry.from_root(repo_root).load([skill_id])[0]

    assert "直属 Worker" in skill.content
    assert "Leader 节点没有直接 Tool" in skill.content
    assert "audit_id" in skill.content
    assert "证据编号" in skill.content
    assert "不得仅因" in skill.content
    assert "重复派发" in skill.content
