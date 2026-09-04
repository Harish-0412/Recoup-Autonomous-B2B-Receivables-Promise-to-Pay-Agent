"""Contracts for the drift endpoints.

A flag is a suggestion that a human look at a customer, so the shapes carry
everything a reviewer needs on first read: the score against its frozen
threshold, which model said so, and the top drivers behind the verdict.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class DriftDriverOut(BaseModel):
    """One behavioral deviation behind a flag, largest first."""

    model_config = ConfigDict(protected_namespaces=())

    feature: str
    value: float
    deviation: float


class DriftFlagOut(BaseModel):
    """One nightly drift verdict for one customer."""

    model_config = ConfigDict(protected_namespaces=())

    customer_id: str
    customer_name: str
    anomaly_score: float
    threshold: float
    flagged: bool
    model_version: str
    window_days: int
    top_drivers: list[DriftDriverOut] = Field(default_factory=list)
    created_at: datetime


class DriftFlagListOut(BaseModel):
    """Recent verdicts, newest first."""

    model_config = ConfigDict(protected_namespaces=())

    count: int
    items: list[DriftFlagOut] = Field(default_factory=list)
