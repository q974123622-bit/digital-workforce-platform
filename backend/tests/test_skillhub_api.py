"""管理端技能市场接口测试：目录代理、分类筛选、批量导入、导入记录。

远端用 ``httpx.MockTransport`` 假响应，不依赖真实 SkillHub。
"""

from __future__ import annotations

import io
import zipfile

import httpx
import pytest

from app.services import config, skillhub_client

SKILL_MD = "---\nname: 文档处理\ndescription: 生成与解析 docx\n---\n\n# 文档处理\n\npython3 scripts/pack.py\n"


@pytest.fixture(autouse=True)
def _skillhub_env(monkeypatch):
    monkeypatch.setenv(config.SKILLHUB_BASE_URL, "http://skillhub.test")
    monkeypatch.setenv(config.SKILLHUB_CACHE_TTL, "0")
    monkeypatch.setenv("DWP_ARTIFACT_ROOT", "/tmp/dwp-test-artifacts")
    skillhub_client.invalidate_cache()
    yield
    skillhub_client.invalidate_cache()


def _bundle(slug: str = "docx") -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr(f"{slug}/SKILL.md", SKILL_MD)
        archive.writestr(f"{slug}/scripts/pack.py", "print('pack')\n")
    return buffer.getvalue()


def _item(slug: str, category: str = "AI大赛-财富管理赛道", **overrides) -> dict:
    payload = {
        "slug": slug, "displayName": f"{slug} 技能", "summary": "一句话说明",
        "version": "0.0.2", "source": "skillhub", "category": category,
        "downloads": 5, "stars": 1, "tags": ["文档"],
    }
    payload.update(overrides)
    return payload


def _install_skillhub(monkeypatch, *, slugs: list[str] | None = None) -> None:
    catalog = [_item(slug) for slug in (slugs or ["docx", "xlsx"])]

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/skills/query"):
            return httpx.Response(200, json={
                "code": 200, "data": {"list": catalog, "total": len(catalog), "page": 1, "pageSize": 100},
            })
        if path.endswith("/skills/categories"):
            return httpx.Response(200, json={
                "code": 200, "data": {"categories": [{"category": "AI大赛-财富管理赛道", "desc": "赛道"}]},
            })
        if path.endswith("/skills/download"):
            return httpx.Response(200, content=_bundle(request.url.params["slug"]),
                                  headers={"content-type": "application/zip"})
        slug = path.rsplit("/", 1)[-1]
        match = next((row for row in catalog if row["slug"] == slug), None)
        if match is None:
            return httpx.Response(200, json={"code": 500, "msg": f"Skill not found: {slug}"})
        return httpx.Response(200, json={"code": 200, "data": match})

    monkeypatch.setattr(
        skillhub_client, "_client",
        lambda: httpx.Client(base_url="http://skillhub.test", transport=httpx.MockTransport(handler)),
    )


def _login_admin(client) -> None:
    response = client.post("/api/v1/auth/login", json={"username": "admin", "password": "Demo@123456"})
    assert response.status_code == 200


def _run_import(client, slugs: list[str], **extra) -> dict:
    """提交批量导入并取回批次进度。

    导入改成后台批次后接口只返回 ``batch_id``；TestClient 会在返回响应前跑完
    BackgroundTasks，所以紧接着查进度就是终态。默认关掉 AI 补简介，避免测试打模型。
    """
    started = client.post(
        "/api/v1/admin/skillhub/import", json={"slugs": slugs, "generate_intro": False, **extra},
    )
    assert started.status_code == 200, started.text
    batch_id = started.json()["batch_id"]
    return client.get(f"/api/v1/admin/skillhub/imports/{batch_id}").json()


def test_overview_lists_categories(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)

    response = client.get("/api/v1/admin/skillhub/overview")
    assert response.status_code == 200
    payload = response.json()
    assert payload["enabled"] is True
    assert payload["categories"][0]["category"] == "AI大赛-财富管理赛道"


def test_overview_reports_unconfigured(client, monkeypatch):
    monkeypatch.delenv(config.SKILLHUB_BASE_URL, raising=False)
    _login_admin(client)
    payload = client.get("/api/v1/admin/skillhub/overview").json()
    assert payload["enabled"] is False
    assert "DWP_SKILLHUB_BASE_URL" in payload["message"]


def test_skills_requires_admin(client, monkeypatch):
    _install_skillhub(monkeypatch)
    response = client.post("/api/v1/auth/login", json={"username": "E10281", "password": "Demo@123456"})
    assert response.status_code == 200
    assert client.get("/api/v1/admin/skillhub/skills").status_code == 403


def test_list_skills_marks_imported(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)

    first = client.get("/api/v1/admin/skillhub/skills").json()
    assert first["total"] == 2
    assert first["imported_total"] == 0
    assert all(item["imported"] is False for item in first["items"])

    imported = _run_import(client, ["docx"])
    assert imported["summary"] == {"imported": 1, "skipped": 0, "failed": 0}

    second = client.get("/api/v1/admin/skillhub/skills").json()
    assert second["imported_total"] == 1
    docx = next(item for item in second["items"] if item["slug"] == "docx")
    assert docx["imported"] is True
    assert docx["imported_version"] == "0.0.2"
    assert docx["imported_plugin_id"].startswith("SKILL-")


def test_list_skills_filters_by_category(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)

    hit = client.get("/api/v1/admin/skillhub/skills", params={"category": "AI大赛-财富管理赛道"}).json()
    assert hit["total"] == 2
    miss = client.get("/api/v1/admin/skillhub/skills", params={"category": "不存在的分类"}).json()
    assert miss["total"] == 0 and miss["items"] == []


def test_import_is_idempotent_and_reports_per_item(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)

    first = _run_import(client, ["docx", "xlsx"])
    assert first["summary"] == {"imported": 2, "skipped": 0, "failed": 0}
    assert first["status"] == "completed" and first["done"] == first["total"] == 2

    second = _run_import(client, ["docx"])
    assert second["summary"] == {"imported": 0, "skipped": 1, "failed": 0}
    assert second["results"][0]["plugin_id"] == first["results"][0]["plugin_id"]


def test_import_reports_unknown_slug_as_failure(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)

    payload = _run_import(client, ["nope"])
    assert payload["summary"] == {"imported": 0, "skipped": 0, "failed": 1}
    assert "Skill not found" in payload["results"][0]["message"]


def test_import_batch_progress_reports_unknown_batch(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)
    payload = client.get("/api/v1/admin/skillhub/imports/SHB-NOT-EXIST").json()
    assert payload["status"] == "unknown" and payload["total"] == 0


def test_import_derives_skill_category_and_featured_from_tags(client, monkeypatch):
    _install_skillhub(monkeypatch, slugs=["kline-query"])
    # 远端 category 恒为 null，分类与推荐位只能来自 tags。
    monkeypatch.setattr(
        skillhub_client, "get_skill",
        lambda slug: skillhub_client.skill_from_payload(
            _item(slug, category=None, tags='["数据分析", "推荐"]'),
        ),
    )
    _login_admin(client)
    _run_import(client, ["kline-query"])

    shelf = client.get("/api/v1/admin/plugins").json()
    row = next(item for item in shelf if item["skill_category"] == "数据分析")
    assert row["featured"] is True
    assert row["source"] == "skillhub"


def test_import_rejects_empty_payload(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)
    assert client.post("/api/v1/admin/skillhub/import", json={"slugs": []}).status_code == 422
    assert client.post("/api/v1/admin/skillhub/import", json={"slugs": ["  "]}).status_code == 422


def test_imported_skill_lands_on_employee_shelf(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)
    _run_import(client, ["docx"])

    client.post("/api/v1/auth/login", json={"username": "E10281", "password": "Demo@123456"})
    shelf = client.get("/api/v1/my/plugins").json()
    rows = shelf.get("market") if isinstance(shelf, dict) else shelf
    docx = [row for row in rows if row.get("plugin_type") == "skill" and row.get("name") == "文档处理"]
    assert docx, "导入的技能应出现在员工侧货架"
    assert docx[0]["state"] == "requestable"
    # 来源徽标：靠 manifest 里的 SkillHub slug 识别，不额外加字段
    assert docx[0]["source"] == "skillhub"


def test_imports_listing_and_cache_refresh(client, monkeypatch):
    _install_skillhub(monkeypatch)
    _login_admin(client)
    progress = _run_import(client, ["docx"])

    listed = client.get("/api/v1/admin/skillhub/imports").json()["items"]
    assert listed[0]["slug"] == "docx" and listed[0]["status"] == "imported"
    assert listed[0]["batch_id"] == progress["batch_id"]
    only_failed = client.get("/api/v1/admin/skillhub/imports", params={"status": "failed"}).json()["items"]
    assert only_failed == []

    assert client.post("/api/v1/admin/skillhub/cache/refresh").json() == {"refreshed": True}


def test_skills_returns_502_when_remote_broken(client, monkeypatch):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    monkeypatch.setattr(
        skillhub_client, "_client",
        lambda: httpx.Client(base_url="http://skillhub.test", transport=httpx.MockTransport(handler)),
    )
    _login_admin(client)
    response = client.get("/api/v1/admin/skillhub/skills")
    assert response.status_code == 502
    assert response.json()["error"]["detail"]["reason"] == "skillhub_unreachable"
