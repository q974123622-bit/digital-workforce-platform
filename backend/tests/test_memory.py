"""记忆插件测试（test_memory）。"""


def test_memory_seed(client):
    """种子数据里应包含示例记忆，字段为 7 维度模型。"""
    memories = client.get("/api/v1/memory", params={"subject_no": "E10021"}).json()
    assert len(memories) == 1
    assert memories[0]["subject_type"] == "human"
    assert memories[0]["subject_no"] == "E10021"
    assert memories[0]["kind"] == "fact"
    assert memories[0]["visibility"] == "personal"


def test_memory_write_and_read(client):
    """写入一条记忆后，能按主体查询出来，且最新在前。"""
    resp = client.post(
        "/api/v1/memory",
        json={
            "subject_type": "human",
            "subject_no": "E10021",
            "kind": "conversation",
            "content": "测试写入一条对话记忆",
            "related_subject_no": "VE-0001",
            "visibility": "personal",
            "data_level": "L2",
            "lifecycle": "active",
        },
    )
    assert resp.status_code == 201
    assert resp.json()["subject_no"] == "E10021"
    assert resp.json()["kind"] == "conversation"
    assert resp.json()["visibility"] == "personal"

    # 读取：种子 1 条 + 刚写的 1 条 = 2 条，最新在前
    memories = client.get("/api/v1/memory", params={"subject_no": "E10021"}).json()
    assert len(memories) == 2
    assert memories[0]["content"] == "测试写入一条对话记忆"


def test_memory_isolated_by_subject(client):
    """不同主体的记忆互不串读。"""
    assert len(client.get("/api/v1/memory", params={"subject_no": "E10281"}).json()) == 1
    # E20888（实习生）没有任何记忆 → 空列表
    assert client.get("/api/v1/memory", params={"subject_no": "E20888"}).json() == []


def test_memory_filter_by_kind(client):
    """按类型过滤：写入一条 decision，按 kind=decision 能查到。"""
    client.post(
        "/api/v1/memory",
        json={
            "subject_type": "virtual",
            "subject_no": "VE-0001",
            "kind": "decision",
            "content": "调用了员工查询，返回成功",
            "visibility": "confidential",
            "data_level": "L3",
        },
    )
    decisions = client.get("/api/v1/memory", params={"kind": "decision"}).json()
    assert len(decisions) == 1
    assert decisions[0]["subject_type"] == "virtual"
    assert decisions[0]["visibility"] == "confidential"


def test_memory_filter_by_related(client):
    """按关联对方过滤：查某用户与某虚拟员工的记忆。"""
    memories = client.get(
        "/api/v1/memory",
        params={"subject_no": "E10021", "related_subject_no": "VE-0001"},
    ).json()
    assert len(memories) == 1
    assert memories[0]["related_subject_no"] == "VE-0001"


# ---- Step 3：权限鉴权 ----


def test_memory_public_visible_to_anyone(client):
    """public 记忆：任何人（含实习生）都能读。"""
    client.post(
        "/api/v1/memory",
        json={"subject_type": "human", "subject_no": "E10021", "kind": "basic_info", "content": "王老师，HR 部门", "visibility": "public"},
    )
    memories = client.get(
        "/api/v1/memory", params={"subject_no": "E10021"}, headers={"X-Demo-Actor": "E20888"}
    ).json()
    assert any(m["visibility"] == "public" for m in memories)


def test_memory_personal_hidden_from_others(client):
    """personal 记忆：非本人、非 owner 读不到。"""
    # 种子数据里 E10021 有 1 条 personal 记忆；E10281 不是其 owner
    memories = client.get(
        "/api/v1/memory", params={"subject_no": "E10021"}, headers={"X-Demo-Actor": "E10281"}
    ).json()
    assert all(m["visibility"] != "personal" for m in memories)


def test_memory_confidential_only_admin(client):
    """confidential 记忆：仅管理员能读。"""
    client.post(
        "/api/v1/memory",
        json={"subject_type": "virtual", "subject_no": "VE-0001", "kind": "decision", "content": "涉密决策", "visibility": "confidential", "data_level": "L3"},
    )
    # 非管理员读不到 confidential
    non_admin = client.get(
        "/api/v1/memory", params={"kind": "decision"}, headers={"X-Demo-Actor": "E10281"}
    ).json()
    assert all(m["visibility"] != "confidential" for m in non_admin)
    # 管理员（E10021）能读到
    admin = client.get(
        "/api/v1/memory", params={"kind": "decision"}, headers={"X-Demo-Actor": "E10021"}
    ).json()
    assert any(m["visibility"] == "confidential" for m in admin)


# ---- Step 4：互通检索 ----


def test_memory_interoperability(client):
    """互通：同一用户的记忆，跨不同 AI 都能查到。"""
    # 王老师 E10021 和 VE-0001 的对话
    client.post("/api/v1/memory", json={
        "subject_type": "human", "subject_no": "E10021", "kind": "conversation",
        "content": "入职第一天要做什么", "related_subject_no": "VE-0001",
    })
    # 王老师 E10021 和 DT-E10281（分身）的对话
    client.post("/api/v1/memory", json={
        "subject_type": "human", "subject_no": "E10021", "kind": "conversation",
        "content": "帮我查一下内部制度", "related_subject_no": "DT-E10281",
    })
    # 查询 E10021 的全部记忆 → 跨 AI 都能查到（互通）
    memories = client.get(
        "/api/v1/memory", params={"subject_no": "E10021"}, headers={"X-Demo-Actor": "E10021"}
    ).json()
    related = {m["related_subject_no"] for m in memories}
    assert "VE-0001" in related
    assert "DT-E10281" in related


# ---- Step 6：对话压缩 ----


def test_summarize_expired_sessions(client, db_session):
    """压缩过期会话：超过 30 天的会话被提炼成摘要，并标记已压缩。"""
    from datetime import datetime, timedelta

    from app import models
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMResponse

    class FakeLLM:
        def chat(self, messages, tools=None):
            return LLMResponse(content="回答")

    from sqlalchemy import select

    orchestrator = ChatOrchestrator(FakeLLM())
    first = orchestrator.handle_message(db_session, employee_no="VE-0001", message="入职流程咨询", session_id=None)

    # 把会话时间改成 31 天前（模拟过期）
    session = db_session.scalar(select(models.ChatSession).where(models.ChatSession.session_id == first.session_id))
    session.created_at = datetime.now() - timedelta(days=31)
    db_session.commit()

    # 触发压缩
    resp = client.post("/api/v1/memory/summarize").json()
    assert resp["summarized"] == 1

    # 生成了 summary 记忆
    summaries = client.get("/api/v1/memory", params={"kind": "summary"}).json()
    assert len(summaries) == 1
    assert summaries[0]["subject_no"] == "VE-0001"
    assert summaries[0]["kind"] == "summary"

    # 会话已标记压缩
    session = db_session.scalar(select(models.ChatSession).where(models.ChatSession.session_id == first.session_id))
    assert session.summarized is True


# ---- Step 7：附件记忆 ----


def test_upload_attachment(client):
    """上传文本附件：存文件 + 存摘要记忆（kind=attachment）。"""
    resp = client.post(
        "/api/v1/memory/attachments",
        data={"subject_no": "E10021"},
        files={"file": ("note.txt", "这是一段测试文本，用于验证附件记忆的摘要提取功能。", "text/plain")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["kind"] == "attachment"
    assert body["subject_no"] == "E10021"
    assert body["file_ref"]  # 文件路径非空

    # 能查到附件记忆
    attachments = client.get("/api/v1/memory", params={"kind": "attachment"}).json()
    assert len(attachments) == 1
    assert attachments[0]["kind"] == "attachment"


# ---- Step 8：用户画像 ----


def test_generate_profile(client):
    """生成用户画像：从记忆提炼画像，存 kind=profile。"""
    # 先写几条记忆
    client.post("/api/v1/memory", json={"subject_type": "human", "subject_no": "E10021", "kind": "fact", "content": "偏好周五下午开会"})
    client.post("/api/v1/memory", json={"subject_type": "human", "subject_no": "E10021", "kind": "fact", "content": "沟通风格简洁直接"})

    # 生成画像
    resp = client.post("/api/v1/memory/profile/E10021")
    assert resp.status_code == 200
    body = resp.json()
    assert body["kind"] == "profile"
    assert body["subject_no"] == "E10021"
    assert body["content"]  # 画像内容非空

    # 能查到画像
    profiles = client.get("/api/v1/memory", params={"kind": "profile"}).json()
    assert len(profiles) == 1
    assert profiles[0]["subject_no"] == "E10021"


# ---- Step 9：MemoryAdapter（统一记忆访问接口） ----


def test_memory_adapter_recall_and_remember(db_session):
    """MemoryAdapter：写入记忆 + 召回记忆（含上下文格式化）。"""
    from app.services.memory_adapter import MemoryAdapter

    adapter = MemoryAdapter()
    adapter.remember(db_session, subject_type="human", subject_no="E10021", kind="fact", content="偏好周五下午开会")
    adapter.remember(db_session, subject_type="human", subject_no="E10021", kind="profile", content="沟通风格简洁直接")

    recall = adapter.recall(db_session, subject_no="E10021")
    assert len(recall.entries) >= 2
    assert "偏好周五下午开会" in recall.context
    assert "画像" in recall.context


def test_memory_adapter_recall_respects_permission(db_session):
    """MemoryAdapter.recall：按权限过滤（读者看不到 confidential）。"""
    from app.services.memory_adapter import MemoryAdapter

    adapter = MemoryAdapter()
    adapter.remember(db_session, subject_type="human", subject_no="E10021", kind="fact", content="公开事实", visibility="public")
    adapter.remember(db_session, subject_type="human", subject_no="E10021", kind="decision", content="涉密决策", visibility="confidential")

    # 非管理员读者：只能看到 public，看不到 confidential
    recall = adapter.recall(db_session, subject_no="E10021", reader_no="E10281")
    assert all(e.visibility != "confidential" for e in recall.entries)
    assert any(e.visibility == "public" for e in recall.entries)
