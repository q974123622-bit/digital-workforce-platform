"""enterprise-knowledge 需要的知识查询边界场景测试（不涉及 ChatOrchestrator）。

enterprise-knowledge 依赖的五类 Tool 结果覆盖情况：
- Allow + Result：已有测试覆盖（test_enterprise_resources.py / test_control_plane.py）
- Deny：已有测试覆盖（test_enterprise_resources.py / test_control_plane.py）
- Approval：已有 Gateway 测试覆盖（test_control_plane.py::test_gateway_approval）
- Empty：本文件新增
- Tool Error：本文件新增

本文件只通过 pytest monkeypatch 控制 Knowledge Adapter 的返回，不修改生产代码。
"""

import pytest

from app.services import gateway
from app.services.knowledge_adapter import MockKnowledgeAdapter


def _empty_search(self, *, employee_id, knowledge_base_id, query, trace_id):
    """模拟 Knowledge Adapter 返回一个真正的空结果（真实 hits=[]）。"""
    return {
        "source": "demo",
        "knowledge_base_id": knowledge_base_id,
        "query": query,
        "hits": [],
    }


def _error_search(self, *, employee_id, knowledge_base_id, query, trace_id):
    """模拟 Knowledge Adapter 抛出一个工具级异常。"""
    raise RuntimeError("demo knowledge tool error")


def test_knowledge_search_allow_with_empty_hits(client, monkeypatch):
    """Allow + 真正空结果：Policy 放行、Gateway 正常调用 Adapter、返回 hits=[]。"""
    monkeypatch.setattr(MockKnowledgeAdapter, "search", _empty_search)

    resp = client.post(
        "/internal/knowledge/search",
        json={
            "employee_id": "DT-E10281",
            "knowledge_base_id": "KB-INTERNAL",
            "query": "一个合法但无匹配内容的问题",
            "trace_id": "T-ENT-EMPTY-001",
        },
    )

    # 请求通过 Policy，HTTP 仍是成功响应，且被判定为 allow
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["decision"] == "allow"

    # Adapter 调用成功，且 hits 是真正的空列表，而不是兜底文案
    assert body["data"]["source"] == "demo"
    assert body["data"]["knowledge_base_id"] == "KB-INTERNAL"
    assert body["data"]["hits"] == []

    # allow 审计正常产生，result_summary 能体现空结果
    audit_id = body["audit_ids"][0]
    audit = client.get(f"/api/v1/audit/{audit_id}").json()
    assert audit["decision"] == "allow"
    assert audit["knowledge_base_id"] == "KB-INTERNAL"
    assert audit["employee_id"] == "DT-E10281"
    assert audit["trace_id"] == "T-ENT-EMPTY-001"
    assert "hits" in (audit["result_summary"] or "")
    assert "[]" in (audit["result_summary"] or "")


def test_knowledge_search_adapter_error_is_not_empty(db_session, monkeypatch):
    """Tool Error：Adapter 异常真实向上传播，不会被转换成空结果或成功结果。

    说明：当前实现中 Adapter.search 的异常发生在 allow audit 写入之前，因此本次
    调用不会产生“执行结果”审计事件。这是当前行为，不在本测试中固化为长期契约。
    """
    monkeypatch.setattr(MockKnowledgeAdapter, "search", _error_search)

    # 错误必须真实向上传播，且不能被包装成正常返回
    with pytest.raises(RuntimeError, match="demo knowledge tool error"):
        gateway.search_knowledge(
            db_session,
            employee_id="DT-E10281",
            knowledge_base_id="KB-INTERNAL",
            query="任意查询",
            trace_id="T-ENT-ERR-001",
        )
