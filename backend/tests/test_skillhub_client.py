"""SkillHub 客户端单元测试（httpx.MockTransport，不连真实服务）。

覆盖实测契约里的四个接口与三类失败：业务码非 200、HTTP 非 200、下载结果不是 zip，
以及「缺参按序报错」对应的本地前置校验。
"""

from __future__ import annotations

import httpx
import pytest

from app.services import config, skillhub_client


@pytest.fixture(autouse=True)
def _skillhub_env(monkeypatch):
    monkeypatch.setenv(config.SKILLHUB_BASE_URL, "http://skillhub.test")
    monkeypatch.setenv(config.SKILLHUB_CACHE_TTL, "0")
    skillhub_client.invalidate_cache()
    yield
    skillhub_client.invalidate_cache()


def _install(monkeypatch, handler) -> None:
    # 每次调用都新建 client（生产代码用 with 包住并关闭），因此这里返回工厂而不是实例；
    # 且必须带 base_url：httpx 提取 cookie 时需要绝对 URL，相对路径会直接抛异常。
    monkeypatch.setattr(
        skillhub_client, "_client",
        lambda: httpx.Client(base_url="http://skillhub.test", transport=httpx.MockTransport(handler)),
    )


def _item(slug: str = "docx", **overrides) -> dict:
    payload = {
        "slug": slug,
        "displayName": "文档处理",
        "summary": "生成与解析 docx",
        "version": "0.0.2",
        "source": "skillhub",
        "category": "AI大赛-财富管理赛道",
        "downloads": 12,
        "stars": 3,
        "updatedAt": 1758384000000,
        "namespace": "official",
        "tags": ["文档", "office"],
        "author": "张三",
        "department": "财富管理部",
    }
    payload.update(overrides)
    return payload


def test_list_skills_parses_envelope_and_sends_filters(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={
            "code": 200, "msg": "操作成功",
            "data": {"list": [_item()], "total": 129, "page": 1, "pageSize": 20},
        })

    _install(monkeypatch, handler)
    skills, total = skillhub_client.list_skills(page=1, page_size=20, keyword="docx")

    assert total == 129
    assert seen["page"] == "1" and seen["pageSize"] == "20" and seen["keyword"] == "docx"
    assert skills[0].slug == "docx"
    assert skills[0].display_name == "文档处理"
    assert skills[0].version == "0.0.2"
    assert skills[0].source == "skillhub"
    assert skills[0].tags == ("文档", "office")


def test_list_skills_omits_empty_keyword(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, json={"code": 200, "data": {"list": [], "total": 0}})

    _install(monkeypatch, handler)
    skills, total = skillhub_client.list_skills(keyword="   ")
    assert skills == [] and total == 0
    assert "keyword" not in seen


def test_list_skills_raises_on_business_error(monkeypatch):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 500, "msg": "Skill not found: detail"})

    _install(monkeypatch, handler)
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.list_skills()
    assert "code=500" in str(excinfo.value)


def test_list_skills_raises_on_http_error(monkeypatch):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(502, text="bad gateway")

    _install(monkeypatch, handler)
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.list_skills()
    assert "HTTP 502" in str(excinfo.value)


def test_list_categories_parses_and_caches(monkeypatch):
    calls = {"count": 0}
    monkeypatch.setenv(config.SKILLHUB_CACHE_TTL, "60")

    def handler(_: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        return httpx.Response(200, json={
            "code": 200,
            "data": {"categories": [{"category": "AI大赛-财富管理赛道", "desc": "数字化人才大赛"}]},
        })

    _install(monkeypatch, handler)
    first = skillhub_client.list_categories()
    second = skillhub_client.list_categories()

    assert first == [{"category": "AI大赛-财富管理赛道", "desc": "数字化人才大赛"}]
    assert second == first
    assert calls["count"] == 1, "TTL 内应命中进程内缓存"


def test_get_skill_uses_slug_path_not_detail(monkeypatch):
    paths: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        paths.append(request.url.path)
        return httpx.Response(200, json={"code": 200, "data": _item(downloadDisabled=True)})

    _install(monkeypatch, handler)
    skill = skillhub_client.get_skill("docx")

    assert paths == ["/skillhub/api/v1/portal/skills/docx"]
    assert skill.download_disabled is True


def test_download_bundle_requires_version_and_source(monkeypatch):
    _install(monkeypatch, lambda _: httpx.Response(200, content=b"PK\x03\x04"))
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.download_bundle(slug="docx", version="", source="skillhub")
    assert "version" in str(excinfo.value)
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.download_bundle(slug="docx", version="0.0.2", source="")
    assert "source" in str(excinfo.value)


def test_download_bundle_returns_zip_bytes(monkeypatch):
    seen: dict[str, str] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(dict(request.url.params))
        return httpx.Response(200, content=b"PK\x03\x04payload", headers={"content-type": "application/zip"})

    _install(monkeypatch, handler)
    data = skillhub_client.download_bundle(slug="docx", version="0.0.2", source="skillhub")
    assert data.startswith(b"PK")
    assert seen == {"slug": "docx", "version": "0.0.2", "source": "skillhub"}


def test_download_bundle_rejects_json_body(monkeypatch):
    def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"code": 400, "msg": "缺少请求参数: version"})

    _install(monkeypatch, handler)
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.download_bundle(slug="docx", version="0.0.2", source="skillhub")
    assert "不是 ZIP" in str(excinfo.value)


def test_unconfigured_raises_readable_error(monkeypatch):
    monkeypatch.delenv(config.SKILLHUB_BASE_URL, raising=False)
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.list_skills()
    assert "DWP_SKILLHUB_BASE_URL" in str(excinfo.value)


def test_disabled_switch_blocks_calls(monkeypatch):
    monkeypatch.setenv(config.SKILLHUB_ENABLED, "0")
    with pytest.raises(skillhub_client.SkillHubError) as excinfo:
        skillhub_client.list_skills()
    assert "DWP_SKILLHUB_ENABLED" in str(excinfo.value)


def test_list_all_skills_paginates(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        items = [_item(f"skill-{page}")] if page <= 2 else []
        return httpx.Response(200, json={
            "code": 200, "data": {"list": items, "total": 2, "page": page, "pageSize": 1},
        })

    _install(monkeypatch, handler)
    skills = skillhub_client.list_all_skills(page_size=1)
    assert [skill.slug for skill in skills] == ["skill-1", "skill-2"]


# ---- tags 脏值解析（远端实测混有 JSON 字符串、null、残缺括号）----

@pytest.mark.parametrize(("raw", "expected"), [
    ('["开发编程"]', ("开发编程",)),
    ('["办公效率", "数据分析"]', ("办公效率", "数据分析")),
    ("null", ()),
    (None, ()),
    ('"推荐"]', ("推荐",)),
    ('["办公效率": 3', ("办公效率",)),
    ('["AI智能", "null"]', ("AI智能",)),
    ("[]", ()),
    (["文档", "office"], ("文档", "office")),
    ("", ()),
])
def test_as_tags_handles_dirty_payloads(raw, expected):
    assert skillhub_client._as_tags(raw) == expected


def test_skill_from_payload_derives_category_from_tags():
    item = _item(category=None, tags='["办公效率", "推荐"]')
    skill = skillhub_client.skill_from_payload(item)

    assert skill.category == "办公效率"
    assert skillhub_client.is_featured_tags(skill.tags) is True


def test_skill_from_payload_keeps_explicit_category():
    skill = skillhub_client.skill_from_payload(_item(category="数据分析", tags='["办公效率"]'))
    assert skill.category == "数据分析"


def test_skill_from_payload_leaves_unmapped_category_empty():
    skill = skillhub_client.skill_from_payload(_item(category=None, tags='["推荐"]'))
    assert skill.category == ""
    assert skillhub_client.is_featured_tags(skill.tags) is True


def test_skill_category_from_tags_accepts_custom_vocabulary():
    assert skillhub_client.skill_category_from_tags(("自建分类",), vocabulary=("自建分类",)) == "自建分类"
    assert skillhub_client.skill_category_from_tags(("自建分类",)) == ""


@pytest.mark.parametrize(("tag", "expected"), [
    ("机构业务", "AI大赛-机构服务赛道"),
    ("财富管理", "AI大赛-财富管理赛道"),
    ("效率提升", "办公效率"),
])
def test_skill_category_from_tags_normalizes_aliases(tag, expected):
    assert skillhub_client.skill_category_from_tags((tag,)) == expected


def test_get_skill_merges_tags_from_catalog(monkeypatch):
    """远端**详情接口不返回 tags**，必须从目录补齐，否则导入后分类整片为空。"""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query"):
            return httpx.Response(200, json={
                "code": 200, "data": {"list": [_item(tags='["办公效率"]')], "total": 1},
            })
        # 详情体没有 tags 字段（与线上实测一致）
        return httpx.Response(200, json={"code": 200, "data": _item(category=None, tags=None)})

    _install(monkeypatch, handler)
    skill = skillhub_client.get_skill("docx")

    assert skill.tags == ("办公效率",)
    assert skill.category == "办公效率"


def test_get_skill_keeps_detail_category_when_catalog_unavailable(monkeypatch):
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/query"):
            return httpx.Response(500, text="boom")
        return httpx.Response(200, json={"code": 200, "data": _item(tags=None)})

    _install(monkeypatch, handler)
    skill = skillhub_client.get_skill("docx")

    assert skill.tags == ()
    assert skill.category == "AI大赛-财富管理赛道"
