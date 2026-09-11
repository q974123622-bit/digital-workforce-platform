import httpx
from sqlalchemy import select

from app import models
from app.services import memory_worker
from app.services.memory_adapter import InternalMemoryAdapter, namespace


def _client_factory(handler):
    def factory(**kwargs):
        return httpx.Client(transport=httpx.MockTransport(handler), **kwargs)
    return factory


def test_automatic_memory_is_queued_and_worker_syncs(db_session, monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_MODE", "internal")
    monkeypatch.setenv("DWP_INTERNAL_MEM0_BASE_URL", "https://mem0.invalid")
    monkeypatch.setenv("DWP_INTERNAL_MEM0_AUTHORIZATION", "test-token")
    row = InternalMemoryAdapter().upsert(
        db_session, namespace("E10281", "AI-GENERAL"), "项目偏好", "summary", "automatic",
    )
    assert row.sync_status == "pending"
    assert db_session.scalar(select(models.MemorySyncJob)).status == "pending"

    def handler(request):
        assert request.method == "POST"
        return httpx.Response(200, json={"id": "remote-1"})

    assert memory_worker.process_one(client_factory=_client_factory(handler)) is True
    db_session.expire_all()
    assert db_session.get(models.MemoryRecord, row.id).sync_status == "synced"
    assert db_session.scalar(select(models.MemorySyncJob)).status == "completed"


def test_delete_job_survives_local_record_removal(db_session, monkeypatch):
    monkeypatch.setenv("DWP_MEMORY_MODE", "internal")
    monkeypatch.setenv("DWP_INTERNAL_MEM0_BASE_URL", "https://mem0.invalid")
    monkeypatch.setenv("DWP_INTERNAL_MEM0_AUTHORIZATION", "test-token")
    row = models.MemoryRecord(
        id="MEM-DELETE", tenant_id="default", requester_human_no="E10281",
        agent_id="AI-GENERAL", content="删除我", external_id="remote-delete", sync_status="synced",
    )
    db_session.add(row); db_session.commit()
    InternalMemoryAdapter().delete(db_session, row)
    assert db_session.get(models.MemoryRecord, "MEM-DELETE") is None

    def handler(request):
        assert request.method == "DELETE"
        assert request.url.path.endswith("/memories/remote-delete")
        return httpx.Response(204)

    assert memory_worker.process_one(client_factory=_client_factory(handler)) is True
    db_session.expire_all()
    assert db_session.scalar(select(models.MemorySyncJob)).status == "completed"


def test_running_memory_jobs_are_recovered(db_session):
    job = models.MemorySyncJob(id="MJ-STUCK", operation="upsert", status="running", attempts=2)
    db_session.add(job); db_session.commit()
    assert memory_worker.recover_running_jobs() == 1
    db_session.expire_all()
    recovered = db_session.get(models.MemorySyncJob, job.id)
    assert recovered.status == "pending"
    assert recovered.locked_at is None
