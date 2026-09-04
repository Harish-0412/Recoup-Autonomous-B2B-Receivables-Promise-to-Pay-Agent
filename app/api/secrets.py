"""Secrets management API endpoints for operations."""

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from app.core.config import get_settings
from app.core.logging import get_logger
from app.core.secrets import get_secret_manager

router = APIRouter(tags=["secrets"])
security = HTTPBearer()
settings = get_settings()
logger = get_logger(__name__)


async def verify_api_key(credentials: HTTPAuthorizationCredentials = Depends(security)) -> None:
    """Verify operator API key for secrets operations."""
    api_key = settings.API_KEY or settings.TASK_API_KEY
    if not api_key or credentials.credentials != api_key:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid API key",
            headers={"WWW-Authenticate": "Bearer"},
        )


@router.post("/secrets/refresh")
async def refresh_secrets(_: None = Depends(verify_api_key)) -> dict:
    """Refresh secrets from the active provider.

    Used during secret rotation to pull updated values without restart.
    Only works if a secrets provider is active (not env fallback).
    """
    try:
        manager = await get_secret_manager()

        if not manager.active_provider:
            return {
                "status": "no_provider",
                "message": "No active secrets provider; using environment fallback only",
            }

        success = await manager.refresh_secrets()

        if success:
            logger.info("Secrets refreshed via API", provider=manager.active_provider.name)
            return {
                "status": "refreshed",
                "provider": manager.active_provider.name,
                "cache_size": len(manager.cache),
            }
        else:
            return {"status": "failed", "message": "Failed to refresh secrets from provider"}

    except Exception as exc:
        logger.error("Secret refresh API failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Secret refresh failed: {exc}",
        )


@router.get("/secrets/status")
async def secrets_status(_: None = Depends(verify_api_key)) -> dict:
    """Get current secrets management status.

    Returns provider info and cache status for operational visibility.
    Does not expose secret values.
    """
    try:
        manager = await get_secret_manager()

        return {
            "provider": manager.active_provider.name if manager.active_provider else "env_fallback",
            "cache_size": len(manager.cache),
            "cached_secrets": [
                {
                    "name": name,
                    "source": secret.source,
                    "last_updated": secret.last_updated,
                    "has_value": bool(secret.value),
                }
                for name, secret in manager.cache.items()
            ],
        }

    except Exception as exc:
        logger.error("Secrets status API failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to get secrets status: {exc}",
        )
