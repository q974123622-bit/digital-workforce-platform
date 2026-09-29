"""附件预览（原文件字节 + 缩略图）的回归测试。

覆盖「用户会踩、但代码里看不出来」的几处：类型判定出参、归属校验、
deleted/文件缺失一律 404、非预览类型拒绝、storage_path 被篡改成越界路径时
拒绝读取、缩略图长边收敛与二次命中缓存、以及清空会话后缩略图缓存一并清除。
"""

from __future__ import annotations

import io
from pathlib import Path

import pytest
from PIL import Image

from app import models
from app.services import attachment_preview

MIME = {
    "png": "image/png", "jpg": "image/jpeg", "pdf": "application/pdf",
    "txt": "text/plain",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _login_and_conversation(client):
    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-e10281"}
    ).status_code == 200
    response = client.post("/api/v1/conversations", json={
        "actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"],
    })
    assert response.status_code == 201
    return response.json()["id"]


def _png_bytes(size=(900, 600), color=(200, 40, 40)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "PNG")
    return buffer.getvalue()


def _jpeg_bytes(size=(300, 700), color=(20, 90, 160)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGB", size, color).save(buffer, "JPEG")
    return buffer.getvalue()


def _pdf_bytes() -> bytes:
    """手写一个最小可渲染 PDF（无文本层，走渲染分支）。"""
    content = b"0.9 0.2 0.2 rg 10 10 180 100 re f"
    objects = [
        b"<</Type/Catalog/Pages 2 0 R>>",
        b"<</Type/Pages/Kids[3 0 R]/Count 1>>",
        b"<</Type/Page/Parent 2 0 R/MediaBox[0 0 200 120]/Contents 4 0 R>>",
        b"<</Length %d>>stream\n" % len(content) + content + b"\nendstream",
    ]
    out = bytearray(b"%PDF-1.4\n")
    offsets: list[int] = []
    for index, obj in enumerate(objects, start=1):
        offsets.append(len(out))
        out += b"%d 0 obj\n" % index + obj + b"\nendobj\n"
    xref_pos = len(out)
    size = len(objects) + 1
    out += b"xref\n0 %d\n" % size
    out += b"0000000000 65535 f \n"
    for offset in offsets:
        out += b"%010d 00000 n \n" % offset
    out += b"trailer\n<</Size %d/Root 1 0 R>>\nstartxref\n%d\n%%%%EOF\n" % (size, xref_pos)
    return bytes(out)


def _upload(client, conversation_id: str, name: str, data: bytes) -> dict:
    response = client.post(
        f"/api/v1/conversations/{conversation_id}/attachments",
        files={"file": (name, data, MIME[name.rsplit(".", 1)[-1].lower()])},
    )
    assert response.status_code == 200, response.text
    return response.json()


@pytest.fixture(autouse=True)
def _no_vision(monkeypatch):
    """图片上传会触发视觉转写；测试里关掉，避免打真实网关。"""
    monkeypatch.setenv("DWP_ATTACHMENT_VISION_ENABLED", "0")


def test_listing_exposes_preview_kind(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    png = _upload(client, conversation_id, "开户截图.png", _png_bytes())
    pdf = _upload(client, conversation_id, "开户合同.pdf", _pdf_bytes())
    txt = _upload(client, conversation_id, "备注.txt", b"hello")
    docx_buffer = io.BytesIO()
    from docx import Document

    document = Document()
    document.add_paragraph("制度说明")
    document.save(docx_buffer)
    docx = _upload(client, conversation_id, "制度说明.docx", docx_buffer.getvalue())

    rows = {
        row["attachment_id"]: row
        for row in client.get(f"/api/v1/conversations/{conversation_id}/attachments").json()
    }
    assert rows[png["attachment_id"]]["preview_kind"] == "image"
    assert rows[pdf["attachment_id"]]["preview_kind"] == "pdf"
    assert rows[txt["attachment_id"]]["preview_kind"] == "none"
    assert rows[docx["attachment_id"]]["preview_kind"] == "none"


def test_content_returns_bytes_inline_then_download(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    payload = _png_bytes()
    uploaded = _upload(client, conversation_id, "开户截图.png", payload)
    url = f"/api/v1/conversations/{conversation_id}/attachments/{uploaded['attachment_id']}/content"

    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/png")
    assert "inline" in response.headers["content-disposition"]
    assert response.content == payload

    download = client.get(url + "?download=1")
    assert download.status_code == 200
    assert "attachment" in download.headers["content-disposition"]


def test_content_rejects_other_owner(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户截图.png", _png_bytes())

    assert client.post(
        "/api/v1/auth/key-login", json={"access_key": "test-key-e20999"}
    ).status_code == 200
    response = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{uploaded['attachment_id']}/content"
    )
    assert response.status_code == 403


def test_content_404_after_delete_and_when_file_missing(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户截图.png", _png_bytes())
    attachment_id = uploaded["attachment_id"]
    url = f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}/content"

    source = tmp_path / "E10281" / conversation_id / attachment_id / "开户截图.png"
    assert source.is_file()
    source.unlink()
    assert client.get(url).status_code == 404

    assert client.delete(
        f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}"
    ).status_code == 204
    assert client.get(url).status_code == 404


def test_thumbnail_is_jpeg_bounded_and_cached(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户截图.png", _png_bytes(size=(1600, 1000)))
    attachment_id = uploaded["attachment_id"]
    url = f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}/thumbnail"

    response = client.get(url)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("image/jpeg")
    with Image.open(io.BytesIO(response.content)) as image:
        assert max(image.size) <= attachment_preview.THUMB_WIDTH
        assert image.mode == "RGB"

    cached = tmp_path / "_thumbs" / attachment_id
    assert list(cached.glob("*.jpg"))
    again = client.get(url)
    assert again.status_code == 200
    assert again.content == response.content


def test_pdf_thumbnail_and_content(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户合同.pdf", _pdf_bytes())
    attachment_id = uploaded["attachment_id"]

    thumbnail = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}/thumbnail"
    )
    assert thumbnail.status_code == 200, thumbnail.text
    with Image.open(io.BytesIO(thumbnail.content)) as image:
        assert max(image.size) <= attachment_preview.THUMB_WIDTH

    content = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}/content"
    )
    assert content.status_code == 200
    assert content.headers["content-type"].startswith("application/pdf")


def test_thumbnail_rejects_non_previewable(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "备注.txt", b"hello")
    response = client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{uploaded['attachment_id']}/thumbnail"
    )
    assert response.status_code == 404


def test_tampered_storage_path_is_refused(client, db_session, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户截图.png", _png_bytes())
    attachment_id = uploaded["attachment_id"]

    row = db_session.get(models.ConversationAttachment, attachment_id)
    row.storage_path = "../../../etc/hosts"
    db_session.commit()

    base = f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}"
    assert client.get(f"{base}/thumbnail").status_code == 422
    assert client.get(f"{base}/content").status_code == 404


def test_purge_removes_thumbnail_cache(client, monkeypatch, tmp_path):
    monkeypatch.setenv("DWP_ATTACHMENT_ROOT", str(tmp_path))
    conversation_id = _login_and_conversation(client)
    uploaded = _upload(client, conversation_id, "开户截图.png", _png_bytes())
    attachment_id = uploaded["attachment_id"]
    assert client.get(
        f"/api/v1/conversations/{conversation_id}/attachments/{attachment_id}/thumbnail"
    ).status_code == 200
    assert (tmp_path / "_thumbs" / attachment_id).is_dir()

    assert client.post(f"/api/v1/conversations/{conversation_id}/clear?actor_no=E10281").status_code == 200
    assert not (tmp_path / "_thumbs" / attachment_id).exists()
    assert Path(tmp_path / "_thumbs").exists()  # 只清该附件的子目录，不删缓存根
