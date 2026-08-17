"""Sandbox Policy 模型与 Mock Executor（Sprint 3）。

最小 Sandbox Policy：runtime_location / internet_access / filesystem_scope。
支持：remote_only、internet_deny、local_deny。

原则：Sandbox 是执行隔离，不是权限定义来源；是否允许执行由 Policy Engine 决定，
本模块只提供策略描述与 Mock 执行，不产生授权决策。
"""

from dataclasses import dataclass

from .identity import EmployeeIdentity


@dataclass(frozen=True)
class SandboxPolicy:
    runtime_location: str  # remote_only | local
    internet_access: str  # deny | allow
    filesystem_scope: str  # /workspace/{employee_id}

    @property
    def local_deny(self) -> bool:
        return self.runtime_location == "remote_only"


def from_identity(identity: EmployeeIdentity) -> SandboxPolicy:
    """从数字员工绑定的环境配置构建 Sandbox Policy（配置来源：digital_employee 内嵌字段）。"""
    return SandboxPolicy(
        runtime_location="remote_only" if identity.location == "remote" else "local",
        internet_access="deny" if identity.internet == "deny" else "allow",
        filesystem_scope=f"/workspace/{identity.employee_id}",
    )


class MockExecutor:
    """Mock 执行器：演示 Sandbox 隔离概念，不做真实容器。"""

    def execute(
        self,
        policy: SandboxPolicy,
        *,
        command: str,
        mount_dir: str,
        network: str,
        execution_location: str,
    ) -> dict:
        logs = [
            f"[sandbox] policy: runtime_location={policy.runtime_location}, internet={policy.internet_access}",
            f"[sandbox] mount: {mount_dir or policy.filesystem_scope}, network={network}",
            f"[sandbox] executor=demo, location={execution_location}, command={command or '(none)'}",
            "[sandbox] status=ok (mock)",
        ]
        return {"mode": "local", "status": "ok", "logs": logs}
