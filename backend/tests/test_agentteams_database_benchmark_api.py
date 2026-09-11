def _payload(**overrides):
    payload = {
        "scenario_ids": ["lock_contention", "large_query"],
        "repeats": 1,
        "max_model_steps": 20,
        "max_tool_calls": 10,
        "timeout_ms": 30_000,
    }
    payload.update(overrides)
    return payload


def test_create_get_and_list_database_benchmark_runs(client, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "app.routers.agentteams_benchmarks.enqueue_experiment",
        lambda experiment_id: queued.append(experiment_id),
    )

    created = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(),
    )

    assert created.status_code == 201
    body = created.json()
    assert body["benchmark_id"] == "multiagentbench-database-local-v1"
    assert body["status"] == "pending"
    assert body["total_runs"] == 6
    assert body["completed_runs"] == 0
    assert len(body["fairness_fingerprint"]) == 64
    assert queued == [body["id"]]

    fetched = client.get(
        f"/api/v1/agentteams-benchmarks/database/experiments/{body['id']}?actor_no=E10281"
    )
    assert fetched.status_code == 200
    assert fetched.json()["id"] == body["id"]

    runs = client.get(
        f"/api/v1/agentteams-benchmarks/database/experiments/{body['id']}/runs?actor_no=E10281"
    )
    assert runs.status_code == 200
    assert [(row["scenario_id"], row["mode"]) for row in runs.json()] == [
        ("lock_contention", "agentteams_full"),
        ("lock_contention", "agentteams_serial"),
        ("lock_contention", "agentteams_uncoordinated"),
        ("large_query", "agentteams_full"),
        ("large_query", "agentteams_serial"),
        ("large_query", "agentteams_uncoordinated"),
    ]
    assert all("task_run_id" in row for row in runs.json())


def test_latest_database_benchmark_restores_the_most_recent_owner_experiment(client, monkeypatch):
    monkeypatch.setattr(
        "app.routers.agentteams_benchmarks.enqueue_experiment",
        lambda _experiment_id: None,
    )

    before_create = client.get(
        "/api/v1/agentteams-benchmarks/database/experiments/latest?actor_no=E10281"
    )
    created = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(scenario_ids=["lock_contention"]),
    )
    restored = client.get(
        "/api/v1/agentteams-benchmarks/database/experiments/latest?actor_no=E10281"
    )

    assert before_create.status_code == 200
    assert before_create.json() is None
    assert restored.status_code == 200
    assert restored.json()["id"] == created.json()["id"]


def test_database_benchmark_isolated_by_owner(client, monkeypatch):
    monkeypatch.setattr("app.routers.agentteams_benchmarks.enqueue_experiment", lambda _id: None)
    created = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(scenario_ids=["lock_contention"]),
    ).json()

    forbidden = client.get(
        f"/api/v1/agentteams-benchmarks/database/experiments/{created['id']}?actor_no=E10021"
    )

    assert forbidden.status_code == 403


def test_database_benchmark_rejects_unknown_scenario_and_invalid_repeat(client, monkeypatch):
    monkeypatch.setattr("app.routers.agentteams_benchmarks.enqueue_experiment", lambda _id: None)

    unknown = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(scenario_ids=["not_a_scenario"]),
    )
    repeats = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(repeats=0),
    )

    assert unknown.status_code == 422
    assert repeats.status_code == 422


def test_database_benchmark_unknown_id_and_actor_are_not_found(client):
    unknown_actor = client.get(
        "/api/v1/agentteams-benchmarks/database/experiments/DBE-X?actor_no=UNKNOWN"
    )
    unknown_id = client.get(
        "/api/v1/agentteams-benchmarks/database/experiments/DBE-X?actor_no=E10281"
    )

    assert unknown_actor.status_code == 404
    assert unknown_id.status_code == 404


def test_database_benchmark_rejects_a_second_active_experiment_for_the_same_owner(client, monkeypatch):
    queued = []
    monkeypatch.setattr(
        "app.routers.agentteams_benchmarks.enqueue_experiment",
        lambda experiment_id: queued.append(experiment_id),
    )

    first = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(scenario_ids=["compound_lock_index"]),
    )
    second = client.post(
        "/api/v1/agentteams-benchmarks/database/experiments?actor_no=E10281",
        json=_payload(scenario_ids=["compound_lock_index"]),
    )

    assert first.status_code == 201
    assert second.status_code == 409
    assert second.json() == {
        "error": {
            "code": "STATE_CONFLICT",
            "message": None,
            "detail": {
            "code": "benchmark_experiment_already_running",
            "experiment_id": first.json()["id"],
            },
        }
    }
    assert queued == [first.json()["id"]]
