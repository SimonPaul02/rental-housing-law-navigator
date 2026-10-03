"""Async SQLAlchemy engine and session dependency."""

from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import settings


def _engine_kwargs() -> dict:
    kwargs: dict = {
        "echo": settings.db_echo,
        "pool_size": settings.db_pool_size,
        "max_overflow": settings.db_max_overflow,
        # Supabase free projects pause when idle and poolers drop connections;
        # revalidate before handing one out rather than failing a request.
        "pool_pre_ping": True,
    }
    if settings.db_disable_prepared_statements:
        # Supavisor in transaction mode (port 6543) multiplexes connections, so
        # asyncpg's prepared statements break. Both knobs are needed: one for
        # asyncpg itself, one for SQLAlchemy's dialect-level cache.
        kwargs["connect_args"] = {"statement_cache_size": 0}
        kwargs["prepared_statement_cache_size"] = 0
    return kwargs


engine = create_async_engine(settings.database_url, **_engine_kwargs())

SessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency: one session per request."""
    async with SessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
