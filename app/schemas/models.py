"""Contracts for the model-studio endpoints.

These serve numbers that already exist elsewhere -- the training scripts compute
them, the model cards narrate them -- as small JSON documents the frontend can
render in 30 seconds. Nothing here trains, scores, or writes; it reads an
artifact when one exists and falls back to the committed model-card numbers
when it does not (a fresh clone has no artifact by design).
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RecoveryModelRow(BaseModel):
    """One row of the results table: one model, one held-out split."""

    model_config = ConfigDict(protected_namespaces=())

    model: str
    auc: float
    average_precision: float
    precision: float
    recall: float
    f1: float
    brier: float
    ece: float
    shipped: bool = False


class CalibrationBinOut(BaseModel):
    """One reliability-curve bin of the shipped model."""

    model_config = ConfigDict(protected_namespaces=())

    lower: float
    upper: float
    count: int = Field(ge=0)
    predicted: float
    observed: float
    gap: float


class HeadToHeadOut(BaseModel):
    """The number that matters: trained model vs the rules it replaces."""

    model_config = ConfigDict(protected_namespaces=())

    challenger: str
    incumbent: str
    challenger_auc: float
    incumbent_auc: float
    auc_delta: float
    challenger_brier: float
    incumbent_brier: float
    challenger_value_at_risk_at_k: float
    incumbent_value_at_risk_at_k: float
    value_delta: float
    k: int
    top_fraction: float = 0.2


class ShapImportanceOut(BaseModel):
    """One global SHAP importance entry, top 10 only."""

    model_config = ConfigDict(protected_namespaces=())

    feature: str
    mean_abs_shap: float
    direction: str = ""


class RecoveryCardOut(BaseModel):
    """The recovery model card as JSON.

    ``source`` says where the numbers came from: a freshly written
    ``model_card.json`` artifact, the full ``evaluation_report.json``, or the
    committed fallback transcribed from ``docs/recovery_model_card.md``.
    """

    model_config = ConfigDict(protected_namespaces=())

    model_version: str | None = None
    shipped_model: str = "xgb-recovery"
    use_model_scorer: bool = False
    threshold: float = 0.5
    calibration: str = "isotonic"
    test_rows: int = 856
    source: str = "model_card_fallback"
    results: list[RecoveryModelRow] = Field(default_factory=list)
    calibration_bins: list[CalibrationBinOut] = Field(default_factory=list)
    head_to_head: HeadToHeadOut | None = None
    global_importance: list[ShapImportanceOut] = Field(default_factory=list)
    limitations: list[str] = Field(default_factory=list)


class ClassifyPreviewIn(BaseModel):
    """Raw customer reply text to classify without side effects."""

    model_config = ConfigDict(protected_namespaces=())

    text: str = Field(min_length=1, max_length=2000)
    invoice_id: str = Field(default="preview", max_length=64)


class ClassifyPreviewEntities(BaseModel):
    """Deterministic entities attached to the preview, if any."""

    model_config = ConfigDict(protected_namespaces=())

    promised_amount: float | None = None
    promised_date: str | None = None
    currency: str = "INR"
    dispute_reason: str | None = None


class ClassifyPreviewOut(BaseModel):
    """What the cascade thought, and which stage thought it.

    Explicitly non-mutating: no signature check, no DB write, no promise
    created, no opt-out recorded. ``stage_used`` is ``cascade`` when Stage C
    answered directly, ``llm`` when it escalated, and ``guard`` when the
    deterministic opt-out guard fired before any classifier ran.
    """

    model_config = ConfigDict(protected_namespaces=())

    intent: str
    confidence: float
    entities: ClassifyPreviewEntities = Field(default_factory=ClassifyPreviewEntities)
    stage_used: str
    classifier_version: str
    fallback_used: bool = False
    needs_review: bool = False
    explanation: str | None = None
