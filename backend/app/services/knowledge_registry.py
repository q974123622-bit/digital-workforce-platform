"""Knowledge Resource Registry（Sprint 3）。

知识库资源模型：
knowledge_base_id / name / resource_type / data_level /
allowed_employment_type / department_scope / status
登记数据存放在 knowledge_base 表（mock-data/seed.json），
Policy 评估与 Knowledge Adapter 都以此为资源依据。
"""

from sqlalchemy.orm import Session

from .. import models
from .policy import DECISION_ALLOW, ResourceRef, evaluate


def list_resources(db: Session) -> list[models.KnowledgeBase]:
    return db.query(models.KnowledgeBase).order_by(models.KnowledgeBase.id).all()


def resolve(db: Session, knowledge_base_id: str) -> models.KnowledgeBase | None:
    """按 knowledge_base_id 解析资源；不存在返回 None。"""
    return db.get(models.KnowledgeBase, knowledge_base_id)


def plugin_id_for_level(data_level: str) -> str:
    """知识库访问经统一知识插件入口（L1→knowledge-l1，L2→knowledge-l2，L3→knowledge-l3）。"""
    return {"L1": "knowledge-l1", "L2": "knowledge-l2", "L3": "knowledge-l3"}.get(
        data_level, "knowledge-l2"
    )


def accessible_knowledge_bases(db: Session, subject) -> list[dict]:
    """按 subject 计算可访问知识库清单：逐库经 Policy evaluate（read）判定，decision=allow 才列入。

    输出 [{knowledge_base_id, name, data_level}]；清单由策略决策得出，不得硬编码。
    """
    accessible: list[dict] = []
    for kb in list_resources(db):
        plugin_id = plugin_id_for_level(kb.data_level)
        decision = evaluate(
            db,
            subject,
            ResourceRef(type="knowledge", id=plugin_id, data_level=kb.data_level),
            "read",
        )
        if decision.decision == DECISION_ALLOW:
            accessible.append(
                {
                    "knowledge_base_id": kb.id,
                    "name": kb.name,
                    "data_level": kb.data_level,
                }
            )
    return accessible
