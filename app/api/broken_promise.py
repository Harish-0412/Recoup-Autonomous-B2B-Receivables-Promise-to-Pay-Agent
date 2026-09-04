"""Broken promise scoring router inside app/api.

Re-exports the router from src.agent.api.broken_promise_api and provides
unified endpoint access.
"""

from __future__ import annotations

from src.agent.api.broken_promise_api import router

__all__ = ["router"]
