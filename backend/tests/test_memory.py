"""个人记忆插件测试（test_memory）。"""


def test_memory_seed(client):
    """种子数据里应包含示例个人记忆。"""
    # E10021（王老师）有 1 条示例记忆
    memories = client.get("/api/v1/memory/E10021").json()
    assert len(memories) == 1
    assert memories[0]["human_no"] == "E10021"


def test_memory_write_and_read(client):
    """写入一条记忆后，能按真人查询出来，且最新在前。"""
    # 写入
    resp = client.post(
        "/api/v1/memory",
        json={"human_no": "E10021", "employee_no": "VE-0001", "content": "测试写入一条记忆"},
    )
    assert resp.status_code == 201
    assert resp.json()["human_no"] == "E10021"

    # 读取：种子 1 条 + 刚写的 1 条 = 2 条
    memories = client.get("/api/v1/memory/E10021").json()
    assert len(memories) == 2
    # 最新写入的排在最前面（倒序）
    assert memories[0]["content"] == "测试写入一条记忆"


def test_memory_isolated_by_human(client):
    """不同真人的记忆互不串读（个人记忆隔离）。"""
    # E10281（张三）有 1 条示例记忆
    assert len(client.get("/api/v1/memory/E10281").json()) == 1
    # E20888（实习生）没有任何记忆 → 返回空列表
    assert client.get("/api/v1/memory/E20888").json() == []
