"""本地聊天记忆服务的接口契约测试。"""


def test_memory_service_contract_is_importable():
    """工作线 B 必须能导入 A 提供的三项公共接口。"""
    from app.services.memory_service import (
        MemoryHit,
        capture_turn,
        render_prompt_context,
        retrieve_for_prompt,
    )

    assert MemoryHit.__name__ == "MemoryHit"
    assert callable(capture_turn)
    assert callable(retrieve_for_prompt)
    assert callable(render_prompt_context)
