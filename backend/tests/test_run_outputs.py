"""AI员工工作区产物（核对表等）的快照、预览与下载。

背景：模型在答复里只会写「完整核对表已保存至 /workspace/output/核对表.md」。
用户看不到、也拿不到那个文件，而它才是这一轮真正要交付的东西。这一层把它取出来、
快照下来、给一个 URL。

覆盖「不这样写就会错」的几处：JSON 中间件不能当产物暴露、平台自己写的目录不算产物、
上一轮的旧文件不算本轮产物、同一执行重复快照不重复入库、别人的会话读不到、
清空会话要连快照文件一起删、storage_path 被篡改要拒绝。
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from urllib.parse import unquote

import pytest
from sqlalchemy import select

from app import models
from app.services import execution_events, run_outputs, workspace_readback

CONVERSATION_URL = "/api/v1/conversations"


def _login(client) -> None:
    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-e10281"}
    ).status_code == 200


def _conversation(client) -> str:
    response = client.post(CONVERSATION_URL, json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    })
    assert response.status_code == 201
    return response.json()["id"]


def _execution(db, conversation_id: str):
    execution = execution_events.create_execution(
        db, conversation_id=conversation_id, trigger_message_seq=1,
        primary_employee_id="AI-AUDIT", conversation_generation=0,
    )
    db.commit()
    return execution


@pytest.fixture()
def env(tmp_path, monkeypatch):
    """把工作区与产物根都指向临时目录，并隔离成同一份。"""
    workspace = tmp_path / "workspace"
    outputs = tmp_path / "run-outputs"
    workspace.mkdir()
    outputs.mkdir()
    monkeypatch.setattr(workspace_readback, "workspace_dir", lambda employee_id: workspace)
    monkeypatch.setenv("DWP_RUN_OUTPUT_ROOT", str(outputs))
    return workspace, outputs


def _write(workspace: Path, relative: str, text: str, *, mtime: float | None = None) -> Path:
    path = workspace / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        os.utime(path, (mtime, mtime))
    return path


def test_snapshot_collects_kinds_and_copies_files(env, db_session, client):
    workspace, outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)

    _write(workspace, "output/核对表.md", "# 核对表\n\n内容")
    _write(workspace, "output/核对表.html", "<html><body>核对表</body></html>")
    _write(workspace, "output/明细.csv", "字段,结果\n企业名称,一致")
    # 这几份都不该进产物：JSON 中间件、平台投放的技能包、隐藏目录
    _write(workspace, "output/findings.json", '{"findings": []}')
    _write(workspace, "skills/demo/SKILL.md", "# 技能说明")
    _write(workspace, ".cache/tmp.md", "临时")

    saved = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT")
    by_name = {row.filename: row for row in saved}
    assert set(by_name) == {"核对表.html", "核对表.md", "明细.csv"}
    assert by_name["核对表.md"].kind == "markdown"
    assert by_name["核对表.html"].kind == "html"
    assert by_name["明细.csv"].kind == "csv"
    assert by_name["核对表.md"].mime_type.startswith("text/markdown")
    assert by_name["核对表.md"].name == "output/核对表.md"
    assert by_name["核对表.md"].sha256
    # 快照文件真的落盘了，且与工作区那份内容一致
    copied = run_outputs.resolve_path(by_name["核对表.md"])
    assert copied.is_file()
    assert copied.read_text(encoding="utf-8") == "# 核对表\n\n内容"
    assert copied.is_relative_to(outputs.resolve())


def test_snapshot_only_takes_current_run_and_is_idempotent(env, db_session, client):
    workspace, _outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)

    _write(workspace, "output/旧表.md", "上一轮的", mtime=time.time() - 600)
    started_at = time.time()
    _write(workspace, "output/核对表.md", "本轮的")

    saved = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT", since=started_at)
    assert [row.filename for row in saved] == ["核对表.md"]

    again = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT", since=started_at)
    assert [row.id for row in again] == [row.id for row in saved]
    rows = db_session.scalars(select(models.ExecutionArtifact).where(
        models.ExecutionArtifact.execution_id == execution.id)).all()
    assert len(rows) == 1


def test_content_endpoint_inline_download_and_owner_check(env, db_session, client):
    workspace, _outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)
    _write(workspace, "output/核对表.html", "<html><body>核对表</body></html>")
    saved = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT")
    artifact_id = saved[0].id
    url = f"{CONVERSATION_URL}/{conversation_id}/artifacts/{artifact_id}/content"

    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert "inline" in response.headers["content-disposition"]
    assert response.text == "<html><body>核对表</body></html>"

    download = client.get(url + "?download=1")
    assert download.status_code == 200
    disposition = download.headers["content-disposition"]
    assert "attachment" in disposition
    # 中文文件名走 RFC 5987 的 filename*，断言前先解码
    assert "核对表.html" in unquote(disposition)

    listed = client.get(f"{CONVERSATION_URL}/{conversation_id}/artifacts")
    assert listed.status_code == 200
    assert [row["artifact_id"] for row in listed.json()] == [artifact_id]

    # 换个人来读：会话归属校验要拦住
    assert client.post("/api/v1/auth/key-login",
                       json={"access_key": "test-key-e20999"}).status_code == 200
    assert client.get(url).status_code == 403
    assert client.get(
        f"{CONVERSATION_URL}/{conversation_id}/artifacts/{artifact_id}/content"
    ).status_code == 403


def test_content_404_when_missing_or_tampered(env, db_session, client):
    workspace, outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)
    _write(workspace, "output/核对表.md", "# 核对表")
    row = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT")[0]
    url = f"{CONVERSATION_URL}/{conversation_id}/artifacts/{row.id}/content"

    assert client.get(
        f"{CONVERSATION_URL}/{conversation_id}/artifacts/ART-NOPE/content").status_code == 404

    # 快照文件被删掉（磁盘丢了）
    run_outputs.resolve_path(row).unlink()
    assert client.get(url).status_code == 404

    # storage_path 被改成越界路径：必须拒绝，不能读到根目录外的文件
    row.storage_path = "../../../etc/hosts"
    db_session.commit()
    assert client.get(url).status_code == 404
    with pytest.raises(FileNotFoundError):
        run_outputs.resolve_path(row)
    assert outputs.exists()


def test_artifacts_come_back_with_run_history(env, db_session, client):
    workspace, _outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)
    _write(workspace, "output/核对表.md", "# 核对表")
    run_outputs.snapshot_run(db_session, execution, "AI-AUDIT")

    history = client.get(f"{CONVERSATION_URL}/{conversation_id}/runs/history")
    assert history.status_code == 200
    entries = history.json()
    assert len(entries) == 1
    artifacts = entries[0]["artifacts"]
    assert [item["filename"] for item in artifacts] == ["核对表.md"]
    assert artifacts[0]["execution_id"] == execution.id
    assert artifacts[0]["kind"] == "markdown"

    latest = client.get(f"{CONVERSATION_URL}/{conversation_id}/runs/latest")
    assert latest.status_code == 200
    assert [item["filename"] for item in latest.json()["artifacts"]] == ["核对表.md"]


def test_purge_removes_artifact_rows_and_files(env, db_session, client):
    workspace, outputs = env
    _login(client)
    conversation_id = _conversation(client)
    execution = _execution(db_session, conversation_id)
    _write(workspace, "output/核对表.md", "# 核对表")
    row = run_outputs.snapshot_run(db_session, execution, "AI-AUDIT")[0]
    path = run_outputs.resolve_path(row)
    assert path.is_file()

    response = client.post(f"{CONVERSATION_URL}/{conversation_id}/clear?actor_no=E10281")
    assert response.status_code == 200
    db_session.expire_all()
    assert db_session.scalars(select(models.ExecutionArtifact).where(
        models.ExecutionArtifact.conversation_id == conversation_id)).all() == []
    assert not path.exists()
    assert outputs.is_dir()  # 只删该会话的目录，产物根保留
