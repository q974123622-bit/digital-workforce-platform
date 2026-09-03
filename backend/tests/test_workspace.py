"""Sprint 6 员工工作台 + 人设注入测试。"""


def test_workspace_formal_twin(client):
    resp = client.get("/api/v1/employees/DT-E10281/workspace")
    assert resp.status_code == 200
    ws = resp.json()
    assert ws["employee"]["employee_no"] == "DT-E10281"
    assert "架构部员工张三的数字分身" in ws["role_prompt"]
    assert ws["security"]["location"] == "remote"
    assert ws["security"]["internet"] == "deny"
    plugin_ids = {p["plugin_id"] for p in ws["plugins"]}
    assert "knowledge-l1" not in plugin_ids
    assert "knowledge-l2" not in plugin_ids
    kbs = {kb["knowledge_base_id"]: kb for kb in ws["knowledge_bases"]}
    assert kbs["KB-INTERNAL"]["accessible"] is False
    assert kbs["KB-PUBLIC"]["accessible"] is False


def test_workspace_intern_twin_kb_denied(client):
    ws = client.get("/api/v1/employees/DT-E20999/workspace").json()
    kbs = {kb["knowledge_base_id"]: kb for kb in ws["knowledge_bases"]}
    assert kbs["KB-INTERNAL"]["accessible"] is False
    assert kbs["KB-INTERNAL"]["decision"] == "deny"
    assert kbs["KB-PUBLIC"]["accessible"] is True


def test_workspace_virtual_onboarding_only(client):
    ws = client.get("/api/v1/employees/VE-0001/workspace").json()
    kbs = {kb["knowledge_base_id"]: kb for kb in ws["knowledge_bases"]}
    assert kbs["KB-ONBOARD"]["accessible"] is True
    assert kbs["KB-INTERNAL"]["accessible"] is False


def test_workspace_not_found(client):
    assert client.get("/api/v1/employees/VE-9999/workspace").status_code == 404


def test_chat_system_prompt_injects_persona(db_session):
    from app.services.chat import ChatOrchestrator
    from app.services.llm import LLMProvider, LLMResponse

    class RecorderLLM(LLMProvider):
        def __init__(self):
            self.seen = []

        def chat(self, messages, tools=None):
            self.seen.append(messages)
            return LLMResponse(content="回答")

        def tool_call(self, messages, tools):
            return self.chat(messages, tools)

        def structured_output(self, messages, schema):
            return {}

    fake = RecorderLLM()
    ChatOrchestrator(fake).handle_message(db_session, employee_no="AI-GENERAL", message="你好", session_id=None)
    system = fake.seen[0][0]["content"]
    assert "AI员工平台" in system  # role_prompt 人设注入
    assert "IT 服务知识库" in system  # 知识库清单注入
