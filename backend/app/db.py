from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from .config import settings


class Base(DeclarativeBase):
    pass


engine = create_engine(settings.database_url, pool_pre_ping=True) if settings.database_url else None
SessionLocal = (
    sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
    if engine is not None
    else None
)


def database_session():
    """Yield a short-lived database session for API dependencies."""
    if SessionLocal is None:
        raise RuntimeError("DATABASE_URL is not configured")
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
