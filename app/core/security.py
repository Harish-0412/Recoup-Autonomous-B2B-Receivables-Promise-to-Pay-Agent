"""Authentication for the endpoints that act on their own.

Most of this API is still open -- that is phase 5 of the backend plan. The task
endpoints could not wait for it: they send real email to real customers, and an
unauthenticated trigger is an unauthenticated way to mail your whole book. So
these are locked now, ahead of the rest.

Two details that are easy to get wrong:

* **Constant-time comparison.** ``==`` on secrets leaks their prefix through
  timing. ``secrets.compare_digest`` does not.
* **An unset key denies, it does not allow.** A blank ``TASK_API_KEY`` refuses
  every request rather than waving them through, so a deploy that forgot to set
  it fails visibly instead of running wide open.

Webhooks deliberately do not use this. Razorpay and Resend cannot present our
bearer token; they prove themselves by signing their payloads, which is checked
in their own handlers.
"""

from __future__ import annotations

import secrets

from fastapi import Header, HTTPException, status

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)


def require_task_key(authorization: str = Header(default="")) -> None:
    """Reject anything without the configured bearer token.

    Used as a route dependency, so the check happens before the handler body
    runs and cannot be forgotten half way down one.
    """

    settings = get_settings()
    expected = settings.TASK_API_KEY

    if not expected:
        logger.error("Task endpoint called but TASK_API_KEY is not configured")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="Task endpoints are disabled: TASK_API_KEY is not configured.",
        )

    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not secrets.compare_digest(token, expected):
        # The response says nothing about which half was wrong.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated.",
            headers={"WWW-Authenticate": "Bearer"},
        )
