from sqlalchemy import create_engine, inspect, text

from app import database


def test_sqlite_compatibility_adds_agentteams_task_owner_column(monkeypatch):
    legacy_engine = create_engine("sqlite://")
    with legacy_engine.begin() as connection:
        connection.execute(text("CREATE TABLE plugin (id VARCHAR PRIMARY KEY)"))
        connection.execute(text("CREATE TABLE employee_plugin_grant (id INTEGER PRIMARY KEY)"))
        connection.execute(
            text("CREATE TABLE task_run (id VARCHAR PRIMARY KEY, team_id VARCHAR NOT NULL)")
        )
        connection.execute(
            text(
                "CREATE TABLE agent_task_node ("
                "id VARCHAR PRIMARY KEY, parent_task_id VARCHAR, status VARCHAR NOT NULL)"
            )
        )
        connection.execute(
            text(
                "CREATE TABLE team_member ("
                "id INTEGER PRIMARY KEY, team_id VARCHAR NOT NULL, "
                "employee_id VARCHAR NOT NULL, role VARCHAR NOT NULL)"
            )
        )

    monkeypatch.setattr(database, "DATABASE_URL", "sqlite://")
    monkeypatch.setattr(database, "engine", legacy_engine)

    database.ensure_schema_compatibility()

    columns = {column["name"] for column in inspect(legacy_engine).get_columns("task_run")}
    assert "owner_human_no" in columns
    node_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("agent_task_node")
    }
    assert {"node_kind", "revision_of_task_id"} <= node_columns
    member_columns = {
        column["name"] for column in inspect(legacy_engine).get_columns("team_member")
    }
    assert "reports_to_employee_id" in member_columns
