"""Engine, session factory, and the declarative base.

**Alembic owns the schema.** This module deliberately has no ``create_all``.
It used to run one on every boot while an Alembic baseline also existed, which
is two sources of truth for the same schema: they agree until the first model
change, and a database built by ``create_all`` carries no version row, so
``alembic upgrade head`` cannot then be applied to it without a manual stamp.
Migrations run as a release step -- ``alembic upgrade head`` -- before the app
starts serving.

The pool is sized for where this actually runs. Neon and Supabase put PgBouncer
in front of Postgres, and a client-side pool behind a server-side pooler holds
connections the pooler is also trying to manage. ``DB_POOL_MODE=null`` turns the
client pool off for those deployments; the default keeps a small real pool for a
directly-connected Postgres.
"""

from collections.abc import AsyncGenerator
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import get_settings

settings = get_settings()


def _engine_kwargs() -> dict[str, Any]:
    """Pool configuration, which differs by deployment shape.

    ``pool_size`` and ``max_overflow`` are not merely ignored by ``NullPool`` --
    passing them alongside it raises ``TypeError``, so the two cases have to be
    built separately rather than filtered.
    """

    if settings.database_url_async.startswith("sqlite"):
        return {"connect_args": {"check_same_thread": False}}
    if settings.DB_POOL_MODE == "null":
        return {"poolclass": NullPool}
    return {
        "pool_size": settings.DB_POOL_SIZE,
        "max_overflow": settings.DB_MAX_OVERFLOW,
        # Recycle below the typical cloud-Postgres idle timeout so a connection
        # is never handed out after the server has already dropped it.
        "pool_recycle": 1800,
        "pool_pre_ping": True,
    }


engine = create_async_engine(
    settings.database_url_async,
    echo=settings.DEBUG,
    **_engine_kwargs(),
)

async_session_maker = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with async_session_maker() as session:
        try:
            yield session
        finally:
            await session.close()


async def close_db() -> None:
    await engine.dispose()
