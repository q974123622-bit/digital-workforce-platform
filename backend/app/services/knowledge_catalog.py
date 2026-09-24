"""平台知识库目录：双后端映射的唯一真相。

背景：内部知识引擎存在两条通道 —— 测试环境的 REST 检索接口（``x-tenant=8``）与
生产环境的 VeRAG MCP（``X-Tenant: 6``）。两边库名完全不同（REST 形如
``信息技术部-QA库-20260723142834``，VeRAG 形如 ``信息技术部-QA库``），无法按字符串
匹配，只能按「部门/业务主题」做语义配对。

因此平台侧把每个业务主题登记为**一个**语义化英文 ID 的知识库，并在
``engine_map`` 里同时保存两个远端库 ID::

    {"rest": 751, "verag": 623}

远端库在某一环境不存在时对应键缺失（例如只在生产 VeRAG 上有的库没有 ``rest``）。
运行期按「当前环境 + 各源可达性」自动选路，AI员工授权只配一次、换环境不改。

本目录由 ``backend/tools/kb_catalog_sync.py`` 对着真实引擎枚举结果核对生成；
运行期只读，不再访问远端枚举接口。新增/调整库时先跑该工具，再改这里。
"""

from __future__ import annotations

# 每个条目的字段：
#   id          平台知识库 ID（语义化英文，避开 seed.LEGACY_MOCK_KNOWLEDGE_BASE_IDS）
#   name        中文库名（员工侧展示）
#   domain      领域标签（进 prompt 与核验页）
#   data_level  L1 通用 / L2 内部敏感（Policy 按此分级判定）
#   description 一句话说明
#   rest        测试环境 REST 远端 kb_id（缺失表示该环境无此库）
#   verag       生产环境 VeRAG MCP 远端 knowledge_base_id（缺失同上）
KB_CATALOG: tuple[dict, ...] = (
    {
        "id": "KB-IT-SERVICE",
        "name": "IT 服务知识库",
        "domain": "IT服务",
        "data_level": "L2",
        "description": "IT 服务与办公工具使用问答（内部知识引擎）",
        "rest": 751,
        "verag": 623,
    },
    {
        "id": "KB-SECURITIES",
        "name": "证券业务知识库",
        "domain": "证券业务",
        "data_level": "L2",
        "description": "证券业务规则与操作问答（内部知识引擎）",
        "rest": 756,
        "verag": 655,
    },
    {
        "id": "KB-INVESTMENT-BANKING",
        "name": "投行资讯知识库",
        "domain": "投行业务",
        "data_level": "L2",
        "description": "投行业务、尽调与项目咨询知识（内部知识引擎）",
        "rest": 755,
        "verag": 711,
    },
    {
        "id": "KB-FINANCE",
        "name": "财务报账知识库",
        "domain": "计划财务",
        "data_level": "L2",
        "description": "财务报账、费用与资金管理问答（内部知识引擎）",
        "rest": 752,
        "verag": 687,
    },
    {
        "id": "KB-PROCUREMENT",
        "name": "立项与采购知识库",
        "domain": "立项采购",
        "data_level": "L2",
        "description": "立项流程、采购与供应商管理问答（内部知识引擎）",
        "rest": 753,
    },
    {
        "id": "KB-DATA-GOVERNANCE",
        "name": "数据治理知识库",
        "domain": "数据治理",
        "data_level": "L1",
        "description": "数据标准、数据资产与治理规范问答（内部知识引擎）",
        "rest": 754,
        "verag": 583,
    },
    {
        "id": "KB-OPS",
        "name": "运营管理知识库",
        "domain": "运营管理",
        "data_level": "L1",
        "description": "运营流程、清算交收与账户业务问答（内部知识引擎）",
        "rest": 760,
        "verag": 699,
    },
    {
        "id": "KB-HR",
        "name": "人力指南知识库",
        "domain": "人力资源",
        "data_level": "L1",
        "description": "人事政策、考勤假期与员工服务问答（内部知识引擎）",
        "rest": 761,
        "verag": 685,
    },
    {
        "id": "KB-AUDIT",
        "name": "智慧纪检知识库",
        "domain": "纪检监督",
        "data_level": "L2",
        "description": "纪检监察制度、监督执纪与合规提醒问答（内部知识引擎）",
        "rest": 765,
    },
    {
        "id": "KB-INTERNAL-POLICY",
        "name": "集团内部制度知识库",
        "domain": "内部制度",
        "data_level": "L2",
        "description": "集团现行基本制度、管理办法与操作规程（内部知识引擎）",
        "rest": 767,
        "verag": 3000290,
    },
    {
        "id": "KB-GROUP-OFFICE",
        "name": "集团办公室知识库",
        "domain": "集团事务",
        "data_level": "L1",
        "description": "集团办公室与党群工作事务问答（内部知识引擎）",
        "verag": 659,
    },
    {
        "id": "KB-AI-INNOVATION",
        "name": "AI 创新发展知识库",
        "domain": "AI创新",
        "data_level": "L1",
        "description": "AI 创新项目、算法与平台建设问答（内部知识引擎）",
        "verag": 3000267,
    },
    {
        "id": "KB-WEALTH",
        "name": "财富管理知识库",
        "domain": "财富管理",
        "data_level": "L2",
        "description": "财富管理产品、客户服务与销售规范问答（内部知识引擎）",
        "verag": 3000281,
    },
    {
        "id": "KB-FINANCIAL-PRODUCT",
        "name": "金融产品知识库",
        "domain": "金融产品",
        "data_level": "L2",
        "description": "金融产品设计、准入与运营规则问答（内部知识引擎）",
        "verag": 601,
    },
    {
        "id": "KB-RESEARCH-INSTITUTE",
        "name": "经济与金融研究院知识库",
        "domain": "研究",
        "data_level": "L2",
        "description": "宏观策略、行业研究与专题报告问答（内部知识引擎）",
        "verag": 653,
    },
    {
        "id": "KB-INSTITUTIONAL",
        "name": "机构业务知识库",
        "domain": "机构业务",
        "data_level": "L2",
        "description": "机构客户服务、绿色金融与业务协同问答（内部知识引擎）",
        "verag": 619,
    },
)

# 独占分配：每个 AI 员工的授权集合两两不相交（同一个知识库不得出现在两个员工的授权里）。
# 允许一人多库；分身不直接持有知识库，遇到知识问题委派给岗位 AI 员工。
GRANT_SPECS: dict[str, list[str]] = {
    "AI-GENERAL": ["KB-IT-SERVICE", "KB-DATA-GOVERNANCE", "KB-INTERNAL-POLICY", "KB-GROUP-OFFICE"],
    "AI-INVESTMENT": ["KB-INVESTMENT-BANKING", "KB-SECURITIES"],
    "AI-RESEARCH": ["KB-RESEARCH-INSTITUTE", "KB-WEALTH"],
    "AI-FUND": ["KB-FINANCIAL-PRODUCT", "KB-INSTITUTIONAL"],
    "AI-XYZQ": ["KB-HR", "KB-FINANCE", "KB-OPS", "KB-AUDIT", "KB-PROCUREMENT", "KB-AI-INNOVATION"],
    "DT-E10281": [],
    "DT-E20999": [],
}


def catalog_entries() -> list[dict]:
    """返回目录的浅拷贝，避免调用方改到模块级常量。"""
    return [dict(entry) for entry in KB_CATALOG]


def catalog_ids() -> list[str]:
    return [entry["id"] for entry in KB_CATALOG]


def engine_map_of(entry: dict) -> dict[str, int]:
    """从目录条目抽取 ``{source: remote_id}`` 映射。"""
    mapping: dict[str, int] = {}
    for source in ("rest", "verag"):
        value = entry.get(source)
        if value is None:
            continue
        try:
            mapping[source] = int(value)
        except (TypeError, ValueError):
            continue
    return mapping


def grant_specs() -> dict[str, list[str]]:
    """独占分配表（浅拷贝）。"""
    return {employee_id: list(kb_ids) for employee_id, kb_ids in GRANT_SPECS.items()}


def assert_exclusive() -> None:
    """自检：授权集合必须两两不相交，且只能引用目录内的库。"""
    known = set(catalog_ids())
    seen: dict[str, str] = {}
    for employee_id, kb_ids in GRANT_SPECS.items():
        for kb_id in kb_ids:
            if kb_id not in known:
                raise ValueError(f"授权引用了目录外的知识库：{employee_id} -> {kb_id}")
            owner = seen.get(kb_id)
            if owner is not None and owner != employee_id:
                raise ValueError(f"知识库被重复授权：{kb_id} 同时属于 {owner} 与 {employee_id}")
            seen[kb_id] = employee_id
