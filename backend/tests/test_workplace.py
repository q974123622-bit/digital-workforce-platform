"""Sprint 7 个人工作中心（职场）：技能 CRUD / 技能注入 / 聚合 / 会话 / 群聊基础编排。"""

from datetime import datetime

from fastapi import HTTPException

from app import models
from app.services.chat import ChatOrchestrator
from app.services.group_chat import _find_recent_task, classify_task_request, send_group_message
from app.services.llm import LLMProvider, LLMResponse
from app.services.team_orchestrator import TeamTaskOrchestrator


class FakeLLM(LLMProvider):
    def __init__(self, script, structured=None):
        self.script = list(script)
        self.calls = []
        self.structured_calls = []
        self.structured = structured if structured is not None else {}

    def chat(self, messages, tools=None):
        self.calls.append(messages)
        return self.script.pop(0)

    def tool_call(self, messages, tools):
        return self.chat(messages, tools)

    def structured_output(self, messages, schema):
        self.structured_calls.append(messages)
        return self.structured


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
    # 技能以 Plugin 为唯一载体（生产路径见 workplace._sync_skill_plugin 自动建插件/版本/绑定），
    # 因此这里必须一并建插件、已发布版本与绑定，否则「已停用的技能不该被注入」这条口径无法成立。
    db_session.add(models.Plugin(
        id="SKILL-SK-TEST-1", name="报销制度速答", type="skill", plugin_type="skill",
        scope="personal", owner_human_no="E10281", status="active",
        description="掌握差旅报销标准", current_version="1.0.0",
    ))
    db_session.add(models.PluginVersion(
        plugin_id="SKILL-SK-TEST-1", version="1.0.0", deployment_mode="instruction",
        artifact_path="", sha256="test",
        manifest={"instruction": "1. 差旅住宿标准：一线城市每晚不超过 600 元。"},
        data_level="L1", review_status="approved", publish_status="published",
        submitted_by="E10281", reviewed_by="automatic-policy",
    ))
    db_session.add(models.AgentPluginBinding(
        plugin_id="SKILL-SK-TEST-1", target_agent_id="DT-E10281",
        authorized_by="automatic-policy", admin_enabled=True, employee_enabled=True,
        decision_mode="allow",
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
        "AI-GENERAL", "AI-INVESTMENT", "AI-RESEARCH", "AI-FUND", "AI-XYZQ", "AI-AUDIT"
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


# ---- 任务链路看得见上文（C 档） ----


def _group_conversation_with_history(db_session, conversation_id: str) -> models.Conversation:
    """构造一个群聊：上一轮已有一问一答，本轮是依赖上文的延续指令。"""
    conv = models.Conversation(
        id=conversation_id, kind="group", title="", owner_human_no="E10281",
        participants=[
            {"employee_no": "DT-E10281", "role": "organizer"},
            {"employee_no": "AI-GENERAL", "role": "member"},
        ],
    )
    db_session.add(conv)
    db_session.add_all([
        models.ConversationMessage(
            conversation_id=conversation_id, participant_no="E10281", participant_name="张三",
            role="user", content="帮我准备入职材料", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id=conversation_id, participant_no="AI-GENERAL",
            participant_name="AI员工平台", role="assistant", content="材料清单已生成。",
            seq=2, generation=0,
        ),
        models.ConversationMessage(
            conversation_id=conversation_id, participant_no="E10281", participant_name="张三",
            role="user", content="再改一下，补上体检指引", seq=3, generation=0,
        ),
    ])
    db_session.commit()
    return conv


def _grant_onboarding_plugin(db_session) -> None:
    """可执行插件的授权真相是 AgentPluginBinding（admin/employee 均启用 + allow）。"""
    db_session.add(models.Plugin(
        id="PL-ONBOARD", name="入职流程 Workflow", type="workflow",
        plugin_type="workflow", status="active", data_level="L1",
        current_version="1.0.0",
    ))
    db_session.add(models.PluginVersion(
        plugin_id="PL-ONBOARD", version="1.0.0", deployment_mode="external",
        artifact_path="", sha256="test", manifest={"tools": []},
        data_level="L1", review_status="approved", publish_status="published",
        submitted_by="seed", reviewed_by="seed",
    ))
    db_session.add(models.AgentPluginBinding(
        plugin_id="PL-ONBOARD", target_agent_id="AI-GENERAL",
        authorized_by="seed", admin_enabled=True, employee_enabled=True,
        decision_mode="allow",
    ))
    db_session.commit()


def test_classify_task_request_sees_recent_conversation(db_session):
    """任务判定带上文：「再改一下」这类延续指令也要能被判成任务。"""
    conv = _group_conversation_with_history(db_session, "CONV-CLASSIFY")
    fake = FakeLLM([], structured={"action": "task"})
    assert classify_task_request(
        db_session, conversation=conv, content="再改一下，补上体检指引", provider=fake,
    ) is True

    prompt = fake.structured_calls[0]
    user_content = next(m["content"] for m in prompt if m["role"] == "user")
    assert "最近对话" in user_content
    assert "我：帮我准备入职材料" in user_content
    assert "AI员工平台：材料清单已生成。" in user_content
    # 本轮用户消息只作为「用户消息」出现一次，不在历史块里重复
    assert user_content.count("再改一下，补上体检指引") == 1
    system_content = next(m["content"] for m in prompt if m["role"] == "system")
    assert "最近对话" in system_content


def test_decompose_sees_recent_conversation_and_keeps_raw_request(db_session):
    """子任务拆解带上文；TaskRun.request 仍是原始用户文本（去重与审计口径不变）。"""
    conv = _group_conversation_with_history(db_session, "CONV-DECOMPOSE")
    _grant_onboarding_plugin(db_session)
    fake = FakeLLM([], structured={"subtasks": [
        {"worker_id": "AI-GENERAL", "summary": "补充体检指引", "plugin_ids": ["PL-ONBOARD"]},
    ]})
    orchestrator = TeamTaskOrchestrator(fake)

    run, _leader = orchestrator.prepare_conversation_task(
        db_session, conversation=conv, actor_no="E10281", request="再改一下，补上体检指引",
    )
    assert run.request == "再改一下，补上体检指引"
    assert run.subtasks[0]["summary"] == "补充体检指引"

    prompt = fake.structured_calls[0]
    user_content = next(m["content"] for m in prompt if m["role"] == "user")
    assert "最近对话" in user_content
    assert "我：帮我准备入职材料" in user_content
    assert "AI员工平台：材料清单已生成。" in user_content
    assert user_content.count("再改一下，补上体检指引") == 1


# ---- 会话检索（历史消息正文全文） ----


def _search_conv(conversation_id: str, participants: list[dict], *, kind: str = "direct", title: str = "") -> models.Conversation:
    return models.Conversation(
        id=conversation_id, kind=kind, title=title, owner_human_no="E10281",
        participants=participants,
    )


def test_search_conversation_by_message_content(client, db_session):
    """只出现在历史消息正文里的词也要能搜到（截图反馈的根因），并给出片段与命中 seq。"""
    db_session.add(_search_conv("CONV-SEARCH-1", [{"employee_no": "AI-GENERAL", "role": "member"}]))
    db_session.add_all([
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-1", participant_no="E10281", participant_name="张三",
            role="user", content="差旅报销标准是多少？", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-1", participant_no="AI-GENERAL", participant_name="AI员工平台",
            role="assistant",
            content="| 城市 | 标准 |\n| --- | --- |\n| 一线 | 600 元 |",
            seq=2, generation=0,
        ),
    ])
    db_session.commit()

    # 路由顺序回归：/search 必须声明在 /{conversation_id} 之前，否则这里会被当成会话 id 匹配。
    resp = client.get("/api/v1/conversations/search?actor_no=E10281&q=一线")
    assert resp.status_code == 200
    hits = resp.json()
    assert [hit["conversation"]["id"] for hit in hits] == ["CONV-SEARCH-1"]
    hit = hits[0]
    assert hit["matched_in"] == "content"
    assert hit["match_count"] == 1
    assert hit["match_seq"] == 2
    assert hit["match_role"] == "assistant"
    assert hit["match_speaker"] == "AI员工平台"
    # 片段压平 Markdown：关键词保留，表格竖线不残留。
    assert "一线" in hit["snippet"]
    assert "|" not in hit["snippet"]


def test_search_prefers_name_match_over_content_match(client, db_session):
    """会话名/成员名命中排在内容命中之前；搜自己的分身名也能命中（旧逻辑搜索时整行隐藏）。"""
    db_session.add_all([
        _search_conv("CONV-SEARCH-TWIN", [{"employee_no": "DT-E10281", "role": "member"}]),
        _search_conv("CONV-SEARCH-CONTENT", [{"employee_no": "AI-GENERAL", "role": "member"}]),
    ])
    db_session.add(models.ConversationMessage(
        conversation_id="CONV-SEARCH-CONTENT", participant_no="E10281", participant_name="张三",
        role="user", content="帮我看看张三的入职材料", seq=1, generation=0,
    ))
    db_session.commit()

    hits = client.get("/api/v1/conversations/search?actor_no=E10281&q=张三").json()
    assert [hit["conversation"]["id"] for hit in hits][:2] == ["CONV-SEARCH-TWIN", "CONV-SEARCH-CONTENT"]
    assert hits[0]["matched_in"] == "name"
    assert hits[0]["match_seq"] is None
    # 成员名来自 DigitalEmployee（participants JSON 里只有工号）。
    assert hits[0]["conversation"]["participants"][0]["name"] == "张三的数字分身"
    assert hits[1]["matched_in"] == "content"


def test_search_escapes_like_wildcards(client, db_session):
    """% 与 _ 按字面量匹配，不能变成通配符把全部会话都捞出来。"""
    db_session.add_all([
        _search_conv("CONV-SEARCH-PLAIN", [{"employee_no": "AI-GENERAL", "role": "member"}]),
        _search_conv("CONV-SEARCH-PERCENT", [{"employee_no": "AI-INVESTMENT", "role": "member"}]),
    ])
    db_session.add_all([
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-PLAIN", participant_no="E10281", participant_name="张三",
            role="user", content="完成度 100% 时再提交", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-PERCENT", participant_no="E10281", participant_name="张三",
            role="user", content="这条消息里没有那个符号", seq=1, generation=0,
        ),
    ])
    db_session.commit()

    hits = client.get("/api/v1/conversations/search?actor_no=E10281&q=100%25").json()
    assert [hit["conversation"]["id"] for hit in hits] == ["CONV-SEARCH-PLAIN"]

    # 单独一个 % 只应命中字面量含 % 的那条，而不是命中全部会话。
    only_percent = client.get("/api/v1/conversations/search?actor_no=E10281&q=%25").json()
    assert [hit["conversation"]["id"] for hit in only_percent] == ["CONV-SEARCH-PLAIN"]

    # _ 同理：不转义会匹配任意单字符，把两条都捞出来。
    underscore = client.get("/api/v1/conversations/search?actor_no=E10281&q=_").json()
    assert underscore == []


def test_search_skips_other_owner_hidden_and_superseded(client, db_session):
    """只能搜到自己的会话；已删除会话与被重跑取代的旧回复不进入结果。"""
    db_session.add_all([
        _search_conv("CONV-SEARCH-MINE", [{"employee_no": "AI-GENERAL", "role": "member"}]),
        _search_conv("CONV-SEARCH-OTHER", [{"employee_no": "AI-GENERAL", "role": "member"}]),
        _search_conv("CONV-SEARCH-HIDDEN", [{"employee_no": "AI-GENERAL", "role": "member"}]),
        _search_conv("CONV-SEARCH-SUPERSEDED", [{"employee_no": "AI-GENERAL", "role": "member"}]),
    ])
    db_session.commit()
    db_session.get(models.Conversation, "CONV-SEARCH-OTHER").owner_human_no = "E20999"
    db_session.get(models.Conversation, "CONV-SEARCH-HIDDEN").hidden_at = datetime.now()
    db_session.add_all([
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-MINE", participant_no="E10281", participant_name="张三",
            role="user", content="立项材料请查收", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-OTHER", participant_no="E20999", participant_name="陈晓萌",
            role="user", content="立项材料请查收", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-HIDDEN", participant_no="E10281", participant_name="张三",
            role="user", content="立项材料请查收", seq=1, generation=0,
        ),
        models.ConversationMessage(
            conversation_id="CONV-SEARCH-SUPERSEDED", participant_no="E10281", participant_name="张三",
            role="user", content="立项材料请查收", seq=1, generation=0,
            superseded_at=datetime.now(),
        ),
    ])
    db_session.commit()

    hits = client.get("/api/v1/conversations/search?actor_no=E10281&q=立项").json()
    assert [hit["conversation"]["id"] for hit in hits] == ["CONV-SEARCH-MINE"]


def test_search_empty_query_returns_empty_list(client, db_session):
    """空关键字（含纯空格）直接返回空数组，不当作「匹配全部」。"""
    db_session.add(_search_conv("CONV-SEARCH-EMPTY", [{"employee_no": "AI-GENERAL", "role": "member"}]))
    db_session.commit()

    assert client.get("/api/v1/conversations/search?actor_no=E10281&q=").json() == []
    assert client.get("/api/v1/conversations/search?actor_no=E10281&q=%20%20").json() == []
