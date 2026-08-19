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
