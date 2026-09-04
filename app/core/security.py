"""Authentication for the endpoints that act on their own.

Most of this API is operator-only: it reads the customer book, moves
collection state, or sends real email. Every such route carries a bearer
dependency (see below). Only liveness/health and the provider webhooks are
public -- webhooks prove themselves by signature, not by bearer token.

Two details that are easy to get wrong:

* **Constant-time comparison.** ``==`` on secrets leaks their prefix through
  timing. ``secrets.compare_digest`` does not.
* **An unset key denies, it does not allow.** A blank key refuses every
  request rather than waving it through, so a deploy that forgot to set it
  fails visibly instead of running wide open.

There is exactly one credential source: ``TASK_API_KEY`` (with ``API_KEY``
as an optional alias for dashboard reads). Publishable provider IDs such as
``RAZORPAY_KEY_ID`` are never accepted -- the publishable half of a payment
pair must not unlock "mail my entire customer book". No fallback or dev key
lives in this file; if none is configured the endpoint answers 503.

Do not add extra accepted keys here for "local convenience": two separate
edits have reintroduced RAZORPAY_KEY_ID/dev fallbacks, each caught by
test_an_unconfigured_key_denies_rather_than_allows and
tests/test_api_security.py. Local dev sets TASK_API_KEY in .env instead.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Marker the perimeter tests assert is the ONLY credential source note.
#: (Human-readable; the enforcement is _candidates() below taking exactly
#: the keys each gate passes -- TASK_API_KEY, optionally API_KEY.)


def _candidates(*keys: str) -> list[str]:
    """Non-empty configured keys only; unconfigured means deny, not allow."""
    return [k for k in keys if k]


def _check_bearer(authorization: str, valid_keys: list[str], *, service: str) -> None:
    if not valid_keys:
        logger.error("Endpoint called but no authorization key is configured", service=service)
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="This endpoint is disabled: TASK_API_KEY is not configured.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not any(secrets.compare_digest(token, k) for k in valid_keys):
        # The response says nothing about which half was wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )


def require_task_key(authorization: str = Header(default="")) -> None:
    """Reject anything without the configured cron bearer token.

    Used as a route dependency, so the check happens before the handler body
    runs and cannot be forgotten half way down one.
    """

    settings = get_settings()
    _check_bearer(authorization, _candidates(settings.TASK_API_KEY), service="tasks")


def require_api_key(authorization: str = Header(default="")) -> None:
    """Bearer gate for operator/dashboard routes.

    Accepts ``API_KEY`` when set, falling back to ``TASK_API_KEY`` so a
    single-operator deploy needs only one secret. Either being configured
    enables the route; neither configured disables it (503) rather than
    opening it.
    """

    settings = get_settings()
    _check_bearer(
        authorization,
        _candidates(settings.API_KEY, settings.TASK_API_KEY),
        service="api",
    )
