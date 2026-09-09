import os

from sqlalchemy import create_engine, event, inspect, text
from sqlalchemy.pool import StaticPool
from sqlalchemy.orm import DeclarativeBase, sessionmaker

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./dwp.db")

# SQLite 并发写锁治理：
# - busy_timeout：撞锁时等待重试（而非立刻抛 "database is locked"）；
# - WAL + synchronous=NORMAL：读写不互斥，显著降低并发写冲突概率。
SQLITE_BUSY_TIMEOUT_MS = int(os.getenv("SQLITE_BUSY_TIMEOUT_MS", "5000"))
SQLITE_IS_MEMORY = DATABASE_URL == "sqlite://"

_engine_kwargs: dict = {}
if DATABASE_URL.startswith("sqlite"):
    _engine_kwargs["connect_args"] = {
        "check_same_thread": False,
        # sqlite3.connect 的 timeout 即 busy_timeout，单位秒；撞锁时会等待而非立刻报错。
        "timeout": SQLITE_BUSY_TIMEOUT_MS / 1000.0,
    }
    # 内存库：后台任务与请求/测试共享同一连接（否则各线程各自独立空库）
    if SQLITE_IS_MEMORY:
        _engine_kwargs["poolclass"] = StaticPool

engine = create_engine(DATABASE_URL, **_engine_kwargs)


if DATABASE_URL.startswith("sqlite") and not SQLITE_IS_MEMORY:
    @event.listens_for(engine, "connect")
    def _set_sqlite_pragmas(dbapi_connection, _connection_record):  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        try:
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.execute(f"PRAGMA busy_timeout={SQLITE_BUSY_TIMEOUT_MS}")
        finally:
            cursor.close()


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
    plugin_additions = {
        "runtime_meta": "JSON NOT NULL DEFAULT '{}'",
        "plugin_type": "VARCHAR NOT NULL DEFAULT 'mcp'",
        "scope": "VARCHAR NOT NULL DEFAULT 'shared'",
        "owner_human_no": "VARCHAR",
        "mcp_category": "VARCHAR",
        "current_version": "VARCHAR",
    }
    if plugin_columns:
        with engine.begin() as connection:
            for name, ddl in plugin_additions.items():
                if name not in plugin_columns:
                    connection.execute(text(f"ALTER TABLE plugin ADD COLUMN {name} {ddl}"))
    grant_columns = {column["name"] for column in inspector.get_columns("employee_plugin_grant")}
    if grant_columns and "grant_source" not in grant_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE employee_plugin_grant ADD COLUMN grant_source VARCHAR NOT NULL DEFAULT ''")
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
