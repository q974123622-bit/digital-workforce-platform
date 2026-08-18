"""黄金链路联调脚本（T3-02）。

把「问答 → RAG 检索 → 团队任务 → 审批 → 审计」串成一条完整演示链路，可重复运行。

用法：
    cd backend
    .\\.venv\\Scripts\\python.exe ..\\scripts\\golden_chain.py [--base-url http://127.0.0.1:8000]

退出码：0 = 全链路通过；1 = 存在失败步骤。
"""

import argparse
import sys
import time

import httpx


class Chain:
    def __init__(self, base_url: str):
        self.base_url = base_url.rstrip("/")
        self.client = httpx.Client(base_url=self.base_url, timeout=240)
        self.results: list[tuple[str, bool, str]] = []

    def api(self, method: str, path: str, **kwargs):
        resp = self.client.request(method, path, **kwargs)
        if resp.status_code >= 400:
            raise RuntimeError(f"{method} {path} -> HTTP {resp.status_code}: {resp.text[:200]}")
        return resp.json()

    def step(self, name: str, fn) -> None:
        try:
            fn()
            self.results.append((name, True, ""))
            print(f"  [PASS] {name}")
        except Exception as exc:  # noqa: BLE001
            self.results.append((name, False, str(exc)))
            print(f"  [FAIL] {name} -> {exc}")

    def report(self) -> int:
        passed = sum(1 for _, ok, _ in self.results if ok)
        print("\n" + "=" * 60)
        print(f"黄金链路联调结果：{passed}/{len(self.results)} 通过")
        for name, ok, err in self.results:
            if not ok:
                print(f"  - {name}: {err}")
        print("=" * 60)
        return 0 if passed == len(self.results) else 1


def main() -> int:
    parser = argparse.ArgumentParser(description="数字员工平台黄金链路联调")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="后端地址")
    args = parser.parse_args()
    chain = Chain(args.base_url)
    v1 = "/api/v1"

    print("== 黄金链路联调：问答 → RAG → 团队任务 → 审批 → 审计 ==\n")

    # 1. 健康检查
    def step_health():
        body = chain.api("GET", "/health")
        assert body.get("status") == "ok", body

    chain.step("1. 健康检查", step_health)

    # 2. 正式分身问答（RAG 检索内部知识库 → Allow → 正常回答）
    session_id = None

    def step_formal_chat():
        nonlocal session_id
        body = chain.api(
            "POST",
            f"{v1}/employees/DT-E10281/chat",
            json={"message": "查询一下内部制度。"},
        )
        assert body.get("message"), "回答为空"
        assert body.get("policy_denied") is None, "正式员工不应被拒绝"
        cards = body.get("tool_cards") or []
        assert any(c.get("decision") == "allow" for c in cards), f"缺少 allow 工具卡片: {cards}"
        session_id = body.get("session_id")

    chain.step("2. 正式分身问答（内部制度 → Allow）", step_formal_chat)

    # 3. 实习生分身问答（同一问题 → Policy Denied）
    def step_intern_chat():
        body = chain.api(
            "POST",
            f"{v1}/employees/DT-E20999/chat",
            json={"message": "查询一下内部制度。"},
        )
        denied = body.get("policy_denied")
        assert denied, "实习生应返回 Policy Denied"
        assert denied.get("policy_id") == "POLICY-002", denied

    chain.step("3. 实习生问答（内部制度 → POLICY-002 Deny）", step_intern_chat)

    # 4. RAG 检索（向量命中 IT 知识库）
    def step_rag():
        body = chain.api(
            "POST",
            "/internal/knowledge/search",
            json={
                "employee_id": "DT-E10281",
                "knowledge_base_id": "KB-IT-SERVICE",
                "query": "企业微信登录不上怎么办",
                "trace_id": "GOLDEN-RAG-001",
            },
        )
        assert body.get("decision") == "allow", body
        hits = (body.get("data") or {}).get("hits") or []
        assert len(hits) > 0, "RAG 未命中任何片段"
        assert body["data"].get("source") == "rag", body["data"].get("source")

    chain.step("4. RAG 向量检索（KB-IT-SERVICE 命中）", step_rag)

    # 5. 发起团队任务 → 3 子任务 → 敏感操作审批挂起
    task_id = None

    def step_team_create():
        nonlocal task_id
        body = chain.api(
            "POST",
            f"{v1}/teams/TEAM-ONBOARD/tasks",
            json={"request": "帮王小明完成入职准备"},
        )
        assert body.get("status") == "approval", body.get("status")
        subs = body.get("subtasks") or []
        assert len(subs) == 3, f"子任务数 {len(subs)}"
        assert [s["status"] for s in subs] == ["completed", "completed", "approval"], [s["status"] for s in subs]
        task_id = body.get("id")

    chain.step("5. 团队任务发起（3 子任务 → 审批挂起）", step_team_create)

    # 6. 审批通过 → 完成 + Leader 汇总
    def step_approve():
        assert task_id, "缺少 task_id"
        body = chain.api(
            "POST",
            f"{v1}/tasks/{task_id}/approve",
            json={"approve": True, "actor_no": "E10281"},
        )
        assert body.get("status") == "completed", body.get("status")
        assert body.get("summary"), "缺少 Leader 汇总"
        assert all(s["status"] == "completed" for s in body.get("subtasks", [])), "存在未完成子任务"

    chain.step("6. 审批通过 → 完成 + Leader 汇总", step_approve)

    # 7. 审计追溯（任务 trace 贯穿）
    def step_audit():
        assert task_id, "缺少 task_id"
        events = chain.api("GET", f"{v1}/audit", params={"trace_id": task_id})
        actions = {e["action"] for e in events}
        assert "create" in actions and "approve" in actions and "summarize" in actions, actions
        assert "rpa-report" in {e["plugin_id"] for e in events}, "缺少敏感报表审计"

    chain.step("7. 审计追溯（trace 贯穿 6 类事件）", step_audit)

    # 8. 会话历史持久化
    def step_session():
        assert session_id, "缺少 session_id"
        msgs = chain.api("GET", f"{v1}/chat/sessions/{session_id}/messages")
        roles = [m["role"] for m in msgs]
        assert "user" in roles and "assistant" in roles, roles

    chain.step("8. 会话历史持久化", step_session)

    return chain.report()


if __name__ == "__main__":
    sys.exit(main())
