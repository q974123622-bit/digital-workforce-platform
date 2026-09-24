"""Knowledge Resource Registry（Sprint 3）。

知识库资源模型：
knowledge_base_id / name / resource_type / data_level /
allowed_employment_type / department_scope / status
登记数据存放在 knowledge_base 表（mock-data/seed.json），
Policy 评估与 Knowledge Adapter 都以此为资源依据。
"""

from sqlalchemy.orm import Session

from .. import models


def list_resources(db: Session) -> list[models.KnowledgeBase]:
    return db.query(models.KnowledgeBase).order_by(models.KnowledgeBase.id).all()


def resolve(db: Session, knowledge_base_id: str) -> models.KnowledgeBase | None:
    """按 knowledge_base_id 解析资源；不存在返回 None。"""
    return db.get(models.KnowledgeBase, knowledge_base_id)


def plugin_id_for_level(data_level: str) -> str:
    """知识库分级标识：直接返回数据等级（L1/L2/L3）。

    历史版本曾映射到 mock 插件 knowledge-l1/l2/l3；现已删除这些 mock 插件，
    知识访问改由 Policy 按 data_level 直接分级判断，不再依赖分级插件入口。
    """
    return data_level


def accessible_knowledge_bases(db: Session, subject, requester_human_no: str | None = None) -> list[dict]:
    """返回当前主体实际可读的知识库，不泄露不可访问资源的名称。

    这里是「不可达即摘除」的统一落点：``routers/internal.py`` 的 ``tools/list``
    枚举、提示词里的知识库描述、``chat.py`` 兜底链路都读这个函数，因此过滤一次即可
    全链路生效；函数本身已保证不返回不可访问资源的名称。
    """
    from .knowledge_source_health import is_kb_available
    from .policy import DECISION_ALLOW, ResourceRef, evaluate

    explicit_grants = {
        row.knowledge_base_id
        for row in db.query(models.AgentKnowledgeGrant).filter(
            models.AgentKnowledgeGrant.employee_id == subject.employee_id,
            models.AgentKnowledgeGrant.action == "read",
            models.AgentKnowledgeGrant.status == "active",
        )
    }
    result: list[dict] = []
    requester = db.get(models.HumanEmployee, requester_human_no) if requester_human_no else None
    for kb in list_resources(db):
        if kb.status != "active":
            continue
        if kb.id not in explicit_grants:
            continue
        if kb.allowed_employment_type and subject.employment_type not in kb.allowed_employment_type:
            continue
        if kb.department_scope and "*" not in kb.department_scope and subject.department not in kb.department_scope:
            continue
        if requester is not None:
            if kb.allowed_employment_type and requester.employment_type not in kb.allowed_employment_type:
                continue
            if kb.department_scope and "*" not in kb.department_scope and requester.department not in kb.department_scope:
                continue
        # 数据源不可达的知识库不进工具清单：调用必然失败，留给模型只会反复试错。
        if not is_kb_available(db, kb):
            continue
        plugin_id = plugin_id_for_level(kb.data_level)
        decision = evaluate(db, subject, ResourceRef(type="knowledge", id=plugin_id, data_level=kb.data_level), "read")
        if decision.decision == DECISION_ALLOW:
            result.append({
                "id": kb.id,
                "name": kb.name,
                "data_level": kb.data_level,
                "domain": kb.domain,
                "active_source": active_source_for_kb(db, kb),
            })
    return result


def active_source_for_kb(db: Session, kb) -> str | None:
    """该知识库当前生效的通道（rest / verag）；无映射或都不可达时返回 None。"""
    from .knowledge_source_health import active_source_for_kb as _active

    return _active(db, kb)
