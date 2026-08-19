"""AgentTeams Gateway（Sprint 8）：Matrix Client-Server API 封装。

门户把任务以房间消息发到 AgentTeams 团队房间（Manager/Workers 协作），
再轮询房间消息回收完成汇报。任何失败统一映射 AgentTeamsUnavailableError，
由上层降级到内置编排。

凭据只从环境变量读取（AGENTTEAMS_MATRIX_URL / ADMIN_USER / ADMIN_PASSWORD，
本地 backend/.env gitignored），不入库、不落日志。
"""

import time
from dataclasses import dataclass

import httpx

from . import config


class AgentTeamsUnavailableError(RuntimeError):
    """AgentTeams 不可用（容器停 / 登录失败 / 网络失败 / 超时）。"""


@dataclass(frozen=True)
class AgentTeamsResult:
    ok: bool
    mode: str  # agentteams | builtin
    detail: str = ""


class AgentTeamsGateway:
    """Matrix 客户端：login → send_message → poll_messages → parse_completion。"""

    def __init__(
        self,
        base_url: str | None = None,
        user: str | None = None,
        password: str | None = None,
        matrix_token: str | None = None,
        timeout: float = 8.0,
    ):
        self.base_url = (base_url or config.get(config.AGENTTEAMS_MATRIX_URL) or "").rstrip("/")
        self.user = user or config.get(config.AGENTTEAMS_ADMIN_USER) or ""
        self.password = password or config.get(config.AGENTTEAMS_ADMIN_PASSWORD) or ""
        self.timeout = timeout
        # 优先使用已配置的 Matrix token（Manager/机器人身份，已在团队房间内）
        self._token = matrix_token or config.get(config.AGENTTEAMS_MATRIX_TOKEN) or None
        self._client = httpx.Client(timeout=timeout)

    # ---------- 登录 ----------

    def login(self) -> str:
        if self._token:
            return self._token
        if not self.base_url or not self.user or not self.password:
            raise AgentTeamsUnavailableError("AgentTeams Matrix 配置缺失（AGENTTEAMS_*）")
        try:
            resp = self._client.post(
                f"{self.base_url}/_matrix/client/v3/login",
                json={
                    "type": "m.login.password",
                    "identifier": {"type": "m.id.user", "user": self.user},
                    "password": self.password,
                },
            )
        except httpx.HTTPError as exc:
            raise AgentTeamsUnavailableError(f"Matrix 连接失败：{exc.__class__.__name__}") from exc
        if resp.status_code != 200:
            raise AgentTeamsUnavailableError(f"Matrix 登录失败：HTTP {resp.status_code}")
        self._token = str(resp.json().get("access_token", ""))
        if not self._token:
            raise AgentTeamsUnavailableError("Matrix 登录响应缺少 access_token")
        return self._token

    # ---------- 房间 ----------

    def joined_rooms(self) -> list[str]:
        token = self.login()
        resp = self._client.get(
            f"{self.base_url}/_matrix/client/v3/joined_rooms",
            headers={"Authorization": f"Bearer {token}"},
        )
        if resp.status_code != 200:
            raise AgentTeamsUnavailableError(f"joined_rooms 失败：HTTP {resp.status_code}")
        return list(resp.json().get("joined_rooms", []))

    # ---------- 消息 ----------

    def send_message(self, room_id: str, text: str) -> str:
        token = self.login()
        body = {"msgtype": "m.text", "body": text}
        resp = self._client.put(
            f"{self.base_url}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{int(time.time() * 1000)}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        if resp.status_code != 200:
            raise AgentTeamsUnavailableError(f"发送房间消息失败：HTTP {resp.status_code}")
        return str(resp.json().get("event_id", ""))

    def poll_messages(self, room_id: str, since: str | None = None, limit: int = 30) -> list[dict]:
        token = self.login()
        params: dict = {"dir": "b", "limit": str(limit)}
        if since:
            params["from"] = since
        resp = self._client.get(
            f"{self.base_url}/_matrix/client/v3/rooms/{room_id}/messages",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if resp.status_code != 200:
            raise AgentTeamsUnavailableError(f"拉取房间消息失败：HTTP {resp.status_code}")
        events = resp.json().get("chunk", [])
        out = []
        for ev in events:
            if ev.get("type") == "m.room.message":
                content = ev.get("content") or {}
                if content.get("msgtype") == "m.text":
                    out.append({"sender": ev.get("sender", ""), "body": str(content.get("body", ""))})
        return out

    # ---------- 解析完成汇报 ----------

    @staticmethod
    def parse_completion(messages: list[dict], request_keyword: str) -> str | None:
        """从房间消息中提取任务完成汇报：含"完成/结果/汇总/已"且提及任务关键词的最近一条消息文本。"""
        keywords = ("完成", "结果", "汇总", "报告", "已")
        for msg in reversed(messages):
            body = msg.get("body", "")
            if any(k in body for k in keywords) and (request_keyword in body or len(body) > 20):
                return body[:500]
        return None

    def close(self) -> None:
        self._client.close()
