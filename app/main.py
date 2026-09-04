from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

# Imported for its side effect: this registers every table on
# Base.metadata, which is what Alembic's autogenerate compares against.
# app.db.session deliberately does not import models itself, to avoid a
# circular import.
#
# Written as `from app import models` rather than `import app.models` on
# purpose: the latter binds the name `app`, which this module then rebinds to
# the FastAPI instance. Inside lifespan() that same import would rebind the
# function's own `app` parameter.
from app import models as _models  # noqa: F401
from app.api.health import router as health_router
from app.api.invoices import router as invoices_router
from app.api.models import router as models_router
from app.api.policy import router as policy_router
from app.api.replies import router as replies_router
from app.api.reports import router as reports_router
from app.api.tasks import router as tasks_router
from app.api.webhooks import router as webhooks_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import close_db, engine
from src.ml.reply.cascade import install_cascade_classifier

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Start up, and verify the database is actually reachable and migrated.

    Note what this does *not* do: create tables. Schema is Alembic's job, run
    as a release step before this process starts serving. Booting an app that
    silently creates whatever tables it happens to want is how a production
    database ends up with no migration history.

    The connectivity check is here so a bad DATABASE_URL fails at startup --
    visible as a failed release -- rather than as a 500 on the first request.
    """

    setup_logging()
    logger.info("Starting application", env=settings.APP_ENV, debug=settings.DEBUG)

    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    logger.info("Database reachable", pool_mode=settings.DB_POOL_MODE)

    # Reply understanding: the trained TF-IDF/SVM classifier answers the clear
    # cases locally in milliseconds and escalates only the ambiguous ones to the
    # LLM. ``cascade`` deliberately does not install itself on import -- doing
    # that as an import side effect makes test failures hard to explain -- so
    # this is the one place it is bound. With no trained artifact present it
    # degrades to the LLM baseline, and with no LLM key to a flagged fallback
    # that routes the reply to human review.
    install_cascade_classifier()
    logger.info("Reply classifier cascade installed")

    yield
    await close_db()
    logger.info("Application shutdown complete")


app = FastAPI(
    title=settings.APP_NAME,
    description="Autonomous B2B Receivables & Promise-to-Pay Agent",
    version="0.1.0",
    lifespan=lifespan,
    docs_url="/docs" if settings.DEBUG else None,
    redoc_url="/redoc" if settings.DEBUG else None,
)

# CORS_ORIGINS is the only way to widen this. The previous form used "*" under
# DEBUG, which browsers reject outright when paired with allow_credentials --
# so the permissive branch did not even work, it just failed confusingly.
# Credentials are allowed only when specific origins are named, which is the
# combination the spec actually permits.
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=bool(settings.cors_origins),
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api/v1")
app.include_router(invoices_router, prefix="/api/v1")
app.include_router(models_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(reports_router, prefix="/api/v1")
app.include_router(policy_router, prefix="/api/v1")
app.include_router(replies_router, prefix="/api/v1")
app.include_router(tasks_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    return {
        "service": settings.APP_NAME,
        "version": "0.1.0",
        # Reports what is actually true, not what the environment is called:
        # docs follow DEBUG, and DEBUG is off by default in every environment.
        "docs": "/docs" if settings.DEBUG else "disabled (set DEBUG=true to enable)",
    }
