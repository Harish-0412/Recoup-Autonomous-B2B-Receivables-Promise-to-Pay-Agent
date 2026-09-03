from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.health import router as health_router
from app.core.config import get_settings
from app.core.logging import get_logger, setup_logging
from app.db.session import close_db, init_db

settings = get_settings()
logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    setup_logging()
    logger.info("Starting application", env=settings.APP_ENV)
    await init_db()
    logger.info("Database initialized")
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

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"] if settings.DEBUG else [],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(health_router, prefix="/api/v1")


@app.get("/")
async def root() -> dict:
    return {
        "service": settings.APP_NAME,
        "version": "0.1.0",
        "docs": "/docs" if settings.DEBUG else "disabled in production",
    }
