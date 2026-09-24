"""RuntimeAdapter：Harness 后端与显式本地 Mock 模式。

- harness 模式：调 `npx @deepseek-ai/dsh --profile headless <task>`，真实执行一轮回答。
- demo 模式：仅在明确关闭 Harness 时使用；Harness 失败不会自动降级。
- Key 只从环境变量读取（DEEPSEEK_API_KEY），不出现在命令行/日志。
"""

import os
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from . import config, runtime_manager

REPO_ROOT = Path(__file__).resolve().parents[3]


@dataclass(frozen=True)
class RuntimeResult:
    mode: str  # harness | demo
    ok: bool
    result: str = ""


@dataclass(frozen=True)
class HarnessExecutionContext:
    """一次AI员工 Harness 执行的完整、可审计上下文。"""

    task_id: str
    employee_id: str
    employee_name: str
    role_prompt: str
    responsibility: str
    request: str
    subtask: str
    collaboration_summary: str
    capability_id: str
    capability_name: str

    @property
    def context_id(self) -> str:
        return f"{self.employee_id}:{self.task_id}"


class RuntimeAdapter:
    """统一 Runtime 接口：run(subject, task, context) -> RuntimeResult。"""

    def run(
        self,
        *,
        employee_id: str,
        task_prompt: str,
        trace_id: str,
        context: HarnessExecutionContext | None = None,
    ) -> RuntimeResult:
        raise NotImplementedError


class NoopRuntimeAdapter(RuntimeAdapter):
    """演示模式：不调用外部运行时（测试/降级用）。"""

    def run(
        self,
        *,
        employee_id: str,
        task_prompt: str,
        trace_id: str,
        context: HarnessExecutionContext | None = None,
    ) -> RuntimeResult:
        return RuntimeResult(mode="demo", ok=False)


class HarnessRuntimeAdapter(RuntimeAdapter):
    """DeepSeek Harness headless 后端；失败返回错误，由上层明确终止执行。

    默认演示模式：仅当显式设置 DWP_HARNESS_ENABLED=1 才尝试真实 Harness
    （门禁 G2 未通过，见 PLANS S5-02；避免 npx dsh 阻塞服务线程）。
    执行输出重定向到临时文件 + 硬超时：Windows 下子进程继承 stdout/stderr
    管道会导致 subprocess.run 永久挂起，从而卡死整个请求线程。
    """

    def __init__(self, timeout: int = 120):
        self.timeout = timeout

    def run(
        self,
        *,
        employee_id: str,
        task_prompt: str,
        trace_id: str,
        context: HarnessExecutionContext | None = None,
    ) -> RuntimeResult:
        if config.get("DWP_HARNESS_ENABLED") != "1":
            return RuntimeResult(mode="demo", ok=False, result="DWP_HARNESS_ENABLED 未开启，使用演示模式")
        if not config.get("DEEPSEEK_API_KEY"):
            return RuntimeResult(mode="demo", ok=False, result="DEEPSEEK_API_KEY 未配置")
        dsh_bin = shutil.which("dsh") or shutil.which("dsh.cmd")
        if dsh_bin:
            runner = [dsh_bin, "--profile", "headless"]
        else:
            npx = shutil.which("npx") or shutil.which("npx.cmd")
            if not npx:
                return RuntimeResult(mode="demo", ok=False, result="dsh/npx 不可用")
            runner = [npx, "--yes", "@deepseek-ai/dsh", "--profile", "headless"]
        env = os.environ.copy()
        safe_employee = "".join(ch.lower() if ch.isalnum() else "-" for ch in employee_id).strip("-")
        context_root = REPO_ROOT / "backend" / "harness-workspaces" / safe_employee
        dsh_home = context_root / "dsh-home"
        workspace = context_root / "workspace"
        dsh_home.mkdir(parents=True, exist_ok=True)
        workspace.mkdir(parents=True, exist_ok=True)
        env["DSH_HOME"] = str(dsh_home)
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
                        [*runner, task_prompt],
                        shell=False,
                        stdin=subprocess.DEVNULL,
                        stdout=fout,
                        stderr=ferr,
                        timeout=self.timeout,
                        env=env,
                        cwd=str(workspace),
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


class DockerHarnessRuntimeAdapter(RuntimeAdapter):
    """Execute inside the employee's stable Harness container.

    镜像（L0 底座）与 dsh 版本（L1 框架层）都由容器自身的挂载决定：
    底座镜像来自 DWP_HARNESS_IMAGE，框架版本来自容器 /opt/dsh-runtime 挂载，
    因此这里只需保证镜像名与 compose 一致即可（见 deploy/offline/versions.env）。
    """

    def __init__(self, image: str | None = None, timeout: int = 120):
        self.image = image or os.getenv("DWP_HARNESS_IMAGE") or "dwp-dsh:rc6"
        self.timeout = timeout

    def run(
        self,
        *,
        employee_id: str,
        task_prompt: str,
        trace_id: str,
        context: HarnessExecutionContext | None = None,
        tool_token: str = "",
        tool_base_url: str = "",
    ) -> RuntimeResult:
        if not config.get("DEEPSEEK_API_KEY"):
            return RuntimeResult(mode="demo", ok=False, result="DEEPSEEK_API_KEY 未配置")
        try:
            name = runtime_manager.ensure_container(employee_id)
            proc = runtime_manager.exec_in_container(
                name,
                ["dsh", "--profile", "dwp-knowledge-agent-v2", task_prompt],
                environment={
                    "DEEPSEEK_API_KEY": config.get("DEEPSEEK_API_KEY") or "",
                    "DEEPSEEK_BASE_URL": config.get("DEEPSEEK_BASE_URL") or "",
                    "DEEPSEEK_MODEL": config.get("DEEPSEEK_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash",
                    "DWP_AGENT_TOOL_TOKEN": tool_token,
                    "DWP_PLATFORM_TOOL_URL": f"{tool_base_url.rstrip('/')}/internal/agent-tools/mcp",
                },
                workdir="/workspace",
                timeout=self.timeout,
            )
        except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
            return RuntimeResult(mode="harness", ok=False, result=f"Harness 容器不可用：{exc.__class__.__name__}")

        if proc.returncode != 0:
            detail = (proc.stderr or proc.stdout or "").strip()[:500]
            if not detail:
                # 进程被信号杀死 / OOM / 容器内异常退出时可能既无 stdout 也无 stderr；
                # 保留退出码，避免上层只看到一句无信息量的「Harness 执行失败」。
                detail = f"Harness 退出码 {proc.returncode} 且无输出（进程可能被终止或异常退出）"
            return RuntimeResult(mode="harness", ok=False, result=detail)
        output = proc.stdout.strip()
        if not output:
            return RuntimeResult(mode="harness", ok=False, result="Harness 未返回内容")
        return RuntimeResult(mode="harness", ok=True, result=output)
