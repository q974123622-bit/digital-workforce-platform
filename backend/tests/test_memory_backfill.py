"""本地历史聊天回填为长期记忆的测试。"""

from sqlalchemy import select


def test_backfill_supports_dry_run_and_idempotent_chat_sources(db_session):
    """dry-run 不写库；正式回填覆盖直接聊天与职场会话，重复执行不重复写入。"""
    from app import models
    from scripts.backfill_memories import backfill_memories

    chat_session = models.ChatSession(
        session_id="S-BACKFILL",
        employee_id="VE-0001",
        trace_id="T-BACKFILL",
    )
    conversation = models.Conversation(
        id="CONV-BACKFILL",
        kind="direct",
        owner_human_no="E10281",
    )
    db_session.add_all([chat_session, conversation])
    db_session.flush()
    db_session.add_all(
        [
            models.ChatMessage(
                session_id="S-BACKFILL", role="user", content="张三账号怎么样？"
            ),
            models.ChatMessage(
                session_id="S-BACKFILL", role="assistant", content="IT 账号待开通。"
            ),
            models.ConversationMessage(
                conversation_id="CONV-BACKFILL",
                participant_no="E10281",
                participant_name="用户",
                role="user",
                content="入职材料准备好了吗？",
                seq=1,
            ),
            models.ConversationMessage(
                conversation_id="CONV-BACKFILL",
                participant_no="VE-0001",
                participant_name="HR 助理",
                role="assistant",
                content="HR 材料已完成。",
                seq=2,
            ),
        ]
    )
    db_session.commit()

    dry_stats = backfill_memories(db_session, dry_run=True)
    assert dry_stats.scanned >= 2
    assert dry_stats.created == dry_stats.scanned
    assert db_session.scalars(
        select(models.MemoryEntry).where(
            models.MemoryEntry.source_ref.like("%BACKFILL%")
        )
    ).all() == []

    first_stats = backfill_memories(db_session)
    assert first_stats.scanned == dry_stats.scanned
    assert first_stats.created == dry_stats.created

    second_stats = backfill_memories(db_session)
    assert second_stats.created == 0
    assert second_stats.skipped == first_stats.scanned
