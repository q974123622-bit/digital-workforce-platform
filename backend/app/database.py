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
    task_run_columns = {column["name"] for column in inspector.get_columns("task_run")}
    if task_run_columns and "owner_human_no" not in task_run_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE task_run ADD COLUMN owner_human_no VARCHAR NOT NULL DEFAULT ''")
            )
    task_node_columns = {
        column["name"] for column in inspector.get_columns("agent_task_node")
    }
    if task_node_columns and "node_kind" not in task_node_columns:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "ALTER TABLE agent_task_node ADD COLUMN "
                    "node_kind VARCHAR NOT NULL DEFAULT 'worker_task'"
                )
            )
            connection.execute(
                text(
                    "UPDATE agent_task_node SET node_kind = 'mission' "
                    "WHERE parent_task_id IS NULL"
                )
            )
    if task_node_columns and "revision_of_task_id" not in task_node_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE agent_task_node ADD COLUMN revision_of_task_id VARCHAR")
            )
    team_member_columns = {
        column["name"] for column in inspector.get_columns("team_member")
    }
    if team_member_columns and "reports_to_employee_id" not in team_member_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE team_member ADD COLUMN reports_to_employee_id VARCHAR")
            )
    if team_member_columns and "capabilities" not in team_member_columns:
        with engine.begin() as connection:
            connection.execute(
                text("ALTER TABLE team_member ADD COLUMN capabilities JSON NOT NULL DEFAULT '[]'")
            )


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
