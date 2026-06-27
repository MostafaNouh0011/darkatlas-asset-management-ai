"""
Database connection setup.

Why a session-per-request pattern: FastAPI's Depends() gives us a fresh
SQLAlchemy session per request and guarantees it's closed afterward, even
if the request raises an exception. This mirrors the connection-per-query
discipline you'd want in raw psycopg2 too — we're just letting SQLAlchemy
manage the boilerplate.
"""
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, declarative_base

DATABASE_URL = os.getenv("DATABASE_URL", "postgresql://buguard:buguard@localhost:5432/assets")

engine = create_engine(DATABASE_URL, pool_pre_ping=True)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def get_db():
    """Dependency injected into routes via Depends(get_db)."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
