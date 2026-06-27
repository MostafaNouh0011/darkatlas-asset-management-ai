"""
Shared test fixtures. Overrides the app's DB dependency to use an in-memory
SQLite database so the full FastAPI app (including routers) can be
exercised in tests without a live Postgres instance running.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from fastapi.testclient import TestClient

from app.database import Base, get_db
import app.main as main_module
from app.main import app


@pytest.fixture
def client():
    # StaticPool is required here: an in-memory SQLite DB is normally
    # connection-scoped, so without it, each new Session() would see an
    # empty database even though tables were "already created" on a
    # different underlying connection. StaticPool forces every session in
    # this test to share the exact same connection/database.
    test_engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    TestingSession = sessionmaker(bind=test_engine)
    Base.metadata.create_all(test_engine)

    # main.py's lifespan hook also calls Base.metadata.create_all(bind=engine)
    # using the module-level `engine` — point that at our test engine too,
    # so entering the TestClient context doesn't try to reach real Postgres.
    main_module.engine = test_engine

    def override_get_db():
        db = TestingSession()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
