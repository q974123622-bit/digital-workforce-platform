"""RuntimeAdapter（Sprint 5）：harness 后端（DeepSeek Harness headless）+ demo 降级。

- harness 模式：调 `npx @deepseek-ai/dsh --profile headless <task>`，真实执行一轮回答。
- demo 模式：不调用外部运行时，返回空结果，由上层走 Mock Gateway 链路。
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
    """一次数字员工 Harness 执行的完整、可审计上下文。"""

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
    """DeepSeek Harness headless 后端；失败自动返回 demo 结果（由上层决定降级）。

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
    """Execute inside the employee's stable Harness container."""

    def __init__(self, image: str = "dwp-dsh:rc6", timeout: int = 120):
        self.image = image
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
        docker_bin = shutil.which("docker")
        if not config.get("DEEPSEEK_API_KEY"):
            return RuntimeResult(mode="demo", ok=False, result="DEEPSEEK_API_KEY 未配置")
        if not docker_bin:
            return RuntimeResult(mode="demo", ok=False, result="docker 不可用")
        env_path = None
        try:
            name = runtime_manager.ensure_container(employee_id)
            with tempfile.NamedTemporaryFile("w", suffix=".env", delete=False, encoding="utf-8") as env_file:
                for key, value in (
                    ("DEEPSEEK_API_KEY", config.get("DEEPSEEK_API_KEY") or ""),
                    ("DEEPSEEK_BASE_URL", config.get("DEEPSEEK_BASE_URL") or ""),
                    ("DEEPSEEK_MODEL", config.get("DEEPSEEK_MODEL", "deepseek-v4-flash") or "deepseek-v4-flash"),
                    ("DWP_AGENT_TOOL_TOKEN", tool_token),
                    ("DWP_PLATFORM_TOOL_URL", f"{tool_base_url.rstrip('/')}/internal/agent-tools/mcp"),
                ):
                    env_file.write(f"{key}={value.replace(chr(10), '').replace(chr(13), '')}\n")
                env_path = env_file.name
            proc = subprocess.run(
                [
                    docker_bin, "exec", "--workdir", "/workspace",
                    "--env-file", env_path,
                    name, "dsh", "--profile", "dwp-knowledge-agent-v2", task_prompt,
                ],
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self.timeout,
                stdin=subprocess.DEVNULL,
            )
        except (subprocess.TimeoutExpired, OSError, RuntimeError) as exc:
            return RuntimeResult(mode="harness", ok=False, result=f"Harness 容器不可用：{exc.__class__.__name__}")
        finally:
            if env_path:
                try:
                    os.remove(env_path)
                except OSError:
                    pass

        if proc.returncode != 0:
            return RuntimeResult(mode="harness", ok=False, result=(proc.stderr or proc.stdout or "").strip()[:500])
        output = proc.stdout.strip()
        if not output:
            return RuntimeResult(mode="harness", ok=False, result="Harness 未返回内容")
        return RuntimeResult(mode="harness", ok=True, result=output)
