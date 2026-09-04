"""Project-owned Skill discovery for the AgentTeams runtime."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
import re
from typing import Iterable


_SKILL_ID_PATTERN = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
_REQUIRED_FRONTMATTER_FIELDS = ("name", "description", "whenToUse")


class SkillRegistryError(ValueError):
    """Raised when a project Skill directory is invalid or not selectable."""


@dataclass(frozen=True)
class ProjectSkill:
    skill_id: str
    name: str
    description: str
    when_to_use: str
    content: str
    path: Path

    def catalog_entry(self) -> dict[str, str]:
        return {
            "skill_id": self.skill_id,
            "name": self.name,
            "description": self.description,
            "whenToUse": self.when_to_use,
        }


class ProjectSkillRegistry:
    """Immutable lookup layer over repository-local ``.agents/skills`` files."""

    def __init__(self, skills: Iterable[ProjectSkill]):
        indexed: dict[str, ProjectSkill] = {}
        for skill in skills:
            if skill.skill_id in indexed:
                raise SkillRegistryError(f"duplicate_skill:{skill.skill_id}")
            indexed[skill.skill_id] = skill
        self._skills = indexed

    @classmethod
    def from_root(cls, root: Path) -> "ProjectSkillRegistry":
        return cls(parse_skill_directories(root / ".agents" / "skills"))

    def catalog(self, allowed_ids: set[str] | None = None) -> list[dict[str, str]]:
        return [skill.catalog_entry() for skill in self._filter(allowed_ids)]

    def load(self, skill_ids: list[str]) -> list[ProjectSkill]:
        return [self._require(skill_id) for skill_id in skill_ids]

    def _filter(self, allowed_ids: set[str] | None) -> list[ProjectSkill]:
        skills = self._skills.values()
        if allowed_ids is not None:
            skills = (skill for skill in skills if skill.skill_id in allowed_ids)
        return sorted(skills, key=lambda skill: skill.skill_id)

    def _require(self, skill_id: str) -> ProjectSkill:
        skill = self._skills.get(skill_id)
        if skill is None:
            raise SkillRegistryError(f"unknown_skill:{skill_id}")
        return skill


def parse_skill_directories(skills_root: Path) -> list[ProjectSkill]:
    """Read direct ``<skill-id>/SKILL.md`` children with strict frontmatter."""
    if not skills_root.is_dir():
        raise SkillRegistryError(f"skills_directory_not_found:{skills_root}")

    for path in skills_root.rglob("SKILL.md"):
        if path.parent.parent != skills_root:
            raise SkillRegistryError(f"invalid_skill_depth:{path}")

    skills: list[ProjectSkill] = []
    for directory in sorted(skills_root.iterdir(), key=lambda path: path.name):
        if not directory.is_dir():
            continue
        skill_path = directory / "SKILL.md"
        if not skill_path.is_file():
            raise SkillRegistryError(f"missing_skill_file:{directory}")
        skill_id = directory.name
        if not _SKILL_ID_PATTERN.fullmatch(skill_id):
            raise SkillRegistryError(f"invalid_skill_id:{skill_id}")
        content = skill_path.read_text(encoding="utf-8")
        frontmatter = _parse_frontmatter(content, skill_path)
        skills.append(ProjectSkill(
            skill_id=skill_id,
            name=frontmatter["name"],
            description=frontmatter["description"],
            when_to_use=frontmatter["whenToUse"],
            content=content,
            path=skill_path,
        ))
    return skills


def _parse_frontmatter(content: str, path: Path) -> dict[str, str]:
    lines = content.splitlines()
    if not lines or lines[0].strip() != "---":
        raise SkillRegistryError(f"missing_frontmatter:{path}")
    try:
        closing_index = next(
            index for index, line in enumerate(lines[1:], start=1)
            if line.strip() == "---"
        )
    except StopIteration as error:
        raise SkillRegistryError(f"unterminated_frontmatter:{path}") from error

    fields: dict[str, str] = {}
    for line in lines[1:closing_index]:
        if not line.strip() or ":" not in line:
            raise SkillRegistryError(f"invalid_frontmatter:{path}")
        key, value = line.split(":", maxsplit=1)
        key, value = key.strip(), value.strip()
        if key in fields or not key or not value:
            raise SkillRegistryError(f"invalid_frontmatter:{path}")
        fields[key] = value

    for field in _REQUIRED_FRONTMATTER_FIELDS:
        if field not in fields:
            raise SkillRegistryError(f"missing_frontmatter:{field}")
    unexpected = set(fields).difference(_REQUIRED_FRONTMATTER_FIELDS)
    if unexpected:
        raise SkillRegistryError(f"unexpected_frontmatter:{sorted(unexpected)[0]}")
    if not "\n".join(lines[closing_index + 1:]).strip():
        raise SkillRegistryError(f"empty_skill_content:{path}")
    return fields
