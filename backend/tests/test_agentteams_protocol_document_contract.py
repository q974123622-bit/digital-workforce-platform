from __future__ import annotations

from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parents[2]
PROTOCOL_DOCUMENT = PROJECT_ROOT / "docs" / "agentteams" / "PROTOCOL.md"
PROTOCOL_SOURCE = PROJECT_ROOT / "backend" / "app" / "services" / "agentteams_protocol.py"


def test_protocol_document_distinguishes_root_and_child_parent_task_id_rules():
    document = PROTOCOL_DOCUMENT.read_text(encoding="utf-8")
    source = PROTOCOL_SOURCE.read_text(encoding="utf-8")

    assert "parent_task_id: str | None = None" in source
    assert "Root: No, defaults to `null`; Child: Yes" in document
    assert "Child envelopes must include the persisted parent node ID" in document
    assert "`invalid_parent_task`" in document
