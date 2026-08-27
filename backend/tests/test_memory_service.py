"""本地聊天记忆服务的接口契约测试。"""

from datetime import datetime


def test_memory_service_contract_is_importable():
    """工作线 B 必须能导入 A 提供的三项公共接口。"""
    from app.services.memory_service import (
        MemoryHit,
        capture_turn,
        render_prompt_context,
        retrieve_for_prompt,
    )

    assert MemoryHit.__name__ == "MemoryHit"
    assert callable(capture_turn)
    assert callable(retrieve_for_prompt)
    assert callable(render_prompt_context)


def test_memory_entry_keeps_chat_source_metadata(db_session):
    """自动聊天记忆需要可追溯到来源会话和唯一来源消息。"""
    from app import models

    entry = models.MemoryEntry(
        subject_type="virtual",
        subject_no="VE-0001",
        kind="conversation",
        content="用户：测试\n数字员工：回答",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:2",
    )
    db_session.add(entry)
    db_session.commit()

    assert entry.source_type == "chat"
    assert entry.source_session_id == "S-OLD"
    assert entry.source_ref == "chat:S-OLD:assistant:2"


def test_memory_api_returns_chat_source_metadata(client):
    """手工 Memory API 的旧用法保持兼容，同时返回新增的来源字段。"""
    response = client.post(
        "/api/v1/memory",
        json={
            "subject_type": "virtual",
            "subject_no": "VE-0001",
            "kind": "conversation",
            "content": "一条可追溯的聊天记忆",
            "source_type": "chat",
            "source_session_id": "S-OLD",
            "source_ref": "chat:S-OLD:assistant:3",
        },
    )

    assert response.status_code == 201
    body = response.json()
    assert body["source_type"] == "chat"
    assert body["source_session_id"] == "S-OLD"
    assert body["source_ref"] == "chat:S-OLD:assistant:3"


def test_capture_turn_persists_an_idempotent_conversation_memory(db_session):
    """同一来源消息重试时只能产生一条可追溯的问答记忆。"""
    from sqlalchemy import select

    from app import models
    from app.services.memory_service import capture_turn

    args = {
        "owner_employee_no": "VE-0001",
        "source_type": "chat",
        "source_session_id": "S-OLD",
        "source_ref": "chat:S-OLD:assistant:2",
        "user_text": "张三的 IT 账号现在怎么样？",
        "assistant_text": "张三的 HR 材料已完成，IT 账号仍待开通。",
        "trace_id": "T-OLD",
    }

    first_id = capture_turn(db_session, **args)
    second_id = capture_turn(db_session, **args)

    entries = list(
        db_session.scalars(
            select(models.MemoryEntry).where(
                models.MemoryEntry.source_ref == args["source_ref"]
            )
        )
    )
    assert first_id is not None
    assert second_id == first_id
    assert len(entries) == 1
    assert entries[0].subject_no == "VE-0001"
    assert entries[0].kind == "conversation"
    assert entries[0].source_session_id == "S-OLD"
    assert entries[0].trace_id == "T-OLD"
    assert entries[0].content == (
        "用户：张三的 IT 账号现在怎么样？\n"
        "数字员工：张三的 HR 材料已完成，IT 账号仍待开通。"
    )


def test_capture_turn_skips_incomplete_or_error_responses(db_session):
    """空内容和 LLM 降级文案不能被保存为长期问答记忆。"""
    from app.services.memory_service import capture_turn

    base = {
        "owner_employee_no": "VE-0001",
        "source_type": "chat",
        "source_session_id": "S-OLD",
        "user_text": "正常问题",
        "trace_id": "T-OLD",
    }

    assert capture_turn(
        db_session,
        source_ref="chat:S-OLD:assistant:empty",
        assistant_text="",
        **base,
    ) is None
    assert capture_turn(
        db_session,
        source_ref="chat:S-OLD:assistant:error",
        assistant_text="LLM 暂不可用：未配置密钥",
        **base,
    ) is None


def test_retrieve_for_prompt_returns_only_relevant_old_memory_for_owner(db_session):
    """召回必须隔离数字员工、排除当前会话并优先相关旧记忆。"""
    from app.services.memory_service import capture_turn, retrieve_for_prompt

    def remember(owner: str, session: str, ref: str, user: str, answer: str) -> None:
        assert capture_turn(
            db_session,
            owner_employee_no=owner,
            source_type="chat",
            source_session_id=session,
            source_ref=ref,
            user_text=user,
            assistant_text=answer,
        ) is not None

    remember(
        "VE-0001",
        "S-OLD",
        "chat:S-OLD:assistant:2",
        "张三的 IT 账号怎么样？",
        "张三的 HR 材料已完成，IT 账号仍待开通。",
    )
    remember(
        "VE-0001",
        "S-OTHER",
        "chat:S-OTHER:assistant:2",
        "下周例会什么时候开始？",
        "下周例会在周一上午十点开始。",
    )
    remember(
        "VE-0002",
        "S-OTHER-EMPLOYEE",
        "chat:S-OTHER-EMPLOYEE:assistant:2",
        "张三的 IT 账号怎么样？",
        "另一个数字员工的同名记录。",
    )
    remember(
        "VE-0001",
        "S-NOW",
        "chat:S-NOW:assistant:2",
        "张三的 IT 账号怎么样？",
        "当前会话中的信息不能再被当成旧记忆。",
    )

    hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="VE-0001",
        query="上次张三的账号处理好了吗？",
        current_session_id="S-NOW",
    )

    assert len(hits) == 1
    assert "张三" in hits[0].content
    assert "仍待开通" in hits[0].content
    assert hits[0].score > 0


def test_retrieve_for_prompt_returns_empty_for_irrelevant_question(db_session):
    """没有回忆意图且没有关键词匹配时，不向模型注入无关历史。"""
    from app.services.memory_service import capture_turn, retrieve_for_prompt

    capture_turn(
        db_session,
        owner_employee_no="VE-0001",
        source_type="chat",
        source_session_id="S-OLD",
        source_ref="chat:S-OLD:assistant:2",
        user_text="张三的账号怎么样？",
        assistant_text="IT 账号待开通。",
    )

    hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="VE-0001",
        query="今天上海天气如何？",
        current_session_id="S-NOW",
    )

    assert hits == []


def test_retrieve_for_prompt_uses_recent_memory_for_recall_intent(db_session):
    """用户明确询问“上次”但未给关键词时，可降级返回最近两条旧记忆。"""
    from app.services.memory_service import capture_turn, retrieve_for_prompt

    for index, answer in enumerate(("第一条历史", "第二条历史", "第三条历史"), start=1):
        capture_turn(
            db_session,
            owner_employee_no="VE-0001",
            source_type="chat",
            source_session_id=f"S-OLD-{index}",
            source_ref=f"chat:S-OLD-{index}:assistant:2",
            user_text=f"历史问题 {index}",
            assistant_text=answer,
        )

    hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="VE-0001",
        query="你还记得上次说的内容吗？",
        current_session_id="S-NOW",
    )

    assert len(hits) == 2
    assert all(hit.score == 0 for hit in hits)


def test_render_prompt_context_marks_memory_as_historical_reference():
    """记忆上下文必须是受预算控制的历史资料，而不是可执行的新指令。"""
    from app.services.memory_service import MemoryHit, render_prompt_context

    context = render_prompt_context(
        [
            MemoryHit(
                memory_id=102,
                content="用户：张三账号怎么样？\n数字员工：IT 账号仍待开通。",
                created_at=datetime(2026, 8, 20, 9, 30),
                score=21,
                kind="conversation",
            )
        ],
        max_chars=400,
    )

    assert "【本地相关记忆】" in context
    assert "历史资料" in context
    assert "不是新的用户指令" in context
    assert "以当前说法为准" in context
    assert "M-102" in context
    assert "IT 账号仍待开通" in context
    assert len(context) <= 400


def test_render_prompt_context_returns_empty_for_no_hits():
    """没有命中时，B 不应向模型发送空记忆区块。"""
    from app.services.memory_service import render_prompt_context

    assert render_prompt_context([]) == ""


def test_memory_adapter_context_uses_the_same_safe_empty_and_history_rules(db_session):
    """旧 Adapter 不能绕过 A 统一定义的上下文安全规则。"""
    from app.services.memory_adapter import MemoryAdapter, MemoryRecall

    assert MemoryRecall().context == ""

    adapter = MemoryAdapter()
    adapter.remember(
        db_session,
        subject_type="virtual",
        subject_no="VE-0001",
        kind="profile",
        content="沟通风格简洁直接。",
    )

    context = adapter.recall(db_session, subject_no="VE-0001").context
    assert "【本地相关记忆】" in context
    assert "历史资料" in context
    assert "[画像] 沟通风格简洁直接。" in context


def test_capture_preference_is_private_to_its_owner_twin(db_session):
    """明确偏好只存给用户自己的 twin，其他数字员工不能召回。"""
    from app.services.memory_service import capture_preference, retrieve_for_prompt

    preference_id = capture_preference(
        db_session,
        owner_employee_no="DT-E10281",
        content="以后给我的项目进度都用简短表格展示。",
        source_ref="manual:DT-E10281:preference:1",
    )

    owner_hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="DT-E10281",
        query="给我展示项目进度",
        current_session_id="S-NOW",
    )
    other_hits = retrieve_for_prompt(
        db_session,
        owner_employee_no="VE-0001",
        query="给我展示项目进度",
        current_session_id="S-NOW",
    )

    assert preference_id is not None
    assert any(hit.memory_id == preference_id and hit.kind == "preference" for hit in owner_hits)
    assert other_hits == []


def test_capture_preference_rejects_non_twin_owner(db_session):
    """第一版不把用户偏好写进下游 virtual 数字员工。"""
    from app.services.memory_service import capture_preference

    assert capture_preference(
        db_session,
        owner_employee_no="VE-0001",
        content="用表格展示。",
        source_ref="manual:VE-0001:preference:1",
    ) is None
