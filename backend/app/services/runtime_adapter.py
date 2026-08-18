"""RuntimeAdapter（Sprint 5）：harness 后端（DeepSeek Harness headless）+ demo 降级。

- harness 模式：调 `npx @deepseek-ai/dsh --profile headless <task>`，真实执行一轮回答。
- demo 模式：不调用外部运行时，返回空结果，由上层走 Mock Gateway 链路。
- Key 只从环境变量读取（DEEPSEEK_API_KEY），不出现在命令行/日志。
"""

import os
import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RuntimeResult:
    mode: str  # harness | demo
    ok: bool
    result: str = ""


class RuntimeAdapter:
    """统一 Runtime 接口：run(subject, task, context) -> RuntimeResult。"""

    def run(self, *, employee_id: str, task_prompt: str, trace_id: str) -> RuntimeResult:
        raise NotImplementedError


class NoopRuntimeAdapter(RuntimeAdapter):
    """演示模式：不调用外部运行时（测试/降级用）。"""

    def run(self, *, employee_id: str, task_prompt: str, trace_id: str) -> RuntimeResult:
        return RuntimeResult(mode="demo", ok=False)


class HarnessRuntimeAdapter(RuntimeAdapter):
    """DeepSeek Harness headless 后端；失败自动返回 demo 结果（由上层决定降级）。

    默认演示模式：仅当显式设置 DWP_HARNESS_ENABLED=1 才尝试真实 Harness
    （门禁 G2 未通过，见 PLANS S5-02；避免 npx dsh 阻塞服务线程）。
    执行输出重定向到临时文件 + 硬超时：Windows 下子进程继承 stdout/stderr
    管道会导致 subprocess.run 永久挂起，从而卡死整个请求线程。
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def run(self, *, employee_id: str, task_prompt: str, trace_id: str) -> RuntimeResult:
        if config.get("DWP_HARNESS_ENABLED") != "1":
            return RuntimeResult(mode="demo", ok=False, result="DWP_HARNESS_ENABLED 未开启，使用演示模式")
        if not config.get("DEEPSEEK_API_KEY"):
            return RuntimeResult(mode="demo", ok=False, result="DEEPSEEK_API_KEY 未配置")
        dsh_bin = shutil.which("dsh") or shutil.which("dsh.cmd")
        if dsh_bin:
            runner = f'"{dsh_bin}" --profile headless'
        else:
            npx = shutil.which("npx") or shutil.which("npx.cmd")
            if not npx:
                return RuntimeResult(mode="demo", ok=False, result="dsh/npx 不可用")
            runner = f'"{npx}" --yes @deepseek-ai/dsh --profile headless'
        env = os.environ.copy()
        # headless 无交互：默认 approval=ask 会等待输入导致卡死；
        # danger-full-access 使 approval=never，仅用于虚构演示任务
        env["DSH_PERMISSION_MODE"] = "danger-full-access"
        # 输出写临时文件而非管道，避免孙子进程持有管道句柄导致等待永不返回
        out_fd, out_path = tempfile.mkstemp(prefix="dsh-out-", suffix=".log")
        err_fd, err_path = tempfile.mkstemp(prefix="dsh-err-", suffix=".log")
        try:
            with os.fdopen(out_fd, "wb") as fout, os.fdopen(err_fd, "wb") as ferr:
                try:
                    proc = subprocess.run(
                        f"{runner} {json.dumps(task_prompt)}",
                        shell=True,
                        stdin=subprocess.DEVNULL,
                        stdout=fout,
                        stderr=ferr,
                        timeout=self.timeout,
                        env=env,
                        cwd=str(REPO_ROOT),
                        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                    )
                except (subprocess.TimeoutExpired, OSError) as exc:
                    return RuntimeResult(mode="demo", ok=False, result=f"Harness 不可用：{exc.__class__.__name__}")
            output = Path(out_path).read_text(encoding="utf-8", errors="replace").strip()
            if proc.returncode != 0:
                err = Path(err_path).read_text(encoding="utf-8", errors="replace").strip()
                return RuntimeResult(mode="demo", ok=False, result=(err or output or "Harness 退出非零")[:200])
            if not output:
                return RuntimeResult(mode="demo", ok=False)
            return RuntimeResult(mode="harness", ok=True, result=output)
        finally:
            for p in (out_path, err_path):
                try:
                    os.remove(p)
                except OSError:
                    pass
