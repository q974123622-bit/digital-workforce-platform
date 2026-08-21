"""Sprint 7 个人工作中心（职场）：技能 CRUD / 技能注入 / 聚合 / 会话 / 群聊编排。"""

import pytest
from fastapi import HTTPException

from app import models
from app.routers.workplace import _conversation_out
from app.services.chat import ChatOrchestrator
from app.services.group_chat import process_conversation, send_conversation_message, send_group_message
from app.services.llm import LLMProvider, LLMResponse, LLMUnavailableError
from app.services.team_orchestrator import TeamTaskOrchestrator


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


class FlakyProvider(LLMProvider):
    """第 1 次调用抛 LLM_UNAVAILABLE，之后正常（模拟单个成员降级）。"""

    def __init__(self, responses):
        self.calls = 0
        self.responses = list(responses)

    def chat(self, messages, tools=None):
        self.calls += 1
        if self.calls == 1:
            raise LLMUnavailableError("mock key missing")
        return self.responses.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return {}


class AlwaysFailProvider(LLMProvider):
    def chat(self, messages, tools=None):
        raise LLMUnavailableError("mock key missing")

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


def test_skill_not_injected_into_virtual_employee(db_session):
    fake = FakeLLM([LLMResponse(content="我是 HR 助理。")])
    ChatOrchestrator(fake).handle_message(
        db_session,
        employee_no="VE-0001",
        message="你好",
        session_id=None,
        persist=False,
        history_override=[],
    )
    system_prompt = fake.calls[0][0]["content"]
    assert "【你掌握的技能】" not in system_prompt


# ---- 职场聚合 ----


def test_workplace_aggregate(client):
    home = client.get("/api/v1/workplace?actor_no=E10281")
    assert home.status_code == 200
    body = home.json()
    assert body["actor"]["employee_no"] == "E10281"
    assert body["twin"]["employee_no"] == "DT-E10281"
    assert all(e["type"] in ("virtual", "rpa") for e in body["available_employees"])
    assert not any(e["type"] == "twin" for e in body["available_employees"])
    assert any(s["name"] == "报销制度速答" for s in body["skills"])
    assert len(body["recent_conversations"]) >= 2


def test_workplace_aggregate_unknown_actor(client):
    assert client.get("/api/v1/workplace?actor_no=E99999").status_code == 404


# ---- 会话创建与幂等 ----


def test_direct_conversation_idempotent(client):
    first = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["VE-0001"]},
    )
    assert first.status_code == 201
    second = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "direct", "participant_employee_nos": ["VE-0001"]},
    )
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]


def test_group_conversation_auto_organizer(client):
    resp = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "title": "测试协作", "participant_employee_nos": ["VE-0001", "VE-0003"]},
    )
    assert resp.status_code == 201
    body = resp.json()
    participants = body["participants"]
    assert participants[0]["employee_no"] == "DT-E10281"
    assert participants[0]["role"] == "organizer"
    assert {p["employee_no"] for p in participants} == {"DT-E10281", "VE-0001", "VE-0003"}


def test_group_conversation_rejects_duplicate_and_unknown(client):
    dup = client.post(
        "/api/v1/conversations",
        json={"actor_no": "E10281", "kind": "group", "participant_employee_nos": ["VE-0001", "VE-0001"]},
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
        json={"actor_no": "E10281", "kind": "group", "participant_employee_nos": ["VE-0001"]},
    ).json()
    resp = client.post(f"/api/v1/conversations/{conv['id']}/participants", json={"employee_no": "VE-0003"})
    assert resp.status_code == 200
    assert "VE-0003" in {p["employee_no"] for p in resp.json()["participants"]}
    again = client.post(f"/api/v1/conversations/{conv['id']}/participants", json={"employee_no": "VE-0003"})
    assert again.status_code == 400


# ---- 群聊/私聊编排 ----


def _seed_group(db_session):
    import app.models as models

    conv = models.Conversation(
        id="CONV-TEST-1",
        kind="group",
        title="测试协作",
        owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "VE-0001", "role": "member"},
        ],
    )
    db_session.add(conv)
    db_session.commit()
    db_session.refresh(conv)
    return conv


def test_send_group_message_sequential_order(db_session):
    conv = _seed_group(db_session)
    fake = FakeLLM([LLMResponse(content="我先整理入职材料。"), LLMResponse(content="IT 账号当天开通。")])
    send_group_message(db_session, conversation=conv, actor_no="E10281", content="帮我准备入职", provider=fake)

    msgs = conv_messages(db_session, conv.id)
    assert [m.role for m in msgs] == ["user", "assistant", "assistant"]
    assert msgs[0].participant_no == "E10281"
    assert msgs[1].participant_no == "DT-E10281"
    assert msgs[1].content == "我先整理入职材料。"
    assert msgs[2].participant_no == "VE-0001"
    assert msgs[2].content == "IT 账号当天开通。"
    # 群聊上下文一律不带「【名字】」前缀，避免模型模仿
    assert not any("【张三】" in m["content"] for m in fake.calls[0])


def test_direct_chat_has_no_name_prefix_or_group_context(db_session):
    import app.models as models

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


def test_send_group_message_single_member_degrade(db_session):
    conv = _seed_group(db_session)
    flaky = FlakyProvider([LLMResponse(content="IT 部分我来。")])
    send_group_message(db_session, conversation=conv, actor_no="E10281", content="帮我准备入职", provider=flaky)

    msgs = conv_messages(db_session, conv.id)
    assert len(msgs) == 3
    assert "暂时无法响应" in msgs[1].content
    assert msgs[2].content == "IT 部分我来。"


def test_send_group_message_all_fail_raises(db_session):
    conv = _seed_group(db_session)
    with pytest.raises(HTTPException) as exc_info:
        send_group_message(
            db_session,
            conversation=conv,
            actor_no="E10281",
            content="帮我准备入职",
            provider=AlwaysFailProvider(),
        )
    assert exc_info.value.status_code == 503


def test_send_message_endpoint(client, db_session, monkeypatch):
    conv = _seed_group(db_session)
    resp = client.post(
        f"/api/v1/conversations/{conv.id}/messages",
        json={"actor_no": "E10281", "content": "大家看看这个"},
    )
    assert resp.status_code == 200
    body = resp.json()
    # 异步语义：POST 只落用户消息并立即返回，后台编排尚未执行
    assert [m["role"] for m in body["messages"]] == ["user"]
    assert body["tasks"] == []


class TaskFakeLLM(LLMProvider):
    """分类器 + 拆解 + 汇总共用：structured_output 与 chat 分别按脚本返回。"""

    def __init__(self, structured=None, chat=None):
        self.structured_script = list(structured or [])
        self.chat_script = list(chat or [])

    def chat(self, messages, tools=None):
        if not self.chat_script:
            raise LLMUnavailableError("empty chat script")
        return self.chat_script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        return self.structured_script.pop(0) if self.structured_script else {}


def test_group_message_task_dispatch(client, db_session, monkeypatch):
    fake = TaskFakeLLM(
        structured=[
            {"action": "task"},
            {"subtasks": [{"worker_id": "VE-0001", "summary": "确认入职材料", "plugin_ids": ["adp-onboarding"]}]},
        ],
        chat=[LLMResponse(content="Leader 汇总：入职材料已确认。")],
    )

    import app.services.group_chat as group_chat

    monkeypatch.setattr(group_chat, "DeepSeekProvider", lambda: fake)
    conv = db_session.get(models.Conversation, "CONV-0002")
    send_conversation_message(db_session, conversation=conv, actor_no="E10281", content="帮我准备入职材料")
    body = _conversation_out(db_session, conv).model_dump()
    seed_task_ids = {"T-20260819-DEMO1", "T-20260819-DEMO2", "T-20260819-DEMO3"}
    new_tasks = [t for t in body["tasks"] if t["id"] not in seed_task_ids]
    assert len(new_tasks) == 1
    task = new_tasks[0]
    assert task["conversation_id"] == "CONV-0002"
    assert task["status"] == "completed"
    assert {sub["worker_no"] for sub in task["subtasks"]} == {"VE-0002", "VE-0003"}
    assert all(sub["status"] == "completed" for sub in task["subtasks"])
    assert task["summary"] == "Leader 汇总：入职材料已确认。"
    # 不再追加受理气泡：最后一条为触发任务的消息，任务记录其 seq 用于卡片内联
    msgs = body["messages"]
    assert msgs[-1]["participant_no"] == "E10281"
    assert task["trigger_message_seq"] == msgs[-1]["seq"]
    assert any("流程：入职流程 Workflow" in (sub["result"] or "") for sub in task["subtasks"])
    assert all("{" not in (sub["result"] or "") for sub in task["subtasks"])


def test_group_message_task_dispatch_plugin_type_alias(client, db_session, monkeypatch):
    """模型返回 plugin_ids 为类型名（workflow）时，归一化为成员可执行的真实插件。"""
    fake = TaskFakeLLM(
        structured=[
            {"action": "task"},
            {
                "subtasks": [
                    {"worker_id": "VE-0001", "summary": "整理入职材料并跟进账号开通", "plugin_ids": ["workflow"]}
                ]
            },
        ],
        chat=[LLMResponse(content="Leader 汇总：入职准备完成。")],
    )

    import app.services.group_chat as group_chat

    monkeypatch.setattr(group_chat, "DeepSeekProvider", lambda: fake)
    conv = db_session.get(models.Conversation, "CONV-0002")
    send_conversation_message(db_session, conversation=conv, actor_no="E10281", content="帮我整理新员工入职准备清单")
    body = _conversation_out(db_session, conv).model_dump()
    seed_task_ids = {"T-20260819-DEMO1", "T-20260819-DEMO2", "T-20260819-DEMO3"}
    task = next(t for t in body["tasks"] if t["id"] not in seed_task_ids)
    assert {sub["plugin_ids"][0] for sub in task["subtasks"]} == {
        "hr-employee-mcp", "adp-onboarding"
    }
    assert {sub["worker_no"] for sub in task["subtasks"]} == {"VE-0002", "VE-0003"}


def test_find_recent_task_only_dedup_running_or_approval(db_session):
    from app.services.group_chat import _find_recent_task

    running = models.TaskRun(
        id="T-RUN-1",
        team_id="CONV-0002",
        conversation_id="CONV-0002",
        request="帮我准备入职材料",
        status="running",
    )
    done = models.TaskRun(
        id="T-DONE-1",
        team_id="CONV-0002",
        conversation_id="CONV-0002",
        request="帮我准备入职材料",
        status="completed",
    )
    db_session.add_all([running, done])
    db_session.commit()
    # 去重只拦 running/approval，completed 允许重发
    assert _find_recent_task(db_session, "CONV-0002", "帮我准备入职材料").id == "T-RUN-1"
    db_session.delete(running)
    db_session.commit()
    assert _find_recent_task(db_session, "CONV-0002", "帮我准备入职材料") is None


def test_group_message_chat_single_reply(client, db_session, monkeypatch):
    fake = TaskFakeLLM(structured=[{"action": "chat"}], chat=[LLMResponse(content="好啊")])

    import app.services.group_chat as group_chat

    monkeypatch.setattr(group_chat, "DeepSeekProvider", lambda: fake)
    conv = db_session.get(models.Conversation, "CONV-0002")
    send_conversation_message(db_session, conversation=conv, actor_no="E10281", content="大家早上好")
    body = _conversation_out(db_session, conv).model_dump()
    seed_task_ids = {"T-20260819-DEMO1", "T-20260819-DEMO2", "T-20260819-DEMO3"}
    assert all(t["id"] in seed_task_ids for t in body["tasks"])
    new_msgs = body["messages"][-2:]
    assert [m["role"] for m in new_msgs] == ["user", "assistant"]
    assert new_msgs[1]["participant_no"] == "DT-E10281"


def test_group_message_chat_mentions_member(client, db_session, monkeypatch):
    fake = TaskFakeLLM(structured=[{"action": "chat"}], chat=[LLMResponse(content="VPN 账号需领导审批，次日生效")])

    import app.services.group_chat as group_chat

    monkeypatch.setattr(group_chat, "DeepSeekProvider", lambda: fake)
    conv = db_session.get(models.Conversation, "CONV-0002")
    send_conversation_message(db_session, conversation=conv, actor_no="E10281", content="IT 助理，VPN 怎么申请")
    body = _conversation_out(db_session, conv).model_dump()
    assert [m["role"] for m in body["messages"][-2:]] == ["user", "assistant"]
    assert body["messages"][-1]["participant_no"] == "VE-0003"


def test_group_message_classifier_failure_defaults_chat(client, db_session, monkeypatch):
    fake = TaskFakeLLM(structured=[], chat=[LLMResponse(content="好")])

    import app.services.group_chat as group_chat

    monkeypatch.setattr(group_chat, "DeepSeekProvider", lambda: fake)
    conv = db_session.get(models.Conversation, "CONV-0002")
    send_conversation_message(db_session, conversation=conv, actor_no="E10281", content="随便聊聊")
    body = _conversation_out(db_session, conv).model_dump()
    seed_task_ids = {"T-20260819-DEMO1", "T-20260819-DEMO2", "T-20260819-DEMO3"}
    assert all(t["id"] in seed_task_ids for t in body["tasks"])
    assert [m["role"] for m in body["messages"][-2:]] == ["user", "assistant"]


def test_conversation_out_includes_seed_task(client):
    body = client.get("/api/v1/conversations/CONV-0002").json()
    assert len(body["tasks"]) >= 1
    task = body["tasks"][0]
    assert task["status"] == "approval"
    assert len(task["subtasks"]) == 3
    assert task["subtasks"][2]["approval"]["policy_id"] is None


def test_approve_seed_conversation_task(client, db_session, monkeypatch):
    fake = TaskFakeLLM(chat=[LLMResponse(content="Leader 汇总：入职准备全部完成。")])
    orchestrator = TeamTaskOrchestrator(fake)
    task = orchestrator.approve(
        db_session, task_id="T-20260819-DEMO1", approve=True, actor_no="E10281"
    )
    assert task.status == "completed"
    assert task.summary == "Leader 汇总：入职准备全部完成。"
    assert all(sub.status == "completed" for sub in task.subtasks)


def test_reject_seed_conversation_task(client):
    resp = client.post(
        "/api/v1/tasks/T-20260819-DEMO1/approve",
        json={"approve": False, "actor_no": "E10281"},
    )
    assert resp.status_code == 200
    task = resp.json()
    assert task["status"] == "denied"


def test_orchestrator_accepts_custom_executor(db_session):
    class FakeExecutor:
        def execute(self, db, *, run, subtask, trace_id):
            return {"decision": "allow", "data": {"ok": True, "summary": "虚构执行结果"}}

    from app.services.team_orchestrator import TeamTaskOrchestrator

    orch = TeamTaskOrchestrator(
        FakeLLM([LLMResponse(content="Leader 汇总：完成。")]),
        executor=FakeExecutor(),
    )
    run = orch.create_task(db_session, team_id="TEAM-ONBOARD", request="测试自定义执行器")
    assert run.status == "completed"
    assert all(sub.status == "completed" for sub in run.subtasks)
    assert run.subtasks[0].result


def test_new_mock_workflow_executes_via_gateway(client):
    resp = client.post(
        "/internal/gateway/invoke",
        json={
            "employee_id": "VE-0002",
            "plugin_id": "expense-claim",
            "action": "execute",
            "params": {"employee_name": "张三", "amount": 1200},
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["decision"] == "allow"
    assert body["data"]["workflow"] == "expense-claim"
    assert "报销申请提交" in body["data"]["steps"]


def test_purchase_workflow_requires_whitelist(client):
    """L3 插件（purchase-request）走 P20 白名单：未授权 403 → 申请批准 → allow。"""
    payload = {
        "employee_id": "VE-0004",
        "plugin_id": "purchase-request",
        "action": "execute",
        "params": {"item": "办公显示器"},
    }
    # 未白名单：L3 默认拒绝
    resp = client.post("/internal/gateway/invoke", json=payload)
    assert resp.status_code == 403
    assert resp.json()["error"]["detail"]["policy_id"] == "P-DATA-003"
    # 白名单申请 → 管理员批准
    req = client.post(
        "/api/v1/access-requests",
        params={"applicant_no": "VE-0004"},
        json={"resource_type": "plugin", "resource_id": "purchase-request", "reason": "采购流程演示"},
    )
    assert req.status_code == 201
    approved = client.post(
        f"/api/v1/access-requests/{req.json()['id']}/approve",
        json={"approve": True, "actor_no": "DT-E10281"},
    )
    assert approved.status_code == 200
    assert approved.json()["status"] == "granted"
    # 白名单生效 → allow
    resp = client.post("/internal/gateway/invoke", json=payload)
    assert resp.status_code == 200
    assert resp.json()["decision"] == "allow"


def test_workflow_catalog(client):
    body = client.get("/api/v1/workflows").json()
    assert len(body) == 7
    assert all(w["type"] in ("workflow", "rpa") for w in body)
    expense = next(w for w in body if w["plugin_id"] == "expense-claim")
    assert expense["steps"] == ["报销申请提交", "直属领导审批", "财务复核打款"]
    assert expense["demo_prompt"] == "帮我提交差旅报销"
    assert {e["employee_no"] for e in expense["authorized_employees"]} == {"VE-0002"}
    purchase = next(w for w in body if w["plugin_id"] == "purchase-request")
    assert purchase["data_level"] == "L3"


def test_clear_conversation(client):
    resp = client.delete("/api/v1/conversations/CONV-0002", params={"actor_no": "E10281"})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    body = client.get("/api/v1/conversations/CONV-0002").json()
    assert body["messages"] == []
    assert body["tasks"] == []


def test_conversation_summary_preview_uses_task_status(client):
    body = client.get("/api/v1/conversations?actor_no=E10281").json()
    conv = next(c for c in body if c["id"] == "CONV-0002")
    assert conv["last_message"].startswith("协作任务")


def test_send_message_forbidden_for_other_actor(client):
    resp = client.post(
        "/api/v1/conversations/CONV-0001/messages",
        json={"actor_no": "E20999", "content": "越权尝试"},
    )
    assert resp.status_code == 403


def conv_messages(db_session, conversation_id):
    from app import models
    from sqlalchemy import select

    return list(
        db_session.scalars(
            select(models.ConversationMessage)
            .where(models.ConversationMessage.conversation_id == conversation_id)
            .order_by(models.ConversationMessage.seq)
        ).all()
    )
