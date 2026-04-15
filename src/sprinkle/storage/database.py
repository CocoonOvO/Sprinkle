"""Database connection module using SQLAlchemy 2.0 async engine."""

from __future__ import annotations

from functools import lru_cache
from typing import AsyncGenerator
from urllib.parse import quote_plus

from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, create_async_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from sprinkle.config import settings


# ============================================================================
# SQLAlchemy Base
# ============================================================================

class Base(DeclarativeBase):
    """SQLAlchemy declarative base."""
    pass


# ============================================================================
# Async Engine & Session
# ============================================================================

@lru_cache()
def get_async_engine() -> AsyncEngine:
    """Create and return an async SQLAlchemy engine.
    
    Uses the database configuration to build the connection URL.
    Supports both synchronous and asynchronous drivers.
    
    For testing (SPRINKLE_TEST=1), uses NullPool to avoid connection reuse
    issues across different event loops in async tests.
    """
    import os
    from sqlalchemy.pool import NullPool
    
    db_url = _build_async_db_url(settings.database)
    
    # Check for test mode
    is_test = os.environ.get("SPRINKLE_TEST", "0") == "1"
    
    if is_test:
        return create_async_engine(
            db_url,
            echo=settings.app.debug,
            pool_pre_ping=False,
            poolclass=NullPool,
        )
    else:
        return create_async_engine(
            db_url,
            echo=settings.app.debug,
            pool_pre_ping=True,
            pool_size=5,
            max_overflow=10,
        )


def _build_async_db_url(db_config) -> str:
    """Build an async database URL from config.
    
    Converts postgresql://... to postgresql+asyncpg://...
    for use with asyncpg driver.
    """
    driver = db_config.driver
    
    # If using asyncpg, prefix with postgresql+asyncpg
    if "asyncpg" not in driver and driver == "postgresql":
        driver = "postgresql+asyncpg"
    
    user = db_config.user or ""
    password = db_config.password or ""
    host = db_config.host or "localhost"
    port = db_config.port or 5432
    name = db_config.name or ""
    
    return f"{driver}://{user}:{quote_plus(password)}@{host}:{port}/{name}"


def get_async_session_factory() -> sessionmaker[AsyncSession]:
    """Get an async session factory."""
    engine = get_async_engine()
    return sessionmaker(
        engine,
        class_=AsyncSession,
        expire_on_commit=False,
    )


async def get_async_session() -> AsyncGenerator[AsyncSession, None]:
    """Dependency for FastAPI to get an async session.
    
    Usage:
        @app.get("/users")
        async def get_users(session: AsyncSession = Depends(get_async_session)):
            ...
    """
    factory = get_async_session_factory()
    async with factory() as session:
        yield session


# ============================================================================
# Sync Engine (for migrations, scripts, etc.)
# ============================================================================

def get_sync_engine():
    """Create a synchronous SQLAlchemy engine.
    
    Used for migrations and scripts that don't need async.
    """
    from sqlalchemy import create_engine
    
    db_url = f"{settings.database.driver}://{settings.database.user}:{quote_plus(settings.database.password)}@{settings.database.host}:{settings.database.port}/{settings.database.name}"
    
    return create_engine(
        db_url,
        echo=settings.app.debug,
        pool_pre_ping=True,
    )


# ============================================================================
# Sync Session (for API endpoints and scripts)
# ============================================================================

@lru_cache()
def get_sync_session_factory():
    """Get a synchronous session factory."""
    engine = get_sync_engine()
    from sqlalchemy.orm import sessionmaker
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


def get_session_local():
    """Get a synchronous session factory (SessionLocal pattern).
    
    Returns a sessionmaker that can be used like:
        db = SessionLocal()
        try:
            ...
        finally:
            db.close()
    """
    return get_sync_session_factory()


# Expose SessionLocal for direct use (e.g., SessionLocal())
SessionLocal = get_sync_session_factory()


# ============================================================================
# Convenience functions
# ============================================================================

async def init_db():
    """Initialize the database (create tables).
    
    Call this on application startup.
    """
    engine = get_async_engine()
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def close_db():
    """Close database connections.
    
    Call this on application shutdown.
    """
    engine = get_async_engine()
    await engine.dispose()