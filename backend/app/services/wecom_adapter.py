"""企业微信智能机器人长连接编排。

职责：将机器人体系 staff ID（``from.userid``）路由到员工及其数字分身，
复用现有 ``run_agent`` / ``ChatOrchestrator`` 链路让分身真正生成回答，并返回
回答文本。消息的接收与回复由 ``wecom_bot.WeComBotClient``（WebSocket 长连接）
负责，本模块不直接对外发送。

处理函数自建 DB Session，避免复用已关闭的请求级 Session。凭据不写日志。
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from .. import models
from ..database import SessionLocal
from . import config, execution_events
from .chat import ChatOrchestrator, ChatResult
from .harness_agent import run_agent
from .llm import DeepSeekProvider, LLMUnavailableError

logger = logging.getLogger(__name__)

# 复用 employees.chat 的 harness 白名单：这些身份在 harness 执行模式下走容器内
# 知识代理循环，其余走内置 ChatOrchestrator。
_HARNESS_EMPLOYEE_IDS = {"AI-GENERAL", "AI-INVESTMENT", "AI-RESEARCH", "AI-FUND", "DT-E10281", "DT-E20999"}


def resolve_target(
    db: Session, bot: config.WeComBotSpec | None, from_user: str,
) -> tuple[models.HumanEmployee, models.DigitalEmployee]:
    """双维路由：把企微 staff ID 解析为 (发起真人, 被呼叫员工)。

    - 发起真人：始终按 ``DirectoryBinding(provider=wecom)`` 全局映射解析，
      与机器人无关，用于 requester_human_no / 授权 / 审计；未绑定则拒绝。
    - 被呼叫员工（bot 承载方式）：
      - ``bot is None`` 或 ``bot.mode == "twin"``：发起真人自己的数字分身；
      - ``bot.mode == "fixed"``：``bot.employee_no`` 指定的岗位员工（AI-GENERAL 等）。
    """
    binding = db.scalar(
        select(models.DirectoryBinding).where(
            models.DirectoryBinding.provider == "wecom",
            models.DirectoryBinding.external_user_id == from_user,
            models.DirectoryBinding.status == "active",
        )
    )
    if binding is None:
        raise HTTPException(status_code=404, detail=f"企微用户未绑定员工：{from_user}")
    human = db.get(models.HumanEmployee, binding.human_employee_no)
    if human is None or human.status != "active":
        raise HTTPException(status_code=404, detail=f"未找到绑定员工：{binding.human_employee_no}")

    if bot is not None and bot.mode == "fixed":
        if not bot.employee_no:
            raise HTTPException(status_code=500, detail="机器人配置缺少承载员工（employee_no）")
        employee = db.get(models.DigitalEmployee, bot.employee_no)
        if employee is None or employee.status != "active":
            raise HTTPException(status_code=404, detail=f"承载员工未启用：{bot.employee_no}")
        return human, employee

    twin = db.scalar(
        select(models.DigitalEmployee).where(
            models.DigitalEmployee.type == "twin",
            models.DigitalEmployee.owner_human_no == binding.human_employee_no,
            models.DigitalEmployee.status == "active",
        )
    )
    if twin is None:
        raise HTTPException(status_code=404, detail=f"未找到数字分身：{binding.human_employee_no}")
    return human, twin


def route_to_twin(db: Session, from_user: str) -> tuple[models.HumanEmployee, models.DigitalEmployee]:
    """（兼容入口）将企微机器人 staff ID 路由到对应真人及其数字分身（twin）。"""
    return resolve_target(db, bot=None, from_user=from_user)


def _answer(
    db: Session, employee_id: str, requester_human_no: str, content: str,
    trace_id: str | None = None,
) -> ChatResult:
    """让数字分身真正生成回答，返回 ChatResult（含 tool_cards）。"""
    mode = config.get("DWP_AGENT_EXECUTION_MODE", "harness") or "harness"
    if employee_id in _HARNESS_EMPLOYEE_IDS and mode == "harness":
        return run_agent(
            db,
            employee_id=employee_id,
            requester_human_no=requester_human_no,
            message=content,
            history=[],
            trace_id=trace_id,
        )
    return ChatOrchestrator(DeepSeekProvider()).handle_message(
        db,
        employee_no=employee_id,
        message=content,
        session_id=None,
        requester_human_no=requester_human_no,
    )


def handle_bot_message(from_user: str, content: str, bot: config.WeComBotSpec | None = None) -> str:
    """长连接消息处理（一次性 / DRY-RUN 预览路径）：双维路由 → 承载员工回答 → 返回文本。

    由 ``WeComBotClient`` 在线程池中调用；自建并关闭 DB Session。
    ``bot is None`` 时回退旧语义（按发起人转接其数字分身）。
    实时流式展示请使用 ``start_execution`` + ``run_execution``。
    """
    if not from_user or not content:
        logger.info("wecom-bot: 忽略空消息 from=%s", from_user)
        return ""

    db = SessionLocal()
    try:
        human, employee = resolve_target(db, bot, from_user)
        result = _answer(db, employee.employee_no, human.employee_no, content)
        return (result.message or "").strip()
    except HTTPException as exc:
        logger.warning("wecom-bot: 路由失败 from=%s err=%s", from_user, exc.detail)
        return f"抱歉，暂时无法处理：{exc.detail}"
    except LLMUnavailableError:
        logger.warning("wecom-bot: LLM 不可用 from=%s", from_user)
        return "抱歉，模型服务暂时不可用，请稍后再试。"
    finally:
        db.close()


@dataclass
class BotExecutionContext:
    """企微实时执行上下文：桥接执行事件流与 agent 执行。"""

    execution_id: str
    trace_id: str
    employee_id: str
    requester_human_no: str
    employee_name: str


def start_execution(from_user: str, content: str, bot: config.WeComBotSpec | None = None) -> BotExecutionContext:
    """创建企微执行记录（双维路由 + create_execution），返回执行上下文。

    由 ``WeComBotClient`` 在线程池中调用；失败抛 ``HTTPException``（路由/绑定问题），
    由调用方决定如何回复。此阶段不执行 agent，仅建立 durable execution。
    """
    if not from_user or not content:
        raise HTTPException(status_code=400, detail="消息为空")

    db = SessionLocal()
    try:
        human, employee = resolve_target(db, bot, from_user)
        # 每条企微消息一个独立会话空间，避免与 workplace 会话/并发执行互相干扰。
        # 会话键加入 bot alias 维度，避免同一用户在不同机器人之间的审计/上下文串扰。
        alias = bot.alias if bot is not None else "twin"
        conversation_id = f"WECOM-{alias}-{human.employee_no}-{uuid4().hex[:8].upper()}"
        execution = execution_events.create_execution(
            db,
            conversation_id=conversation_id,
            trigger_message_seq=1,
            primary_employee_id=employee.employee_no,
        )
        return BotExecutionContext(
            execution_id=execution.id,
            trace_id=execution.trace_id,
            employee_id=employee.employee_no,
            requester_human_no=human.employee_no,
            employee_name=employee.name,
        )
    finally:
        db.close()


def run_execution(ctx: BotExecutionContext, content: str) -> str:
    """执行 agent 并写入执行事件，返回最终回答文本。

    由 ``WeComBotClient`` 在线程池中调用；自建并关闭 DB Session。
    异常在此兜底（不向上抛），并通过 ``execution_events.fail`` 记录失败。
    """
    db = SessionLocal()
    try:
        execution = db.get(models.AgentExecution, ctx.execution_id)
        if execution is None or execution.status == "cancelled":
            return ""
        execution_events.emit(
            db, ctx.execution_id, event_type="agent_started", stage="agent_started",
            status="running", actor_employee_id=ctx.employee_id,
            title=f"{ctx.employee_name} 已受理任务", detail="正在分析问题并规划执行步骤",
        )
        result = _answer(db, ctx.employee_id, ctx.requester_human_no, content, ctx.trace_id)
        answer = (result.message or "").strip()
        execution_events.complete(
            db, ctx.execution_id, actor_employee_id=ctx.employee_id,
            message_id=None, trace_id=ctx.trace_id, tool_cards=result.tool_cards,
        )
        return answer
    except HTTPException as exc:
        logger.warning("wecom-bot: 执行中断 execution_id=%s status=%s detail=%s",
                       ctx.execution_id, exc.status_code, exc.detail)
        try:
            execution_events.fail(db, ctx.execution_id, exc)
        except Exception:  # noqa: BLE001 - 补救写入失败不应掩盖原始异常
            logger.exception("wecom-bot: 记录失败状态异常 execution_id=%s", ctx.execution_id)
        detail = exc.detail
        if isinstance(detail, dict):
            detail = detail.get("message", "处理失败")
        return f"抱歉，暂时无法处理：{detail}"
    except LLMUnavailableError:
        logger.warning("wecom-bot: LLM 不可用 execution_id=%s", ctx.execution_id)
        try:
            execution_events.fail(db, ctx.execution_id, LLMUnavailableError())
        except Exception:  # noqa: BLE001 - 补救写入失败不应掩盖原始异常
            logger.exception("wecom-bot: 记录失败状态异常 execution_id=%s", ctx.execution_id)
        return "抱歉，模型服务暂时不可用，请稍后再试。"
    except Exception as exc:  # noqa: BLE001
        # 记录真实异常，避免"抱歉"掩盖故障根因（如 SQLite database is locked）。
        logger.exception("wecom-bot: 执行失败 execution_id=%s", ctx.execution_id)
        try:
            execution_events.fail(db, ctx.execution_id, exc)
        except Exception:  # noqa: BLE001 - 补救写入失败不应掩盖原始异常
            logger.exception("wecom-bot: 记录失败状态异常 execution_id=%s", ctx.execution_id)
        return "抱歉，处理过程中出现问题，请稍后再试。"
    finally:
        db.close()
