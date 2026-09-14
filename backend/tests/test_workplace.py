"""Sprint 7 个人工作中心（职场）：技能 CRUD / 技能注入 / 聚合 / 会话 / 群聊基础编排。"""

from fastapi import HTTPException

from app import models
from app.services.chat import ChatOrchestrator
from app.services.group_chat import _find_recent_task, send_group_message
from app.services.llm import LLMProvider, LLMResponse


class FakeLLM(LLMProvider):
    def __init__(self, script):
        self.script = list(script)
        self.calls = []

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


# ---- 技能 CRUD 与注入 ----


def test_skill_crud(client, db_session):
    created = client.post(
        "/api/v1/skills",
        json={"actor_no": "E10281", "name": "会议纪要模板", "description": "快速整理会议纪要", "content": "结论先行，行动项带负责人。"},
    )
    assert created.status_code == 201
    skill_id = created.json()["id"]
    assert created.json()["status"] == "active"

    listed = client.get("/api/v1/skills?actor_no=E10281").json()
    assert any(s["id"] == skill_id for s in listed)

    updated = client.put(
        f"/api/v1/skills/{skill_id}?actor_no=E10281",
        json={"status": "disabled"},
    )
    assert updated.status_code == 200
    assert updated.json()["status"] == "disabled"

    deleted = client.delete(f"/api/v1/skills/{skill_id}?actor_no=E10281")
    assert deleted.status_code == 204
    assert not any(s["id"] == skill_id for s in client.get("/api/v1/skills?actor_no=E10281").json())


def test_skill_unknown_actor(client):
    resp = client.post("/api/v1/skills", json={"actor_no": "E99999", "name": "x", "content": "y"})
    assert resp.status_code == 404


def test_skill_injected_into_twin_persona(db_session):
    db_session.add(models.Skill(
        id="SK-TEST-1", owner_human_no="E10281", name="报销制度速答",
        description="掌握差旅报销标准", content="1. 差旅住宿标准：一线城市每晚不超过 600 元。",
        status="active",
    ))
    db_session.commit()
    fake = FakeLLM([LLMResponse(content="好的，我按技能回答。")])
    result = ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="DT-E10281",
        message="报销有什么需要注意的？",
        session_id=None,
        persist=False,
        history_override=[],
    )
    assert result.message == "好的，我按技能回答。"
    system_prompt = fake.calls[0][0]["content"]
    assert "报销制度速答" in system_prompt
    assert "【用户维护的参考技能】" in system_prompt


# ---- 职场聚合 ----


def test_workplace_aggregate(client):
    home = client.get("/api/v1/workplace?actor_no=E10281")
    assert home.status_code == 200
    body = home.json()
    assert body["actor"]["employee_no"] == "E10281"
    assert body["twin"]["employee_no"] == "DT-E10281"
    assert {e["employee_no"] for e in body["available_employees"]} == {
        "AI-GENERAL", "AI-INVESTMENT", "AI-RESEARCH", "AI-FUND", "AI-XYZQ"
    }
    assert isinstance(body["skills"], list)
    assert isinstance(body["recent_conversations"], list)


def test_workplace_aggregate_unknown_actor(client):
    assert client.get("/api/v1/workplace?actor_no=E99999").status_code == 404


# ---- 会话创建与幂等 ----


def test_direct_conversation_idempotent(client):
    first = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"]},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["AI-GENERAL"]},
    )
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_group_conversation_auto_organizer(client):
    resp = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "title": "测试协作", "participant_employee_nos": ["AI-GENERAL", "AI-INVESTMENT"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    participants = body["participants"]
    assert participants[0]["employee_no"] == "DT-E10281"
    assert participants[0]["role"] == "organizer"
    assert {p["employee_no"] for p in participants} == {"DT-E10281", "AI-GENERAL", "AI-INVESTMENT"}


def test_group_conversation_rejects_duplicate_and_unknown(client):
    dup = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "participant_employee_nos": ["AI-GENERAL", "AI-GENERAL"]},
    )
    assert dup.status_code == 400
    unknown = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "participant_employee_nos": ["DT-E20999"]},
    )
    assert unknown.status_code == 400


def test_direct_with_other_twin_rejected(client):
    resp = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["DT-E20999"]},
    )
    assert resp.status_code == 400


def test_add_participant_to_group(client):
    conv = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "participant_employee_nos": ["AI-GENERAL"]},
    ).json()
    resp = client.post(f"/api/v1/conversations/{conv['id']}/participants", json={"employee_no": "AI-INVESTMENT"})
    assert resp.status_code == 200
    assert "AI-INVESTMENT" in {p["employee_no"] for p in resp.json()["participants"]}
    again = client.post(f"/api/v1/conversations/{conv['id']}/participants", json={"employee_no": "AI-INVESTMENT"})
    assert again.status_code == 400


# ---- 私聊编排 ----

def test_direct_chat_has_no_name_prefix_or_group_context(db_session):
    conv = models.Conversation(
        id="CONV-TEST-DIRECT",
        kind="direct",
        title="",
        owner_human_no="E10281",
        participants=[{"employee_no": "DT-E10281", "role": "member"}],
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)

    fake = FakeLLM([LLMResponse(content="你好呀，有什么可以帮你？")])
    send_group_message(db_session, conversation=conv, actor_no="E10281", content="你好", provider=fake)

    system_msgs = [m["content"] for m in fake.calls[0] if m["role"] == "system"]
    assert not any("协作空间" in m for m in system_msgs)
    assert not any("【张三】" in m["content"] for m in fake.calls[0])


def test_find_recent_task_only_dedup_running_or_approval(db_session):
    running = models.TaskRun(
        id="T-RUN-1",
        team_id="CONV-X",
        conversation_id="CONV-X",
        request="帮我准备入职材料",
        status="running",
    )
    done = models.TaskRun(
        id="T-DONE-1",
        team_id="CONV-X",
        conversation_id="CONV-X",
        request="帮我准备入职材料",
        status="completed",
    )
    db_session.add_all([running, done])
    db_session.commit()
    # 去重只拦 running/approval，completed 允许重发
    assert _find_recent_task(db_session, "CONV-X", "帮我准备入职材料").id == "T-RUN-1"
    db_session.delete(running)
    db_session.commit()
    assert _find_recent_task(db_session, "CONV-X", "帮我准备入职材料") is None


def test_send_message_forbidden_for_other_actor(client, db_session):
    conv = models.Conversation(
        id="CONV-TEST-1", kind="direct", title="", owner_human_no="E10281",
        participants=[{"employee_no": "DT-E10281", "role": "member"}],
    )
    db_session.add(conv)
    db_session.commit()
    resp = client.post(
        f"/api/v1/conversations/{conv.id}/messages",
        json={"actor_no": "E20999", "content": "越权尝试"},
    )
    assert resp.status_code == 403
