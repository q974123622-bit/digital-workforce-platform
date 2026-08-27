import os

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dwp.db")

_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {"check_same_thread": False}
    # 内存库：后台任务与请求/测试共享同一连接（否则各线程各自独立空库）
    if DATABASE_URL == "sqlite://":
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_engine_kwargs)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, expire_on_commit=False, bind=engine)


class Base(DeclarativeBase):
    pass


def ensure_schema_compatibility() -> None:
    """PoC 轻量兼容迁移。

    create_all 不会给旧 SQLite 表补列；在引入正式迁移工具前，只处理已知的
    向后兼容新增列，避免 -NoReset 或直接 uvicorn 启动时报错。
    """
    if not DATABASE_URL.startswith("sqlite"):
        return
    inspector = inspect(engine)
    plugin_columns = {column["name"] for column in inspector.get_columns("plugin")}
    if plugin_columns and "runtime_meta" not in plugin_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE plugin ADD COLUMN runtime_meta JSON NOT NULL DEFAULT '{}'")
            )
    grant_columns = {column["name"] for column in inspector.get_columns("employee_plugin_grant")}
    if grant_columns and "grant_source" not in grant_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE employee_plugin_grant ADD COLUMN grant_source VARCHAR NOT NULL DEFAULT ''")
            )
    table_names = set(inspector.get_table_names())
    if "memory_entry" not in table_names:
        return
    memory_columns = {column["name"] for column in inspector.get_columns("memory_entry")}
    with engine.begin() as connection:
        if "source_type" not in memory_columns:
            connection.execute(
                text("ALTER TABLE memory_entry ADD COLUMN source_type VARCHAR NOT NULL DEFAULT 'manual'")
            )
        if "source_session_id" not in memory_columns:
            connection.execute(
                text("ALTER TABLE memory_entry ADD COLUMN source_session_id VARCHAR")
            )
        if "source_ref" not in memory_columns:
            connection.execute(
                text("ALTER TABLE memory_entry ADD COLUMN source_ref VARCHAR")
            )
        connection.execute(
            text(
                "CREATE INDEX IF NOT EXISTS ix_memory_entry_source_session_id "
                "ON memory_entry (source_session_id)"
            )
        )
        connection.execute(
            text(
                "CREATE UNIQUE INDEX IF NOT EXISTS ix_memory_entry_source_ref "
                "ON memory_entry (source_ref)"
            )
        )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
