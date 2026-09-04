"""Response contracts for the receivables cash forecast.

Windows ride as a list rather than a dict so the shape stays stable if a
window is added later; every monetary figure is rupees, rounded to paise at
the simulation boundary.
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class WindowForecastOut(BaseModel):
    """The simulated distribution of cash landing inside one window."""

    model_config = ConfigDict(protected_namespaces=())

    window_days: int = Field(gt=0)
    draws: int = Field(gt=0)
    mean: float = Field(ge=0.0)
    median: float = Field(ge=0.0)
    p5: float = Field(ge=0.0)
    p25: float = Field(ge=0.0)
    p75: float = Field(ge=0.0)
    p95: float = Field(ge=0.0)
    prob_any_cash: float = Field(ge=0.0, le=1.0)


class CashForecastOut(BaseModel):
    """Windowed cash forecast over the current at-risk book."""

    model_config = ConfigDict(protected_namespaces=())

    windows: list[WindowForecastOut]
    n_invoices: int = Field(ge=0)
    at_risk_value: float = Field(ge=0.0)
    draws: int = Field(gt=0)
    seed: int
    probability_source: str
    scorer_fallbacks: int = Field(ge=0)
    lag_model_version: str
    calibrated: bool
    generated_at: datetime
