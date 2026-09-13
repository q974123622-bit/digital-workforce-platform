"""AgentTeams Gateway（Sprint 8）：Matrix Client-Server API 封装。

门户把任务以房间消息发到 AgentTeams 团队房间（Manager/Workers 协作），
再轮询房间消息回收完成汇报。任何失败统一映射 AgentTeamsUnavailableError，
由上层降级到内置编排。

凭据只从环境变量读取（AGENTTEAMS_MATRIX_URL / ADMIN_USER / ADMIN_PASSWORD，
本地 backend/.env gitignored），不入库、不落日志。
"""

import json
from dataclasses import dataclass
from urllib.parse import urlsplit
from uuid import uuid4

import httpx

from . import config


_MATRIX_SYNC_TIMELINE_LIMIT = 100


class AgentTeamsUnavailableError(RuntimeError):
    """AgentTeams 不可用（容器停 / 登录失败 / 网络失败 / 超时）。"""


@dataclass(frozen=True)
class AgentTeamsResult:
    ok: bool
    mode: str  # agentteams | builtin
    detail: str = ""


@dataclass(frozen=True)
class MatrixEvent:
    event_id: str
    room_id: str
    sender: str
    body: str
    ts: int


@dataclass(frozen=True)
class MatrixSyncBatch:
    events: list[MatrixEvent]
    next_cursor: str


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
        loopback = urlsplit(self.base_url).hostname in {"127.0.0.1", "localhost", "::1"}
        self._client = httpx.Client(timeout=timeout, trust_env=not loopback)

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

    def health(self, room_id: str) -> AgentTeamsResult:
        rooms = self.joined_rooms()
        return AgentTeamsResult(
            ok=room_id in rooms,
            mode="agentteams",
            detail="ready" if room_id in rooms else "room_not_joined",
        )

    def whoami(self) -> str:
        """Return only this token's Matrix MXID, never credential material."""
        payload = self._get_json("/_matrix/client/v3/account/whoami", "whoami")
        user_id = payload.get("user_id") if isinstance(payload, dict) else None
        if not isinstance(user_id, str) or not user_id.startswith("@"):
            raise AgentTeamsUnavailableError("whoami 响应缺少 user_id")
        return user_id

    def joined_members(self, room_id: str) -> set[str]:
        """Return the joined-member MXID set for one room and no profile data."""
        if not isinstance(room_id, str) or not room_id:
            raise AgentTeamsUnavailableError("joined_members 房间无效")
        payload = self._get_json(
            f"/_matrix/client/v3/rooms/{room_id}/joined_members", "joined_members"
        )
        joined = payload.get("joined") if isinstance(payload, dict) else None
        if not isinstance(joined, dict):
            raise AgentTeamsUnavailableError("joined_members 响应无效")
        return {member for member in joined if isinstance(member, str) and member.startswith("@")}

    def _get_json(self, suffix: str, operation: str) -> dict:
        token = self.login()
        try:
            response = self._client.get(
                f"{self.base_url}{suffix}", headers={"Authorization": f"Bearer {token}"}
            )
        except httpx.HTTPError as exc:
            raise AgentTeamsUnavailableError(f"{operation} 连接失败：{exc.__class__.__name__}") from exc
        if response.status_code != 200:
            raise AgentTeamsUnavailableError(f"{operation} 失败：HTTP {response.status_code}")
        try:
            payload = response.json()
        except ValueError as exc:
            raise AgentTeamsUnavailableError(f"{operation} 响应无效") from exc
        if not isinstance(payload, dict):
            raise AgentTeamsUnavailableError(f"{operation} 响应无效")
        return payload

    # ---------- 消息 ----------

    def send_message(self, room_id: str, text: str) -> str:
        token = self.login()
        body = {"msgtype": "m.text", "body": text}
        resp = self._client.put(
            f"{self.base_url}/_matrix/client/v3/rooms/{room_id}/send/m.room.message/{uuid4().hex}",
            headers={"Authorization": f"Bearer {token}"},
            json=body,
        )
        if resp.status_code != 200:
            raise AgentTeamsUnavailableError(f"发送房间消息失败：HTTP {resp.status_code}")
        return str(resp.json().get("event_id", ""))

    def send_protocol_message(self, room_id: str, text: str, protocol_line: str) -> str:
        return self.send_message(room_id, f"{text.rstrip()}\n\n{protocol_line}")

    def sync_messages(
        self, since: str | None, room_ids: set[str], timeout_ms: int = 0
    ) -> MatrixSyncBatch:
        token = self.login()
        params = {
            "timeout": str(timeout_ms),
            "filter": json.dumps(
                {"room": {"timeline": {"limit": _MATRIX_SYNC_TIMELINE_LIMIT}}},
                separators=(",", ":"),
            ),
        }
        if since:
            params["since"] = since
        response = self._client.get(
            f"{self.base_url}/_matrix/client/v3/sync",
            headers={"Authorization": f"Bearer {token}"},
            params=params,
        )
        if response.status_code != 200:
            raise AgentTeamsUnavailableError(f"sync失败：HTTP {response.status_code}")
        data = response.json()
        events = []
        for room_id, room_data in (data.get("rooms", {}).get("join", {})).items():
            if room_id not in room_ids:
                continue
            timeline = room_data.get("timeline", {})
            if timeline.get("limited") is True:
                raise AgentTeamsUnavailableError("sync返回limited时间线，拒绝推进游标")
            for event in timeline.get("events", []):
                content = event.get("content") or {}
                if event.get("type") == "m.room.message" and content.get("msgtype") == "m.text":
                    events.append(
                        MatrixEvent(
                            event_id=str(event.get("event_id", "")),
                            room_id=room_id,
                            sender=str(event.get("sender", "")),
                            body=str(content.get("body", "")),
                            ts=int(event.get("origin_server_ts", 0)),
                        )
                    )
        return MatrixSyncBatch(events=events, next_cursor=str(data.get("next_batch", "")))

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
                    out.append(
                        {
                            "sender": ev.get("sender", ""),
                            "body": str(content.get("body", "")),
                            "event_id": ev.get("event_id", ""),
                            "ts": ev.get("origin_server_ts", 0),
                        }
                    )
        return out

    # ---------- 解析完成汇报 ----------

    @staticmethod
    def parse_completion(
        messages: list[dict],
        request_keyword: str,
        since_ts: int | None = None,
        task_id: str | None = None,
        exclude_senders: set[str] | None = None,
    ) -> str | None:
        """从房间消息中提取任务完成汇报：含"完成/结果/汇总/已"且提及任务关键词的最近一条消息文本。

        since_ts 用于排除发送任务之前的旧消息，避免误匹配历史汇报；
        task_id 优先：回执若带 task_id 则精确匹配，防止串任务；
        exclude_senders 用于排除平台自身发送的任务消息（含 task_id，不得当回执）。
        """
        # “已收到/已派发”只是 ACK，不能结束任务。终态必须同时携带 task_id
        # 和明确的完成协议/语义。
        terminal_keywords = ("TASK_COMPLETED", "完成", "已交付", "最终汇总", "最终报告")
        excluded = exclude_senders or set()
        if task_id:
            for msg in reversed(messages):
                body = msg.get("body", "")
                if msg.get("sender", "") in excluded:
                    continue
                if (
                    task_id in body
                    and any(k in body for k in terminal_keywords)
                    and (since_ts is None or (msg.get("ts") or 0) >= since_ts)
                ):
                    return body
        for msg in reversed(messages):
            body = msg.get("body", "")
            if msg.get("sender", "") in excluded:
                continue
            if since_ts is not None and (msg.get("ts") or 0) < since_ts:
                continue
            if any(k in body for k in terminal_keywords) and request_keyword and request_keyword in body:
                return body
        return None

    def close(self) -> None:
        self._client.close()
