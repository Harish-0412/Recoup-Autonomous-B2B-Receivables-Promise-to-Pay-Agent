"""Request/response contracts for the integrations endpoints.

Covers: OAuth connect, sync trigger, status query, and Tally file import.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class OAuthConnectIn(BaseModel):
    """OAuth2 authorization code exchange request."""

    model_config = ConfigDict(protected_namespaces=())

    code: str = Field(min_length=1, description="Authorization code from OAuth redirect")
    redirect_uri: str = Field(
        min_length=1,
        description="The redirect URI used when generating the auth URL; must match exactly",
    )
    #: Provider-specific extras (e.g. Zoho org_id, QuickBooks realm_id)
    extras: dict = Field(default_factory=dict)


class OAuthConnectOut(BaseModel):
    """Result of exchanging an OAuth code for tokens."""

    model_config = ConfigDict(protected_namespaces=())

    provider: str
    connected: bool
    scope: str
    expires_at: datetime | None
    message: str


class SyncResponse(BaseModel):
    """Result of a manual or scheduled ERP sync."""

    model_config = ConfigDict(protected_namespaces=())

    provider: str
    customers_created: int = 0
    customers_updated: int = 0
    invoices_created: int = 0
    invoices_updated: int = 0
    invoices_skipped: int = 0
    credit_notes_applied: int = 0
    errors: list[str] = Field(default_factory=list)
    synced_at: datetime


class IntegrationStatusOut(BaseModel):
    """Current state of one integration for one tenant."""

    model_config = ConfigDict(protected_namespaces=())

    provider: str
    connected: bool
    last_sync_at: datetime | None
    last_sync_invoices: int
    last_sync_errors: int
    token_expires_at: datetime | None


class TallyImportResponse(BaseModel):
    """Result of a Tally file import (CSV or XML)."""

    model_config = ConfigDict(protected_namespaces=())

    filename: str
    format: str  # "csv" | "xml"
    customers_created: int = 0
    customers_updated: int = 0
    invoices_created: int = 0
    invoices_updated: int = 0
    invoices_skipped: int = 0
    errors: list[str] = Field(default_factory=list)
    imported_at: datetime
