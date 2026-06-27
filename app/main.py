"""
App entrypoint. Run with: uvicorn app.main:app --reload
(or via docker-compose, see docker-compose.yml)
"""
from contextlib import asynccontextmanager
from fastapi import FastAPI
from app.database import Base, engine
from app.routers import import_router, assets_router, analysis_router


@asynccontextmanager
async def lifespan(app: FastAPI):
    """
    Tables are created on startup rather than via a full Alembic migration
    setup — a deliberate scope decision for a 1-week minimal-API track
    (documented in the README; Alembic would be the production answer).

    Done in a lifespan hook, not at module import time, so `import app.main`
    stays safe in tests even when no live database is reachable yet (tests
    override the DB dependency and the engine before this hook fires).
    """
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(
    title="Buguard Asset Management — AI Track",
    description="Minimal asset management API with a LangChain analysis layer.",
    version="0.1.0",
    lifespan=lifespan,
)

app.include_router(import_router.router)
app.include_router(assets_router.router)
app.include_router(analysis_router.router)


@app.get("/health")
def health():
    return {"status": "ok"}
