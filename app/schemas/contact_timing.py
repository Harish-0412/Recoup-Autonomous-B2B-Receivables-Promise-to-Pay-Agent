"""Contracts for the contact-timing endpoints."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class NextTimeOut(BaseModel):
    """One send-time recommendation for a customer."""

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str
    segment: str
    arm: str
    scheduled_for: datetime
    expected_response_rate: float = Field(ge=0.0, le=1.0)
    observations: int = Field(ge=0)
    backed_off_to_global: bool = False
    fallback_used: bool = False
    fallback_reason: str = ""
