"""Small helpers for model timestamps and stable version names."""

import re
import secrets
from datetime import UTC, datetime


def utc_now() -> datetime:
    """Return the current UTC time as a timezone-aware datetime."""

    return datetime.now(UTC)


def utc_now_iso() -> str:
    """Return the current UTC time in a compact ISO-8601 string."""

    return utc_now().isoformat(timespec="seconds").replace("+00:00", "Z")


def new_model_version(model_name: str) -> str:
    """Create a human-readable model version string.

    Example: ``recovery-xgb-20260904-a1b2c3``.
    """

    normalized_name = re.sub(r"[^a-z0-9]+", "-", model_name.lower()).strip("-")
    if not normalized_name:
        raise ValueError("model_name must contain at least one alphanumeric character")

    date_part = utc_now().strftime("%Y%m%d")
    suffix = secrets.token_hex(3)
    return f"{normalized_name}-{date_part}-{suffix}"
