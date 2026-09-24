import os

os.environ["DATABASE_URL"] = "sqlite://"
# 测试默认走内置编排，避免加载真实 .env 后连外部编排服务
os.environ["DWP_TEAM_BACKEND"] = "builtin"
os.environ["DWP_HARNESS_ENABLED"] = "0"
# 旧版接口测试保留兼容模式；正式部署默认要求登录。
os.environ["DWP_REQUIRE_AUTH"] = "0"
# 固定初始密码：避免容器 /run/secrets 的真实密码覆盖测试默认密码，导致登录 401。
os.environ["DWP_EMPLOYEE_INITIAL_PASSWORD"] = "Demo@123456"
os.environ["DWP_ADMIN_INITIAL_PASSWORD"] = "Demo@123456"
os.environ["DWP_LOGIN_KEY_E10281"] = "test-key-e10281"
os.environ["DWP_LOGIN_KEY_E20999"] = "test-key-e20999"
os.environ["DWP_LOGIN_KEY_ADMIN"] = "test-key-admin00"
os.environ["DWP_ENABLE_LEGACY_PASSWORD_LOGIN"] = "1"

import pytest
from fastapi.testclient import TestClient

from app.database import Base, SessionLocal, engine, get_db
from app.main import app
from app.seed import load_seed, seed_data


@pytest.fixture(autouse=True)
def _reset_knowledge_source_cache():
    """知识源健康状态有进程内 TTL 缓存，而每个用例都会重建数据库。

    缓存按引擎对象标识缓存，跨用例复用同一引擎 → 必须每例清空，否则上一个用例
    写入的「不可达」状态会串到下一个用例。
    """
    from app.services import knowledge_source_health

    knowledge_source_health.invalidate_cache()
    yield
    knowledge_source_health.invalidate_cache()


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
