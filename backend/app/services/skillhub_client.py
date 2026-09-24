"""内网 SkillHub 技能市场的只读客户端。

实测契约（2026-09-21 复测通过，门户免鉴权）：

* 目录 ``GET {base}/skillhub/api/v1/portal/skills/query?page=&pageSize=&keyword=``
  → ``{"code":200,"data":{"list":[...],"total":129,"page":1,"pageSize":N}}``
* 分类 ``GET {base}/skillhub/api/v1/portal/skills/categories``
  → ``{"code":200,"data":{"categories":[{"category":"…","desc":"…"}]}}``
* 详情 ``GET {base}/skillhub/api/v1/portal/skills/{slug}``
  —— **不要用 ``/detail`` 子路径**，会被当成 slug 返回 ``Skill not found: detail``。
* 下载 ``GET {base}/skillhub/api/v1/portal/skills/download?slug=&version=&source=``
  —— ``version`` / ``source`` 必须取自目录项，缺参时服务端按序报
  ``缺少请求参数: version`` / ``缺少请求参数: source``。

本模块只做网络与解析，不落库、不写文件；异常统一收敛为 ``SkillHubError``，
消息可直接展示给管理员（不含凭据，因为该服务本身免鉴权）。
"""

from __future__ import annotations

import json
import logging
import threading
import time
from dataclasses import dataclass, field, replace
from typing import Any, Iterable

import httpx

from . import config

logger = logging.getLogger(__name__)

API_PREFIX = "/skillhub/api/v1/portal/skills"
MAX_PAGE_SIZE = 100
# 详情接口不带 tags，靠目录索引补齐；TTL 比目录缓存长，避免批量导入反复翻页。
TAGS_INDEX_TTL = 300.0

# 分类词表（``/categories`` 实测 19 类）。远端 ``category`` 字段全量为 null，
# 唯一分类线索是 ``tags``；这里内置一份词表，保证解析分类不依赖网络可达性。
DEFAULT_SKILL_CATEGORIES: tuple[str, ...] = (
    "AI大赛-财富管理赛道",
    "AI大赛-机构服务赛道",
    "AI大赛-办公助手赛道",
    "AI大赛-内控管理赛道",
    "AI大赛-运营管理赛道",
    "AI大赛-AI研发赛道",
    "产品经理",
    "沙箱执行",
    "测试工具",
    "数据治理",
    "知识管理",
    "生活服务",
    "AI智能",
    "开发编程",
    "办公效率",
    "数据分析",
    "内容创作",
    "安全合规",
    "通讯协作",
)
# 运营推荐位：命中这些 tag 的技能在员工货架置顶。
FEATURED_TAGS: frozenset[str] = frozenset({"推荐", "精选", "热门"})
# tags 里的近义词 → 分类词表。只收录实测出现过的取值，不做无据推测。
CATEGORY_ALIASES: dict[str, str] = {
    "财富管理": "AI大赛-财富管理赛道",
    "机构业务": "AI大赛-机构服务赛道",
    "机构服务": "AI大赛-机构服务赛道",
    "效率提升": "办公效率",
}
# 目录/分类结果的进程内缓存：管理端开页会反复请求，缓存 60 秒避免把远端打爆。
_CACHE_LOCK = threading.Lock()
_CACHE: dict[str, tuple[float, Any]] = {}
# ``{slug: tags}`` 索引（详情接口不带 tags，靠它补齐），TTL 见 ``TAGS_INDEX_TTL``。
_TAGS_INDEX: dict[str, tuple[str, ...]] = {}
_TAGS_INDEX_AT: float = 0.0


class SkillHubError(RuntimeError):
    """SkillHub 未配置、不可达，或返回了非预期结果。"""


@dataclass(frozen=True)
class SkillHubSkill:
    """SkillHub 目录项（列表与详情共用同一组字段）。"""

    slug: str
    display_name: str
    summary: str
    version: str
    source: str
    category: str = ""
    downloads: int = 0
    stars: int = 0
    updated_at: int | None = None
    namespace: str = ""
    author: str = ""
    department: str = ""
    tags: tuple[str, ...] = field(default_factory=tuple)
    download_disabled: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "slug": self.slug,
            "display_name": self.display_name,
            "summary": self.summary,
            "version": self.version,
            "source": self.source,
            "category": self.category,
            "downloads": self.downloads,
            "stars": self.stars,
            "updated_at": self.updated_at,
            "namespace": self.namespace,
            "author": self.author,
            "department": self.department,
            "tags": list(self.tags),
            "featured": is_featured_tags(self.tags),
            "download_disabled": self.download_disabled,
        }


def invalidate_cache() -> None:
    """清空目录/分类缓存与 tags 索引（管理端手动刷新或导入后调用）。"""
    global _TAGS_INDEX, _TAGS_INDEX_AT
    with _CACHE_LOCK:
        _CACHE.clear()
        _TAGS_INDEX = {}
        _TAGS_INDEX_AT = 0.0


def _cache_get(key: str) -> Any | None:
    ttl = config.skillhub_cache_ttl()
    if ttl <= 0:
        return None
    with _CACHE_LOCK:
        entry = _CACHE.get(key)
    if entry is None:
        return None
    stored_at, value = entry
    if time.monotonic() - stored_at > ttl:
        with _CACHE_LOCK:
            _CACHE.pop(key, None)
        return None
    return value


def _cache_put(key: str, value: Any) -> None:
    if config.skillhub_cache_ttl() <= 0:
        return
    with _CACHE_LOCK:
        _CACHE[key] = (time.monotonic(), value)


def _base_url() -> str:
    base = config.skillhub_base_url()
    if not base:
        raise SkillHubError("SkillHub 未接入：请配置 DWP_SKILLHUB_BASE_URL")
    if not config.skillhub_enabled():
        raise SkillHubError("SkillHub 接入已被 DWP_SKILLHUB_ENABLED=0 关闭")
    return base


def _client() -> httpx.Client:
    return httpx.Client(
        base_url=_base_url(),
        timeout=config.skillhub_timeout(),
        headers={"Accept": "application/json, */*"},
        follow_redirects=True,
    )


def _request_json(path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
    """发一次 GET 并校验 SkillHub 的业务信封（``code == 200``）。"""
    url = f"{API_PREFIX}{path}"
    try:
        with _client() as client:
            response = client.get(url, params=params)
    except httpx.HTTPError as exc:
        raise SkillHubError(f"SkillHub 请求失败：{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise SkillHubError(
            f"SkillHub 返回 HTTP {response.status_code}：{_error_snippet(response)}"
        )
    try:
        payload = response.json()
    except ValueError as exc:
        raise SkillHubError("SkillHub 返回的不是 JSON") from exc
    if not isinstance(payload, dict):
        raise SkillHubError("SkillHub 返回结构异常")
    code = payload.get("code")
    if code not in (200, "200", None):
        raise SkillHubError(f"SkillHub 业务错误：code={code} msg={payload.get('msg')}")
    data = payload.get("data")
    if not isinstance(data, dict):
        raise SkillHubError(f"SkillHub 返回缺少 data：{_error_snippet(response)}")
    return data


def _error_snippet(response: httpx.Response) -> str:
    text = (response.text or "").strip().replace("\n", " ")
    return text[:200] or "（空响应）"


def _as_int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _as_tags(value: Any) -> tuple[str, ...]:
    """把 SkillHub 的 tags 规整为字符串元组。

    远端实测返回的是**字符串形态的 JSON 数组**（``'["办公效率", "数据分析"]'``），
    也混有 ``'null'``、``'"推荐"]'``、``'["办公效率": 3'`` 这类残缺值，
    所以先按 JSON 解析，失败再退化为「剥括号引号 + 逗号/冒号切分」。
    """
    if isinstance(value, (list, tuple, set)):
        raw_items: list[Any] = list(value)
    elif isinstance(value, str):
        text = value.strip()
        if not text:
            return ()
        try:
            parsed = json.loads(text)
        except (TypeError, ValueError):
            parsed = None
        if isinstance(parsed, list):
            raw_items = list(parsed)
        elif parsed is None:
            raw_items = _split_loose_tags(text)
        else:
            raw_items = [parsed]
    else:
        return ()
    tags: list[str] = []
    for item in raw_items:
        token = str(item or "").strip().strip("[]\"'").strip()
        if not token or token.lower() in {"null", "none", "nan"}:
            continue
        if token not in tags:
            tags.append(token)
    return tuple(tags)


def _split_loose_tags(text: str) -> list[str]:
    """残缺 tags 的兜底切分：``'"推荐"]'`` → ``['推荐']``。"""
    parts: list[str] = []
    for chunk in text.strip().strip("[]").split(","):
        # ``["办公效率": 3`` 这类脏值只取冒号前的片段。
        parts.append(chunk.split(":")[0])
    return parts


def skill_category_from_tags(
    tags: Iterable[str] | None, *, vocabulary: Iterable[str] | None = None,
) -> str:
    """用「tags ∩ 分类词表」得出技能业务分类；无命中返回空串（交给管理端人工标注）。

    上游 tags 里混有近义词（如 ``机构业务``、``财富管理``），先按 ``CATEGORY_ALIASES``
    归一化再匹配，能多归几类。实测 131 条里仍有 37 条完全没有 tags —— 那些只能人工标注。
    """
    allowed = set(vocabulary or DEFAULT_SKILL_CATEGORIES)
    for tag in tags or ():
        if tag in allowed:
            return tag
        alias = CATEGORY_ALIASES.get(tag)
        if alias and alias in allowed:
            return alias
    return ""


def is_featured_tags(tags: Iterable[str] | None) -> bool:
    """tags 里出现「推荐 / 精选 / 热门」即视为运营推荐位。"""
    return any(tag in FEATURED_TAGS for tag in tags or ())


def skill_from_payload(item: dict[str, Any]) -> SkillHubSkill:
    """把 SkillHub 的列表项/详情体转成 ``SkillHubSkill``。

    远端 ``category`` 恒为 null，因此分类实际来自 ``tags`` 与分类词表的交集；
    显式返回了 ``category`` 时以显式值为准。
    """
    tags = _as_tags(item.get("tags"))
    return SkillHubSkill(
        slug=str(item.get("slug") or "").strip(),
        display_name=str(item.get("displayName") or item.get("display_name") or item.get("slug") or "").strip(),
        summary=str(item.get("summary") or item.get("description") or "").strip(),
        version=str(item.get("version") or "").strip(),
        source=str(item.get("source") or "").strip(),
        category=str(item.get("category") or "").strip() or skill_category_from_tags(tags),
        downloads=_as_int(item.get("downloads")),
        stars=_as_int(item.get("stars")),
        updated_at=_as_int(item.get("updatedAt") or item.get("updated_at")) or None,
        namespace=str(item.get("namespace") or "").strip(),
        author=str(item.get("author") or "").strip(),
        department=str(item.get("department") or "").strip(),
        tags=tags,
        download_disabled=bool(item.get("downloadDisabled") or item.get("download_disabled")),
    )


def list_skills(
    *, page: int = 1, page_size: int = 20, keyword: str | None = None,
) -> tuple[list[SkillHubSkill], int]:
    """目录分页查询；返回 ``(技能列表, 总数)``。

    ``keyword`` 是服务端唯一支持的过滤参数（``search``/``q``/``sort`` 实测被忽略）。
    """
    safe_page = max(1, int(page or 1))
    safe_size = max(1, min(MAX_PAGE_SIZE, int(page_size or 20)))
    query = (keyword or "").strip()
    cache_key = f"list:{safe_page}:{safe_size}:{query}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _request_json("/query", {"page": safe_page, "pageSize": safe_size, **({"keyword": query} if query else {})})
    raw_items = data.get("list")
    items = raw_items if isinstance(raw_items, list) else []
    skills = [skill_from_payload(item) for item in items if isinstance(item, dict)]
    total = _as_int(data.get("total")) or len(skills)
    result = (skills, total)
    _cache_put(cache_key, result)
    return result


def list_all_skills(*, keyword: str | None = None, page_size: int = MAX_PAGE_SIZE) -> list[SkillHubSkill]:
    """翻页取全量目录（供批量导入用）。"""
    skills: list[SkillHubSkill] = []
    page = 1
    while True:
        batch, total = list_skills(page=page, page_size=page_size, keyword=keyword)
        if not batch:
            break
        skills.extend(batch)
        if len(skills) >= total or len(batch) < page_size:
            break
        page += 1
    return skills


def list_categories() -> list[dict[str, str]]:
    """分类清单（实测 19 类）。注意：技能列表项的 ``category`` 字段恒为 null，
    该清单只用于展示与「tags ∩ 词表」的分类映射，不能直接按它筛技能。"""
    cache_key = "categories"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached
    data = _request_json("/categories")
    raw = data.get("categories")
    items = raw if isinstance(raw, list) else []
    categories: list[dict[str, str]] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                categories.append({"category": name, "desc": ""})
            continue
        if not isinstance(item, dict):
            continue
        name = str(item.get("category") or item.get("name") or "").strip()
        if not name:
            continue
        categories.append({"category": name, "desc": str(item.get("desc") or item.get("description") or "").strip()})
    _cache_put(cache_key, categories)
    return categories


def get_skill(slug: str) -> SkillHubSkill:
    """技能详情（含 ``downloadDisabled``）。

    **远端详情接口不返回 tags**（只有列表接口有），而技能分类与推荐位都靠 tags 推导，
    所以详情缺 tags 时用目录索引补上，否则导入后分类会整片为空。
    """
    target = (slug or "").strip()
    if not target:
        raise SkillHubError("缺少技能标识 slug")
    skill = skill_from_payload(_request_json(f"/{target}"))
    if skill.tags:
        return skill
    tags = _tags_index().get(target)
    if not tags:
        return skill
    return replace(skill, tags=tags, category=skill.category or skill_category_from_tags(tags))


def _tags_index(*, force: bool = False) -> dict[str, tuple[str, ...]]:
    """``{slug: tags}`` 索引，供详情缺 tags 时补齐。

    独立 TTL（比目录缓存长）是为了让批量导入只多拉几次目录：一次导入要处理上百条，
    每条都重新翻页取目录既慢又没必要。目录不可达时返回空表，不抛异常 —— tags 是
    锦上添花，不该让导入失败。
    """
    global _TAGS_INDEX, _TAGS_INDEX_AT
    # 目录缓存被显式关掉（TTL=0）时，索引也不该偷偷缓存。
    cacheable = config.skillhub_cache_ttl() > 0
    now = time.monotonic()
    with _CACHE_LOCK:
        if not force and cacheable and _TAGS_INDEX and now - _TAGS_INDEX_AT < TAGS_INDEX_TTL:
            return dict(_TAGS_INDEX)
    try:
        index = {item.slug: item.tags for item in list_all_skills()}
    except SkillHubError as exc:
        logger.info("SkillHub 目录不可用，tags 索引留空：%s", exc)
        index = {}
    with _CACHE_LOCK:
        _TAGS_INDEX = index
        _TAGS_INDEX_AT = time.monotonic()
    return dict(index)


def download_bundle(*, slug: str, version: str, source: str) -> bytes:
    """下载技能包 zip；``version`` / ``source`` 必须取自目录项。"""
    target = (slug or "").strip()
    if not target:
        raise SkillHubError("缺少技能标识 slug")
    if not (version or "").strip():
        raise SkillHubError("下载技能需要版本号 version（取自目录项的 version 字段）")
    if not (source or "").strip():
        raise SkillHubError("下载技能需要来源 source（取自目录项的 source 字段）")
    params = {"slug": target, "version": version.strip(), "source": source.strip()}
    try:
        with _client() as client:
            response = client.get(f"{API_PREFIX}/download", params=params)
    except httpx.HTTPError as exc:
        raise SkillHubError(f"SkillHub 下载失败：{type(exc).__name__}: {exc}") from exc
    if response.status_code != 200:
        raise SkillHubError(
            f"SkillHub 下载返回 HTTP {response.status_code}：{_error_snippet(response)}"
        )
    content = response.content or b""
    if not content.startswith(b"PK"):
        # 服务端错误时仍可能返回 200 + JSON 信封，这里兜住，避免把 JSON 当 zip 存盘。
        raise SkillHubError(f"SkillHub 下载结果不是 ZIP：{_error_snippet(response)}")
    return content
