from __future__ import annotations

import asyncio

from app import main
from app.services import config


def _run_lifespan_without_external_side_effects(monkeypatch) -> list[str]:
    """Exercise the real lifespan control flow without DDL, seeding, or a runner."""
    calls: list[str] = []
    monkeypatch.setattr(
        main.Base.metadata,
        "create_all",
        lambda **_kwargs: calls.append("create_all"),
    )
    monkeypatch.setattr(
        main,
        "ensure_schema_compatibility",
        lambda: calls.append("ensure_schema_compatibility"),
    )
    monkeypatch.setattr(main, "seed_if_empty", lambda: calls.append("seed_if_empty"))
    monkeypatch.setattr(main.config, "agentteams_collaboration_enabled", lambda: False)
    monkeypatch.setattr(main, "runner_thread", None)

    async def exercise() -> None:
        async with main.lifespan(main.app):
            pass

    asyncio.run(exercise())
    return calls


def test_skip_auto_seed_setting_requires_literal_one(monkeypatch):
    getter = getattr(config, "skip_auto_seed", None)
    assert callable(getter), "configuration must expose the demo auto-seed safety switch"

    monkeypatch.setenv("DWP_SKIP_AUTO_SEED", "1")
    assert getter() is True
    monkeypatch.setenv("DWP_SKIP_AUTO_SEED", "true")
    assert getter() is False


def test_lifespan_skips_seed_when_auto_seed_skip_is_enabled(monkeypatch):
    monkeypatch.setenv("DWP_SKIP_AUTO_SEED", "1")

    calls = _run_lifespan_without_external_side_effects(monkeypatch)

    assert calls == ["create_all", "ensure_schema_compatibility"]


def test_lifespan_keeps_default_seed_behavior(monkeypatch):
    monkeypatch.delenv("DWP_SKIP_AUTO_SEED", raising=False)

    calls = _run_lifespan_without_external_side_effects(monkeypatch)

    assert calls == ["create_all", "ensure_schema_compatibility", "seed_if_empty"]
