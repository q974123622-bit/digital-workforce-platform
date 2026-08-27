"""测试专用：在工作线 A 的 memory_service 合并前，用固定契约模拟三个记忆接口。

仅测试使用，不进入生产导入路径，不访问数据库和网络。
契约以 MEMORY_WORKLINE_B_AI_EXECUTION_PLAN 第 2 节为准：

    capture_turn(...) -> int | None
    retrieve_for_prompt(...) -> list[MemoryHit]
    render_prompt_context(hits, ...) -> str
"""


class MemoryContractStub:
    """按 A 的固定契约模拟记忆读写；可注入读/写失败用于降级测试。"""

    def __init__(self, hits=(), context="", capture_id=9001, fail_read=False, fail_write=False):
        self.hits = list(hits)
        self.context = context
        self.capture_id = capture_id
        self.fail_read = fail_read
        self.fail_write = fail_write
        self.calls = []

    def retrieve_for_prompt(self, **kwargs):
        self.calls.append(("retrieve", kwargs))
        if self.fail_read:
            raise RuntimeError("mock memory read failed")
        return self.hits

    def render_prompt_context(self, hits, **kwargs):
        self.calls.append(("render", {"hits": hits, **kwargs}))
        return self.context if hits else ""

    def capture_turn(self, **kwargs):
        self.calls.append(("capture", kwargs))
        if self.fail_write:
            raise RuntimeError("mock memory write failed")
        return self.capture_id
