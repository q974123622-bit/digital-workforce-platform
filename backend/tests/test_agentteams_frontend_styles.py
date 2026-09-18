from pathlib import Path


def test_task_tree_sibling_rail_uses_each_child_center_as_its_endpoint():
    repo_root = Path(__file__).resolve().parents[2]
    styles = (repo_root / "frontend" / "src" / "index.css").read_text(encoding="utf-8")

    assert ".at-chart-children::after" not in styles
    assert ".at-chart-children > .at-chart-node::after" in styles
    assert ".at-chart-children > .at-chart-node:first-child::after" in styles
    assert ".at-chart-children > .at-chart-node:last-child::after" in styles
    assert ".at-chart-children > .at-chart-node:only-child::after" in styles
    assert "top: -20px" in styles
    assert "height: 40px" in styles
