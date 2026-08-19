"""TeamTaskOrchestrator（Sprint 5，P0-lite）。

模板拆解（确定性，演示稳定）+ LLM 汇总（失败自动降级模板拼接）。
子任务执行一律经 Plugin Gateway（Policy + 审计自动生效），绝不绕过。
状态机：parsing -> running -> approval <-> running -> completed / denied / failed
"""

import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models, schemas
from .gateway import write_audit
from .llm import LLMProvider, LLMUnavailableError
from .runtime_adapter import NoopRuntimeAdapter, RuntimeAdapter
from .subtask_executor import GatewaySubtaskExecutor, SubtaskExecutor

# TEAM-ONBOARD 预置模板：HR 确认制度 -> IT 开通账号 -> 敏感报表（触发审批）
ONBOARD_TEMPLATE = [
    {
        "worker_id": "VE-0002",
        "worker_no": "VE-0002",
        "summary": "确认入职制度与材料",
        "plugin_ids": ["hr-employee-mcp"],
        "action": "execute",
        "params": {"employee_name": "王小明"},
    },
    {
        "worker_id": "VE-0003",
        "worker_no": "VE-0003",
        "summary": "开通办公账号与 IT 流程",
        "plugin_ids": ["adp-onboarding"],
        "action": "execute",
        "params": {"employee_name": "王小明"},
    },
    {
        "worker_id": "VE-0003",
        "worker_no": "VE-0003",
        "summary": "确认员工权限归属（敏感操作，需审批）",
        "plugin_ids": ["hr-employee-mcp"],
        "action": "execute",
        "params": {},
    },
]

TEMPLATES = {"TEAM-ONBOARD": ONBOARD_TEMPLATE}

_STATUS_CN = {
    "submitted": "已提交",
    "started": "已启动",
    "pending_approval": "待审批",
    "generated": "已生成",
    "ready": "已就绪",
    "completed": "已完成",
}


def _format_gateway_result(data, plugin_name: str = "") -> str:
    """把 Gateway 返回的 Mock 结果格式化为可读文本，不再展示原始 JSON。"""
    if not isinstance(data, dict):
        return str(data)[:500]
    parts: list[str] = []
    if plugin_name:
        parts.append(f"流程：{plugin_name}")
    elif data.get("workflow"):
        parts.append(f"流程：{data['workflow']}")
    if data.get("report"):
        parts.append(f"报告：{data['report']}")
    if data.get("employee_name"):
        parts.append(f"员工：{data['employee_name']}")
    if data.get("employee_no"):
        parts.append(f"工号：{data['employee_no']}")
    if data.get("name"):
        parts.append(f"姓名：{data['name']}")
    if data.get("department"):
        parts.append(f"部门：{data['department']}")
    if data.get("employment_type"):
        parts.append(f"用工类型：{'正式' if data['employment_type'] == 'formal' else data['employment_type']}")
    if data.get("amount") is not None:
        parts.append(f"金额：{data['amount']} 元")
    if data.get("period"):
        parts.append(f"周期：{data['period']}")
    if data.get("status"):
        parts.append(f"状态：{_STATUS_CN.get(data['status'], data['status'])}")
    if data.get("steps"):
        parts.append("步骤：" + " → ".join(data["steps"]))
    if data.get("rows") is not None:
        parts.append(f"数据行数：{data['rows']}")
    if data.get("note"):
        parts.append(f"说明：{data['note']}")
    if not parts:
        parts.append(json.dumps(data, ensure_ascii=False))
    return "；".join(parts)[:500]

# 请求关键词 → 插件（演示稳定性兜底：LLM 拆解失效时按语义指派）
PLUGIN_KEYWORDS = [
    ("expense-claim", ["报销", "差旅", "打款"]),
    ("leave-request", ["请假", "年假", "调休"]),
    ("meeting-notes", ["会议纪要", "纪要", "会议记录"]),
    ("weekly-report", ["周报", "月报", "费用周报"]),
    ("purchase-request", ["采购", "下单", "比价"]),
    ("adp-onboarding", ["入职", "账号", "工牌"]),
    ("rpa-report", ["报表", "权限报表"]),
]

FALLBACK_SUMMARY = {
    "adp-onboarding": "整理入职材料清单并开通办公账号（企业微信/邮箱/VPN）",
    "expense-claim": "提交差旅报销申请并跟进审批打款",
    "leave-request": "提交请假申请并同步考勤",
    "meeting-notes": "整理会议纪要（结论/决议/行动项/遗留问题四段式）",
    "weekly-report": "生成本周/本月费用周报",
    "purchase-request": "发起采购申请并完成比价下单",
    "rpa-report": "生成权限报表（敏感）",
}


def _fallback_summary(run: models.TaskRun) -> str:
    done = [s["summary"] for s in run.subtasks if s.get("status") == "completed"]
    return "协作完成：" + "；".join(done) + "。"


class TeamTaskOrchestrator:
    def __init__(
        self,
        provider: LLMProvider,
        runtime: RuntimeAdapter | None = None,
        executor: SubtaskExecutor | None = None,
    ):
        self.provider = provider
        self.runtime = runtime or NoopRuntimeAdapter()
        self.executor = executor or GatewaySubtaskExecutor()

    # ---------- 创建与执行 ----------

    def create_task(self, db: Session, *, team_id: str, request: str) -> schemas.TaskRunOut:
        team = db.get(models.Team, team_id)
        if team is None:
            raise HTTPException(status_code=404, detail="团队不存在")
        template = TEMPLATES.get(team_id)
        if template is None:
            raise HTTPException(status_code=400, detail="该团队暂无任务模板")

        task_id = f"T-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
        subtasks = [
            {
                "worker_id": t["worker_id"],
                "worker_no": t["worker_no"],
                "summary": t["summary"],
                "plugin_ids": list(t["plugin_ids"]),
                "status": "pending",
                "result": None,
                "approval": None,
            }
            for t in template
        ]
        run = models.TaskRun(
            id=task_id,
            team_id=team_id,
            trace_id=task_id,
            request=request,
            status="running",
            subtasks=subtasks,
            summary="",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        write_audit(
            db,
            trace_id=run.trace_id,
            employee_id=team.leader_employee_id,
            plugin_id="team:task",
            action="create",
            decision="allow",
            reason="任务创建",
            result_summary=request[:200],
        )
        return self._run_loop(db, run)

    def create_conversation_task(
        self,
        db: Session,
        *,
        conversation: models.Conversation,
        actor_no: str,
        request: str,
        trigger_seq: int | None = None,
    ) -> schemas.TaskRunOut:
        """职场协作群聊任务（Sprint 7 C 档）：分身拆解 → 指派成员执行 → 审批 → Leader 汇总。"""
        if conversation.kind != "group":
            raise HTTPException(status_code=400, detail="只有协作群聊可以发起任务")

        leader = None
        members: list[models.DigitalEmployee] = []
        for participant in conversation.participants or []:
            emp = db.get(models.DigitalEmployee, participant["employee_no"])
            if emp is None:
                continue
            if emp.type == "twin":
                leader = emp
            elif emp.type in ("virtual", "rpa"):
                members.append(emp)
        if leader is None or not members:
            raise HTTPException(status_code=400, detail="协作空间缺少组织者（我的分身）或可执行成员")

        subtasks = self._decompose(db, conversation, members, request)
        task_id = f"T-{datetime.now():%Y%m%d}-{uuid4().hex[:6].upper()}"
        run = models.TaskRun(
            id=task_id,
            team_id=conversation.id,
            conversation_id=conversation.id,
            trigger_message_seq=trigger_seq,
            trace_id=task_id,
            request=request,
            status="running",
            subtasks=subtasks,
            summary="",
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        write_audit(
            db,
            trace_id=run.trace_id,
            employee_id=leader.employee_no,
            plugin_id="conv:task",
            action="create",
            decision="allow",
            reason="群聊任务创建（分身拆解）",
            result_summary=request[:200],
        )
        return self._run_loop(db, run, leader_employee_id=leader.employee_no)

    def _decompose(
        self,
        db: Session,
        conversation: models.Conversation,
        members: list[models.DigitalEmployee],
        request: str,
    ) -> list[dict]:
        """组织者（分身）把请求拆成 1-3 个子任务并指派成员；LLM 失败时降级为单子任务。"""
        plugins = db.scalars(select(models.Plugin)).all()
        plugin_names = {p.id: f"{p.name}({p.type}, L{p.data_level})" for p in plugins}
        plugins_by_id = {p.id: p for p in plugins}
        executable: dict[str, list[str]] = {}
        for member in members:
            grants = db.scalars(
                select(models.EmployeePluginGrant).where(
                    models.EmployeePluginGrant.employee_id == member.employee_no
                )
            ).all()
            ids = [g.plugin_id for g in grants if g.decision_mode in ("allow", "approval")]
            executable[member.employee_no] = [pid for pid in ids if pid in plugin_names]

        roster = "\n".join(
            f"- {m.employee_no} {m.name}（{m.department}），可执行插件："
            f"{'、'.join(plugin_names.get(pid, pid) for pid in executable.get(m.employee_no, [])) or '无'}"
            for m in members
        )
        suggested = [pid for pid, keywords in PLUGIN_KEYWORDS if any(k in request for k in keywords)]
        prompt = [
            {
                "role": "system",
                "content": (
                    "你是数字分身（组织者）。请把用户的请求拆解成 1-3 个子任务并指派给成员执行，"
                    '只输出 JSON：{"subtasks":[{"worker_id":"成员工号","summary":"子任务说明","plugin_ids":["插件ID"]}]}。'
                    "规则：worker_id 必须来自成员列表；plugin_ids 只从该成员的可执行插件中选 1 个；"
                    "summary 用一句话描述要完成的事，不要编造不存在的成员或插件。"
                    "插件选择参考：报销/打款→expense-claim；请假→leave-request；会议纪要→meeting-notes；"
                    "周报/报告→weekly-report；入职/账号开通→adp-onboarding；采购/下单→purchase-request；报表→rpa-report。"
                    + (f"建议优先使用插件：{'、'.join(suggested)}。" if suggested else "")
                ),
                "source": "demo",
            },
            {
                "role": "user",
                "content": f"用户请求：{request}\n成员与可执行插件：\n{roster}",
                "source": "demo",
            },
        ]
        try:
            raw = self.provider.structured_output(prompt, {"type": "object"})
            subtasks: list[dict] = []
            for item in (raw.get("subtasks") or [])[:3]:
                worker_id = self._resolve_worker(item.get("worker_id"), executable, members)
                summary = str(item.get("summary", "")).strip()
                plugin_ids = item.get("plugin_ids") or []
                if worker_id is None or not summary:
                    continue
                resolved = self._resolve_plugin(plugin_ids, executable[worker_id], plugins_by_id)
                if resolved is None:
                    # 插件无法解析到该成员的可执行插件则跳过（避免「报销」跑去执行入职流程）
                    continue
                subtasks.append(
                    {
                        "worker_id": worker_id,
                        "worker_no": worker_id,
                        "summary": summary,
                        "plugin_ids": [resolved],
                        "status": "pending",
                        "result": None,
                        "approval": None,
                    }
                )
            subtasks = self._merge_suggested(
                subtasks,
                suggested=suggested,
                executable=executable,
                members=members,
                plugin_names=plugin_names,
            )
            if subtasks:
                return subtasks
        except (LLMUnavailableError, Exception):
            pass

        # 兜底：按请求关键词为命中的插件指派拥有该插件的成员
        subtasks = self._merge_suggested(
            subtasks,
            suggested=suggested,
            executable=executable,
            members=members,
            plugin_names=plugin_names,
        )
        if subtasks:
            return subtasks

        # 兜底 2：第一个有可执行插件的成员执行一个子任务
        for member in members:
            plugins = executable.get(member.employee_no) or []
            if plugins:
                return [
                    {
                        "worker_id": member.employee_no,
                        "worker_no": member.employee_no,
                        "summary": f"处理：{request[:50]}",
                        "plugin_ids": plugins[:1],
                        "status": "pending",
                        "result": None,
                        "approval": None,
                    }
                ]
        raise HTTPException(status_code=400, detail="协作空间成员均无可执行插件")

    @staticmethod
    def _merge_suggested(
        subtasks: list[dict],
        *,
        suggested: list[str],
        executable: dict[str, list[str]],
        members: list[models.DigitalEmployee],
        plugin_names: dict[str, str],
    ) -> list[dict]:
        """把关键词命中的插件补进子任务（LLM 漏掉的语义插件，指派给拥有它的成员）。"""
        covered = {sub["plugin_ids"][0] for sub in subtasks if sub.get("plugin_ids")}
        for plugin_id in suggested:
            if plugin_id in covered or len(subtasks) >= 3:
                continue
            for member in members:
                if plugin_id in executable.get(member.employee_no, []):
                    subtasks.append(
                        {
                            "worker_id": member.employee_no,
                            "worker_no": member.employee_no,
                            "summary": FALLBACK_SUMMARY.get(plugin_id, f"执行：{plugin_names.get(plugin_id, plugin_id)}"),
                            "plugin_ids": [plugin_id],
                            "status": "pending",
                            "result": None,
                            "approval": None,
                        }
                    )
                    break
        return subtasks

    @staticmethod
    def _resolve_plugin(
        plugin_ids,
        executable_plugins: list[str],
        plugins_by_id: dict[str, models.Plugin],
    ) -> str | None:
        """把模型返回的插件（ID / 名称 / 类型别名）归一化为该成员的可执行插件 ID。"""
        type_alias = {
            "workflow": "workflow",
            "rpa": "rpa",
            "mcp": "mcp",
            "http": "http",
            "knowledge": "knowledge",
            "knowledgebase": "knowledge",
        }
        for raw in plugin_ids or []:
            text = str(raw or "").strip()
            if text in executable_plugins:
                return text
            # 插件显示名（如「入职流程 Workflow」）→ ID
            for pid in executable_plugins:
                label = f"{plugins_by_id[pid].name}({plugins_by_id[pid].type}, L{plugins_by_id[pid].data_level})"
                if text and (text in label or label in text):
                    return pid
            # 类型别名（如 workflow）→ 该成员第一个同类型插件
            target_type = type_alias.get(text.lower())
            if target_type:
                for pid in executable_plugins:
                    if plugins_by_id[pid].type == target_type:
                        return pid
        return None

    @staticmethod
    def _resolve_worker(
        worker_id,
        executable: dict[str, list[str]],
        members: list[models.DigitalEmployee],
    ) -> str | None:
        """把模型返回的成员（工号/名字）归一化为成员 employee_no。"""
        text = str(worker_id or "").strip()
        if text in executable:
            return text
        for member in members:
            if member.name and (member.name == text or member.name in text or text in member.name):
                return member.employee_no
        return None

    def _run_loop(
        self,
        db: Session,
        run: models.TaskRun,
        leader_employee_id: str | None = None,
    ) -> schemas.TaskRunOut:
        while run.status == "running":
            sub = next((s for s in run.subtasks if s.get("status") == "pending"), None)
            if sub is None:
                break
            sub["status"] = "running"
            self._save(db, run)
            try:
                result = self.executor.execute(
                    db,
                    run=run,
                    subtask=sub,
                    trace_id=run.trace_id,
                )
            except HTTPException as exc:
                if exc.status_code == 403:
                    detail = exc.detail if isinstance(exc.detail, dict) else {}
                    sub["status"] = "denied"
                    sub["result"] = detail.get("reason")
                    run.status = "denied"
                    self._save(db, run)
                    return self._to_out(run)
                sub["status"] = "failed"
                sub["result"] = "子任务执行失败"
                run.status = "failed"
                self._save(db, run)
                return self._to_out(run)

            decision = result.get("decision")
            if decision == "approval":
                sub["status"] = "approval"
                sub["approval"] = {
                    "policy_id": result.get("policy_id"),
                    "reason": "敏感操作需人工审批",
                }
                run.status = "approval"
                self._save(db, run)
                return self._to_out(run)
            if decision == "deny":
                sub["status"] = "denied"
                run.status = "denied"
                self._save(db, run)
                return self._to_out(run)

            sub["status"] = "completed"
            plugin = db.get(models.Plugin, sub["plugin_ids"][0]) if sub.get("plugin_ids") else None
            gateway_summary = _format_gateway_result(result.get("data"), plugin.name if plugin else "")
            runtime_res = self.runtime.run(
                employee_id=sub["worker_id"],
                task_prompt=f"{run.request}：{sub['summary']}",
                trace_id=run.trace_id,
            )
            if runtime_res.ok:
                sub["result"] = f"[Harness 执行] {runtime_res.result[:400]}\n[Gateway] {gateway_summary}"
            else:
                sub["result"] = gateway_summary
            self._save(db, run)

        return self._finish(db, run, leader_employee_id=leader_employee_id)

    # ---------- 审批 ----------

    def approve(self, db: Session, *, task_id: str, approve: bool, actor_no: str) -> schemas.TaskRunOut:
        run = db.get(models.TaskRun, task_id)
        if run is None:
            raise HTTPException(status_code=404, detail="任务不存在")
        if run.status != "approval":
            raise HTTPException(status_code=409, detail="任务当前不处于审批挂起状态")

        write_audit(
            db,
            trace_id=run.trace_id,
            employee_id=actor_no,
            plugin_id="team:approval",
            action="approve" if approve else "reject",
            decision="allow" if approve else "deny",
            reason="审批通过" if approve else "审批拒绝",
        )
        if not approve:
            run.status = "denied"
            for sub in run.subtasks:
                if sub.get("status") == "approval":
                    sub["status"] = "denied"
            self._save(db, run)
            return self._to_out(run)

        run.status = "running"
        for sub in run.subtasks:
            if sub.get("status") == "approval":
                sub["status"] = "completed"
                sub["result"] = "已批准执行（Mock 结果）"
                sub["approval"] = None
        self._save(db, run)
        return self._run_loop(db, run)

    # ---------- 汇总 ----------

    def _finish(
        self,
        db: Session,
        run: models.TaskRun,
        leader_employee_id: str | None = None,
    ) -> schemas.TaskRunOut:
        run.status = "completed"
        summary = self._llm_summarize(run)
        run.summary = summary or _fallback_summary(run)
        write_audit(
            db,
            trace_id=run.trace_id,
            employee_id=leader_employee_id or run.team_id,
            plugin_id="team:summary",
            action="summarize",
            decision="allow",
            reason="Leader 汇总",
            result_summary=run.summary[:200],
        )
        self._save(db, run)
        return self._to_out(run)

    def _llm_summarize(self, run: models.TaskRun) -> str | None:
        try:
            detail = "\n".join(
                f"- {s['worker_no']} {s['summary']}: {s.get('result') or '（无结果）'}" for s in run.subtasks
            )
            resp = self.provider.chat(
                [
                    {
                        "role": "system",
                        "content": (
                            "你是团队 Leader，请用不超过 80 字汇总以下子任务执行结果。"
                            "演示环境中这些子任务均已执行完成，请用完成态描述（如「已完成」「已就绪」），"
                            "不要使用「待完成」「进行中」等未完成表述；全部为虚构演示数据。"
                        ),
                        "source": "demo",
                    },
                    {"role": "user", "content": f"任务：{run.request}\n{detail}", "source": "demo"},
                ]
            )
            return resp.content.strip() or None
        except (LLMUnavailableError, Exception):
            return None

    # ---------- 工具 ----------

    @staticmethod
    def _save(db: Session, run: models.TaskRun) -> None:
        # 原地修改 JSON 列表，用 flag_modified 显式标记变更；保持引用一致
        flag_modified(run, "subtasks")
        db.commit()

    @staticmethod
    def _to_out(run: models.TaskRun) -> schemas.TaskRunOut:
        return schemas.TaskRunOut(
            id=run.id,
            team_id=run.team_id,
            conversation_id=run.conversation_id,
            trigger_message_seq=run.trigger_message_seq,
            trace_id=run.trace_id,
            request=run.request,
            status=run.status,
            subtasks=[schemas.SubtaskOut(**s) for s in run.subtasks],
            summary=run.summary,
            created_at=run.created_at,
        )
