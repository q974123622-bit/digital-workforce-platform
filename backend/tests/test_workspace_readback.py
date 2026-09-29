"""dsh 没给最终答复时读回工作区产物的回归用例。

背景：dsh 是一次性 CLI 调用，最终答复只从 stdout 出来。模型把预算花在工具调用上、
或者进程被终止时 stdout 可能是空的，而它在 /workspace 里写出的核对表是**产物**。
读回来给用户，比报一句「没有内容」有用 —— 但必须只读本轮的、文本类的、非平台自己的文件，
否则会把上一轮的旧表或工具的中间 JSON 当成结论。
"""

from __future__ import annotations

import os
import time

import pytest

from app.services import runtime_manager, workspace_readback


@pytest.fixture()
def workspace(tmp_path, monkeypatch):
    """把某员工的工作区指到临时目录，避免碰真机上的 harness/。"""
    root = tmp_path / "harness-workspaces" / "AI-AUDIT"
    (root / "workspace").mkdir(parents=True)
    monkeypatch.setattr(runtime_manager, "workspace_ref", lambda employee_id: str(root))
    return root / "workspace"


def _write(base, relative, text, mtime=None):
    path = base / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_collects_only_text_artifacts(workspace):
    _write(workspace, "核对表_B2026001.md", "# 核对表\n一致 3 项")
    _write(workspace, "findings_B2026001.json", '{"summary": {}}')
    _write(workspace, "导出.csv", "字段,结论\n名称,一致")
    _write(workspace, "notes.txt", "随手记")
    # 平台自己写的，不是模型产物
    _write(workspace, "skills/institution-account-material-review/SKILL.md", "# 技能说明")
    _write(workspace, "materials/PACK-1/B2026001/营业执照.json", "{}")
    # 隐藏文件（幂等清单这类）
    _write(workspace, ".dwp-deploy-state.json", "{}")

    names = [item["name"] for item in workspace_readback.collect("AI-AUDIT")]
    assert "核对表_B2026001.md" in names
    assert "导出.csv" in names and "notes.txt" in names
    assert not any(name.endswith(".json") for name in names)
    assert not any(name.startswith("skills/") for name in names)
    assert not any(name.startswith("materials/") for name in names)
    assert not any(name.startswith(".") for name in names)


def test_skips_files_from_previous_runs(workspace):
    """只读本轮动过的文件：上一轮留下的旧核对表不能当成本轮结论。"""
    old = time.time() - 3600
    _write(workspace, "旧核对表.md", "上一轮的结论", mtime=old)
    _write(workspace, "新核对表.md", "本轮的结论")

    fresh = [item["name"] for item in workspace_readback.collect("AI-AUDIT", since=time.time() - 60)]
    assert fresh == ["新核对表.md"], fresh
    # 不给 since 时（手工排查）两份都读
    assert len(workspace_readback.collect("AI-AUDIT")) == 2


def test_truncates_long_artifact(workspace):
    _write(workspace, "长表.md", "行\n" * 5000)
    items = workspace_readback.collect("AI-AUDIT", max_file_chars=200)
    assert items and items[0]["truncated"] is True
    assert len(items[0]["text"]) == 200


def test_newest_first_and_file_limit(workspace):
    for index in range(6):
        _write(workspace, "表%d.md" % index, "内容%d" % index, mtime=time.time() - index * 10)
    items = workspace_readback.collect("AI-AUDIT", max_files=3)
    assert [item["name"] for item in items] == ["表0.md", "表1.md", "表2.md"]


def test_render_marks_it_as_fallback(workspace):
    _write(workspace, "核对表_B2026001.md", "# 核对表\n\n| 字段 | 结论 |\n|---|---|\n| 名称 | 一致 |")
    text = workspace_readback.render("AI-AUDIT", since=time.time() - 60, reason="进程没有输出")
    assert "没有返回最终答复" in text
    assert "进程没有输出" in text
    assert "核对表_B2026001.md" in text
    assert "| 名称 | 一致 |" in text
    # 必须说清这是原文读回，不是模型答复
    assert "原文读回" in text


def test_render_empty_when_nothing_to_read(workspace):
    assert workspace_readback.render("AI-AUDIT") == ""
    _write(workspace, "findings.json", "{}")          # 只有中间件
    assert workspace_readback.render("AI-AUDIT") == ""


def test_render_caps_total_size(workspace, monkeypatch):
    monkeypatch.setattr(workspace_readback, "MAX_TOTAL_CHARS", 500)
    _write(workspace, "大表.md", "甲" * 2000)
    text = workspace_readback.render("AI-AUDIT")
    assert len(text) <= 500 + len("\n\n（内容过长，已截断）")
    assert text.endswith("（内容过长，已截断）")


def test_summarize_lists_names(workspace):
    _write(workspace, "核对表.md", "x")
    items = workspace_readback.collect("AI-AUDIT")
    assert workspace_readback.summarize(items) == "已读回 核对表.md"
    assert "没有可读回" in workspace_readback.summarize([])


# ------------------------------------------------------------------ 接进执行链路

def test_run_agent_reads_back_workspace_when_no_answer(db_session, monkeypatch):
    """dsh 没输出时：答复换成工作区产物，并在轨迹里留一条 answer_from_workspace。"""
    from app.services import harness_agent
    from app.services.runtime_adapter import RuntimeResult

    root = os.path.join(os.environ.get("TMPDIR", "/tmp"), "rb-%s" % os.getpid())
    workspace = os.path.join(root, "harness-workspaces", "AI-AUDIT", "workspace")
    os.makedirs(workspace, exist_ok=True)
    with open(os.path.join(workspace, "核对表_B2026001.md"), "w", encoding="utf-8") as fh:
        fh.write("# 机构开户材料核对表\n\n| 字段 | 结论 |\n|---|---|\n| 名称 | 一致 |")
    monkeypatch.setattr(runtime_manager, "workspace_ref", lambda employee_id: os.path.join(
        root, "harness-workspaces", employee_id))

    class EmptyRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=False, result="Harness 未返回内容",
                                 empty_output=True)

    monkeypatch.setattr(harness_agent, "DockerHarnessRuntimeAdapter", EmptyRuntime)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_busy", lambda *_args: None)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_ready", lambda *_args: None)

    result = harness_agent.run_agent(
        db_session, employee_id="AI-AUDIT", requester_human_no="E10281", message="核对材料",
    )
    assert "没有返回最终答复" in result.message
    assert "| 名称 | 一致 |" in result.message


def test_run_agent_still_fails_when_workspace_is_empty(db_session, monkeypatch):
    """真失败（容器起不来）不能兜底成「读回产物」，必须照旧 503。"""
    from fastapi import HTTPException

    from app.services import harness_agent
    from app.services.runtime_adapter import RuntimeResult

    class BrokenRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=False, result="Harness 容器不可用：RuntimeError")

    monkeypatch.setattr(harness_agent, "DockerHarnessRuntimeAdapter", BrokenRuntime)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_busy", lambda *_args: None)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_ready", lambda *_args: None)

    with pytest.raises(HTTPException) as exc:
        harness_agent.run_agent(
            db_session, employee_id="AI-AUDIT", requester_human_no="E10281", message="核对材料",
        )
    assert exc.value.status_code == 503


def test_run_agent_fails_when_no_answer_and_nothing_to_read(db_session, monkeypatch, tmp_path):
    from fastapi import HTTPException

    from app.services import harness_agent
    from app.services.runtime_adapter import RuntimeResult

    monkeypatch.setattr(runtime_manager, "workspace_ref",
                        lambda employee_id: str(tmp_path / "empty" / employee_id))

    class EmptyRuntime:
        def __init__(self, **_kwargs):
            pass

        def run(self, **_kwargs):
            return RuntimeResult(mode="harness", ok=False, result="Harness 未返回内容",
                                 empty_output=True)

    monkeypatch.setattr(harness_agent, "DockerHarnessRuntimeAdapter", EmptyRuntime)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_busy", lambda *_args: None)
    monkeypatch.setattr(harness_agent.runtime_manager, "mark_ready", lambda *_args: None)

    with pytest.raises(HTTPException) as exc:
        harness_agent.run_agent(
            db_session, employee_id="AI-AUDIT", requester_human_no="E10281", message="核对材料",
        )
    assert exc.value.status_code == 503
