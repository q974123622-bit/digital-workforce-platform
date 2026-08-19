"""TeamTaskOrchestrator（Sprint 5，P0-lite）。

模板拆解（确定性，演示稳定）+ LLM 汇总（失败自动降级模板拼接）。
子任务执行一律经 Plugin Gateway（Policy + 审计自动生效），绝不绕过。
状态机：parsing -> running -> approval <-> running -> completed / denied / failed
"""

import json
from datetime import datetime
from uuid import uuid4

from fastapi import HTTPException
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from .. import models, schemas
from .gateway import invoke_plugin, write_audit
from .llm import LLMProvider, LLMUnavailableError
from .runtime_adapter import NoopRuntimeAdapter, RuntimeAdapter

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


def _fallback_summary(run: models.TaskRun) -> str:
    done = [s["summary"] for s in run.subtasks if s.get("status") == "completed"]
    return "协作完成：" + "；".join(done) + "。"


class TeamTaskOrchestrator:
    def __init__(self, provider: LLMProvider, runtime: RuntimeAdapter | None = None):
        self.provider = provider
        self.runtime = runtime or NoopRuntimeAdapter()

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

    def _run_loop(self, db: Session, run: models.TaskRun) -> schemas.TaskRunOut:
        while run.status == "running":
            sub = next((s for s in run.subtasks if s.get("status") == "pending"), None)
            if sub is None:
                break
            sub["status"] = "running"
            self._save(db, run)
            plugin_id = sub["plugin_ids"][0] if sub["plugin_ids"] else ""
            try:
                result = invoke_plugin(
                    db,
                    employee_id=sub["worker_id"],
                    plugin_id=plugin_id,
                    action="execute",
                    params=sub.get("params") or {},
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
            gateway_summary = json.dumps(result.get("data"), ensure_ascii=False)[:500]
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

        return self._finish(db, run)

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

    def _finish(self, db: Session, run: models.TaskRun) -> schemas.TaskRunOut:
        run.status = "completed"
        summary = self._llm_summarize(run)
        run.summary = summary or _fallback_summary(run)
        write_audit(
            db,
            trace_id=run.trace_id,
            employee_id=run.team_id,
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
                        "content": "你是团队 Leader，请用不超过 80 字汇总以下子任务执行结果（全部为虚构演示数据）。",
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
            trace_id=run.trace_id,
            request=run.request,
            status=run.status,
            subtasks=[schemas.SubtaskOut(**s) for s in run.subtasks],
            summary=run.summary,
            created_at=run.created_at,
        )
