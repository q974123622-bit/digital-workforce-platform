from __future__ import annotations


def five_complete_rounds(messages: list[dict]) -> list[dict]:
    """Return the last five complete user/assistant pairs; dangling current goal is excluded."""
    pairs: list[list[dict]] = []
    pending: dict | None = None
    for item in messages:
        if item.get("role") == "user":
            pending = item
        elif item.get("role") == "assistant" and pending is not None:
            pairs.append([pending, item])
            pending = None
    return [item for pair in pairs[-5:] for item in pair]
