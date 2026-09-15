"""企业微信智能机器人长连接客户端（WebSocket）。

协议要点（智能机器人长连接，非自建应用回调）：
- 客户端主动连接 ``wss://openws.work.weixin.qq.com``，无需公网回调地址；
- 用 ``aibot_subscribe`` 订阅（携带 bot_id + secret），订阅成功即开始收消息；
- 每 30 秒发送 ``ping`` 心跳；
- 收到 ``aibot_msg_callback``（明文 JSON）后用 ``aibot_respond_msg`` 回复，
  透传原消息的 ``headers.req_id``；
- 收到 ``disconnected_event`` 或连接异常时自动指数退避重连。

多机器人：每个机器人是独立的 ``WeComBotClient``，各持一条 WebSocket 长连接，
订阅时携带各自 bot_id/secret。消息回调把该机器人的 ``WeComBotSpec`` 传给
``wecom_adapter`` 做双维路由（fixed 岗位员工 / twin 按发起人转接）。

凭据不写日志；dry-run 默认开启，只打印将要发送的内容，不真发。
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select

try:
    from websockets.asyncio.client import connect  # websockets >= 13
except ImportError:  # pragma: no cover - websockets 10-12 兼容
    from websockets import connect  # type: ignore[attr-defined]

from .. import models
from ..database import SessionLocal
from . import config
from .wecom_adapter import BotExecutionContext, handle_bot_message, run_execution, start_execution

logger = logging.getLogger(__name__)
# 交由应用根日志器统一输出，避免模块私有 handler 造成重复日志，
# 同时确保测试、集中采集与审计侧能够捕获断连事件。
logger.setLevel(logging.INFO)


def _render_event_line(event) -> str | None:
    """把一条执行事件渲染成单行 markdown 步骤；不展示的事件返回 None。"""
    if event.event_type in ("answer_chunk", "answer_done"):
        return None
    if event.stage == "queued":
        return None
    title = (event.title or "").strip()
    if not title:
        return None
    if event.event_type == "error":
        icon = "❌"
    elif event.event_type.endswith("_completed") or event.event_type == "agent_started":
        icon = "✅"
    elif event.event_type.endswith("_started"):
        icon = "🔄"
    else:
        icon = "✅"
    return f"{icon} {title}"


def _render_markdown(ctx: BotExecutionContext, steps: list[str], answer: str | None = None) -> str:
    """把执行步骤（+ 可选最终答案）拼成一条企微 stream 的完整 markdown 内容。"""
    lines = [f"🤖 {ctx.employee_name} 正在处理你的问题", ""]
    lines.extend(steps)
    if answer:
        lines.extend(["", answer])
    return "\n".join(lines)


def _poll_events(execution_id: str, after_seq: int) -> list:
    db = SessionLocal()
    try:
        return db.scalars(select(models.AgentExecutionEvent).where(
            models.AgentExecutionEvent.execution_id == execution_id,
            models.AgentExecutionEvent.event_seq > after_seq,
        ).order_by(models.AgentExecutionEvent.event_seq)).all()
    finally:
        db.close()


def _ingest_events(events: list, seen_seq: int, steps: list[str]) -> tuple[int, bool]:
    """把新事件并入步骤列表，返回 (最新 seq, 是否有新增行)。"""
    changed = False
    for event in events:
        seen_seq = event.event_seq
        line = _render_event_line(event)
        if line and line not in steps:
            steps.append(line)
            changed = True
    return seen_seq, changed


# 答案流式展示：企微 stream 计 30 条/分钟，逐字推会超限；改为按块渐进展开。
_ANSWER_CHUNK_DELAY = 0.10   # 每块间隔秒
_ANSWER_MAX_CHUNKS = 20      # 答案最大分块数（与步骤刷新合计仍低于 30 条/分钟）


def _split_answer(answer: str) -> list[str]:
    """把最终答案切成流式展示块：优先在句子边界切，块大小自适应以控制总块数。"""
    text = (answer or "").strip()
    if not text:
        return []
    target = max(24, (len(text) + _ANSWER_MAX_CHUNKS - 1) // _ANSWER_MAX_CHUNKS)
    chunks: list[str] = []
    current = ""
    for ch in text:
        current += ch
        if len(current) >= target and ch in "。！？\n；;!? ":
            chunks.append(current)
            current = ""
    if current:
        chunks.append(current)
    flat: list[str] = []
    for chunk in chunks:
        while len(chunk) > target * 2:
            flat.append(chunk[:target])
            chunk = chunk[target:]
        flat.append(chunk)
    return [piece for piece in flat if piece.strip()]


HEARTBEAT_INTERVAL = 30
RECONNECT_MIN = 1.0
RECONNECT_MAX = 60.0


def _req_id() -> str:
    return uuid4().hex


class WeComBotClient:
    """常驻长连接客户端；由 FastAPI lifespan 作为后台任务启动/停止。

    多 bot 部署时通过 ``spec`` 声明机器人的承载方式；无 spec 时由调用方显式
    传 ``bot_id/secret`` 退化为单 bot（mode="twin"），保持既有测试与存量用法兼容。
    """

    def __init__(
        self,
        bot_id: str = "",
        secret: str = "",
        ws_url: str = "",
        spec: config.WeComBotSpec | None = None,
    ) -> None:
        if spec is not None:
            self.spec = spec
        else:
            self.spec = config.WeComBotSpec(
                bot_id=bot_id, secret=secret,
                name=bot_id or "wecom-bot", alias="twin", mode="twin",
            )
        self.bot_id = self.spec.bot_id
        self.secret = self.spec.secret
        self.ws_url = ws_url
        self._stop = asyncio.Event()

    def stop(self) -> None:
        self._stop.set()

    async def run_forever(self) -> None:
        """循环连接：异常断开后指数退避重连，直到 ``stop()``。"""
        backoff = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                await self._run_once()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # noqa: BLE001 - 任何异常都不终止常驻循环
                logger.error("wecom-bot: 连接异常，%.1fs 后重连：%s", backoff, exc)
            if self._stop.is_set():
                break
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=backoff)
            except asyncio.TimeoutError:
                pass
            backoff = min(backoff * 2, RECONNECT_MAX)

    async def _run_once(self) -> None:
        # 关闭 websockets 库的协议层 ping/pong 保活：企微长连接只响应
        # 应用层 {"cmd":"ping"} 心跳，不回应 RFC6455 协议层 ping，否则会
        # 在默认 20s 后触发 "keepalive ping timeout" 并断开连接。
        async with connect(self.ws_url, ping_interval=None, ping_timeout=None) as ws:
            await self._subscribe(ws)
            hb = asyncio.create_task(self._heartbeat(ws))
            try:
                await self._receive_loop(ws)
            finally:
                hb.cancel()
                try:
                    await hb
                except asyncio.CancelledError:
                    pass

    async def _subscribe(self, ws) -> None:
        req_id = _req_id()
        await ws.send(json.dumps({
            "cmd": "aibot_subscribe",
            "headers": {"req_id": req_id},
            "body": {"bot_id": self.bot_id, "secret": self.secret},
        }, ensure_ascii=False))
        try:
            raw = await asyncio.wait_for(ws.recv(), timeout=15)
            data = json.loads(raw)
            if data.get("errcode") == 0:
                logger.info("wecom-bot[%s]: 订阅成功 req_id=%s", self.spec.alias, req_id)
            else:
                logger.error(
                    "wecom-bot[%s]: 订阅失败 req_id=%s errcode=%s errmsg=%s",
                    self.spec.alias, req_id, data.get("errcode"), data.get("errmsg"),
                )
        except Exception as exc:  # noqa: BLE001 - 订阅异常统一记录，由外层重连兜底
            logger.error("wecom-bot: 等待订阅响应异常：%s", exc)

    async def _receive_loop(self, ws) -> None:
        async for raw in ws:
            try:
                data = json.loads(raw)
            except (TypeError, ValueError):
                logger.warning("wecom-bot: 忽略非 JSON 消息")
                continue
            await self._dispatch(ws, data)

    async def _dispatch(self, ws, data: dict) -> None:
        cmd = data.get("cmd")
        if cmd == "aibot_msg_callback":
            await self._on_message(ws, data)
        elif cmd == "aibot_event_callback":
            self._on_event(data)
        elif "errcode" in data and data.get("errcode") != 0:
            logger.warning(
                "wecom-bot: 响应异常 cmd=%s errcode=%s errmsg=%s",
                cmd, data.get("errcode"), data.get("errmsg"),
            )

    async def _on_message(self, ws, data: dict) -> None:
        req_id = (data.get("headers") or {}).get("req_id", "")
        body = data.get("body") or {}
        msgtype = body.get("msgtype", "text")
        content = ""
        if msgtype == "text":
            content = (body.get("text") or {}).get("content", "").strip()
        elif msgtype == "voice":
            content = (body.get("voice") or {}).get("content", "").strip()
        from_user = (body.get("from") or {}).get("userid", "")
        if not content:
            return

        # DRY-RUN：不建立执行流，仅打印最终回答用于联调观察。
        if config.wecom_dry_run():
            answer = await asyncio.to_thread(handle_bot_message, from_user, content, self.spec)
            logger.info("wecom-bot[%s][DRY-RUN] 将回复 %s：%s", self.spec.alias, from_user, answer)
            return

        # 实时流式：先建立执行记录，再后台跑 agent，同时把执行事件流式刷新到企微。
        try:
            ctx = await asyncio.to_thread(start_execution, from_user, content, self.spec)
        except HTTPException as exc:
            await self._respond(ws, req_id, f"抱歉，暂时无法处理：{exc.detail}")
            return
        await self._stream_execution(ws, req_id, ctx, content)

    async def _respond(self, ws, req_id: str, content: str) -> None:
        # aibot_respond_msg 不支持 msgtype=text（text 仅用于 enter_chat 欢迎语），
        # 一次性文本回复使用 markdown（content 最长 20480 字节，UTF-8）。
        await ws.send(json.dumps({
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {"msgtype": "markdown", "markdown": {"content": content}},
        }, ensure_ascii=False))

    async def _send_stream(self, ws, req_id: str, stream_id: str, finish: bool, content: str) -> None:
        """发送/刷新企微 stream 流式消息（相同 stream_id 覆盖更新，finish=True 结束）。"""
        await ws.send(json.dumps({
            "cmd": "aibot_respond_msg",
            "headers": {"req_id": req_id},
            "body": {
                "msgtype": "stream",
                "stream": {"id": stream_id, "finish": finish, "content": content},
            },
        }, ensure_ascii=False))

    async def _stream_execution(self, ws, req_id: str, ctx: BotExecutionContext, content: str) -> None:
        """后台执行 agent，同时把执行事件流式刷新为企微 stream 消息。

        agent 在独立线程运行（事件写入 DB），本协程轮询 ``AgentExecutionEvent``
        表并实时刷新 stream，最终以 finish=True 带上完整答案收尾。
        """
        stream_id = f"s-{uuid4().hex}"
        steps: list[str] = []
        await self._send_stream(
            ws, req_id, stream_id, finish=False,
            content=_render_markdown(ctx, ["🔄 正在受理任务…"]),
        )

        agent_task = asyncio.create_task(asyncio.to_thread(run_execution, ctx, content))
        seen_seq = 0
        try:
            while not agent_task.done():
                events = _poll_events(ctx.execution_id, seen_seq)
                seen_seq, changed = _ingest_events(events, seen_seq, steps)
                if changed:
                    await self._send_stream(
                        ws, req_id, stream_id, finish=False, content=_render_markdown(ctx, steps),
                    )
                await asyncio.sleep(0.5)

            try:
                answer = agent_task.result()
            except Exception:  # noqa: BLE001 - 兜底，保证最终帧仍能 finish
                logger.exception("wecom-bot: 获取 agent 结果异常 execution_id=%s", ctx.execution_id)
                answer = "抱歉，处理过程中出现问题，请稍后再试。"

            # 收尾前再消费一次剩余事件（complete/error 等）。
            events = _poll_events(ctx.execution_id, seen_seq)
            _, _ = _ingest_events(events, seen_seq, steps)

            # 答案流式：把最终答案按块逐步展开，覆盖刷新，模拟打字机效果。
            shown = ""
            for chunk in _split_answer(answer):
                shown += chunk
                await self._send_stream(
                    ws, req_id, stream_id, finish=False,
                    content=_render_markdown(ctx, steps, shown),
                )
                await asyncio.sleep(_ANSWER_CHUNK_DELAY)
            # 最终帧 finish=true，带完整答案（分块中间帧被覆盖，保证最终完整无缺字）。
            await self._send_stream(
                ws, req_id, stream_id, finish=True,
                content=_render_markdown(ctx, steps, answer),
            )
        finally:
            # 连接中断时 agent 线程仍在跑，等待其结束，避免 task 泄漏/未捕获异常。
            if not agent_task.done():
                try:
                    await agent_task
                except Exception:  # noqa: BLE001
                    pass

    async def _heartbeat(self, ws) -> None:
        while True:
            await asyncio.sleep(HEARTBEAT_INTERVAL)
            await ws.send(json.dumps({"cmd": "ping", "headers": {"req_id": _req_id()}}))

    def _on_event(self, data: dict) -> None:
        event = (data.get("body") or {}).get("event") or {}
        if event.get("eventtype") == "disconnected_event":
            logger.warning("wecom-bot: 收到 disconnected_event，准备重连")


def create_clients() -> list[WeComBotClient]:
    """按配置构建全部长连接客户端；未启用或配置不全时返回空列表（可插拔）。"""
    if not config.wecom_bot_enabled():
        return []
    specs = config.wecom_bots()
    if not specs:
        logger.warning("wecom-bot: 已启用但 bot_id/secret 未配置，跳过启动")
        return []
    ws_url = config.wecom_ws_url()
    clients = [WeComBotClient(spec=spec, ws_url=ws_url) for spec in specs]
    logger.info("wecom-bot: 将启动 %d 条机器人长连接：%s",
                len(clients), ",".join(spec.alias for spec in specs))
    return clients


@asynccontextmanager
async def managed_lifespan():
    """可插拔的企微长连接生命周期：为每个机器人各起一条常驻连接，未启用时是空操作。

    在 FastAPI lifespan 中一行接入：

        async with wecom_bot.managed_lifespan():
            yield

    开关与配置集中在 config.py（DWP_WECOM_BOT_ENABLED / DWP_WECOM_BOTS 等），
    本函数不感知业务。不同机器人的连接互不影响，各自独立重连。
    """
    clients = create_clients()
    tasks = [asyncio.create_task(client.run_forever()) for client in clients]
    try:
        yield
    finally:
        for client, task in zip(clients, tasks):
            client.stop()
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass
