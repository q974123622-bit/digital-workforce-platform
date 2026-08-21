import os

os.environ["DATABASE_URL"] = "sqlite://"
# 测试默认 mock 检索模式：避免 RAG 路径访问生产 DB/真实 embedding 服务
os.environ["DWP_KB_MODE"] = "mock"
# 测试默认走内置编排，避免加载真实 .env 后连 Matrix/AgentTeams（AgentTeams 路径由专门测试 mock 覆盖）
os.environ["DWP_TEAM_BACKEND"] = "builtin"
# AgentTeams 单元测试使用 Fake Gateway，但仍需一个非空房间 ID 才会进入该路径。
os.environ["AGENTTEAMS_ROOM_ID"] = "!test-room:agentteams.local"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.seed import load_seed, seed_data


@pytest.fixture()
def db_session():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    session = SessionLocal()
    seed_data(session, load_seed())
    yield session
    session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
