from fastapi import APIRouter, Depends, status
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.db.session import get_db

router = APIRouter(tags=["health"])
settings = get_settings()


@router.get("/health")
async def health_check() -> dict:
    return {
        "status": "healthy",
        "service": settings.APP_NAME,
        "environment": settings.APP_ENV,
        "version": "0.1.0",
    }


@router.get("/health/db")
async def health_check_db(db: AsyncSession = Depends(get_db)) -> dict:
    try:
        await db.execute(text("SELECT 1"))
        return {"status": "healthy", "database": "connected"}
    except Exception as e:
        return {"status": "unhealthy", "database": "disconnected", "error": str(e)}


@router.get("/ready")
async def ready_check(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Dependency probe: DB and (optionally) Redis must answer.

    Returns 200 when every configured dependency answers; 503 with a
    ``failing`` list otherwise. Render / Kubernetes use this to decide
    whether to route traffic to this pod.

    * ``db``: always checked (SELECT 1 ping)
    * ``redis``: checked IFF ``RATE_LIMIT_REDIS_URL`` is set; else ``skipped``
    """

    checks: dict[str, str] = {}
    failing: list[str] = []

    try:
        await db.execute(text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as exc:  # noqa: BLE001
        checks["db"] = "fail"
        checks["db_error"] = str(exc)[:200]
        failing.append("db")

    redis_url = get_settings().RATE_LIMIT_REDIS_URL
    if not redis_url:
        checks["redis"] = "skipped"
    else:
        try:
            from app.core.ratelimit import _get_redis_client

            client = await _get_redis_client()
            if client is None:
                checks["redis"] = "fail"
                failing.append("redis")
            else:
                pong = await client.ping()
                checks["redis"] = "ok" if pong else "fail"
                if not pong:
                    failing.append("redis")
        except Exception as exc:  # noqa: BLE001
            checks["redis"] = "fail"
            checks["redis_error"] = str(exc)[:200]
            failing.append("redis")

    overall = "ready" if not failing else "not_ready"
    code = status.HTTP_200_OK if not failing else status.HTTP_503_SERVICE_UNAVAILABLE
    return JSONResponse(
        status_code=code,
        content={
            "status": overall,
            "checks": checks,
            "failing": failing,
            "service": settings.APP_NAME,
            "version": "0.1.0",
        },
    )
