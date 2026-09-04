"""Model studios: serve trained-model evidence as JSON.

Two read-only endpoints with no database, no network, and no side effects:

* ``GET /models/recovery/card`` -- the recovery model card's numbers
  (``docs/recovery_model_card.md``) as a document the frontend renders.
* The reply studio's preview lives on the replies router
  (``POST /replies/classify-preview``), next to the pipeline it previews.

The metrics come from ``data/recovery_model/model_card.json`` when the training
script has been run, else the full ``evaluation_report.json``, else committed
fallback constants transcribed from the model card at seed 42. The fallback
exists because trained binaries are gitignored: a fresh clone has no artifact
and the studio must still show the documented numbers, labelled as such via
``source``. The two live flags -- ``model_version`` from the artifact store and
``use_model_scorer`` from settings -- are always read fresh.
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter

from app.core.logging import get_logger
from app.schemas.models import (
    CalibrationBinOut,
    HeadToHeadOut,
    RecoveryCardOut,
    RecoveryModelRow,
    ShapImportanceOut,
)
from src.ml.config import MLSettings

router = APIRouter(prefix="/models", tags=["models"])
logger = get_logger(__name__)

REPO_ROOT = Path(__file__).resolve().parents[2]
RECOVERY_REPORT_DIR = REPO_ROOT / "data" / "recovery_model"

SHIPPED_MODEL = "xgb-recovery"

#: Committed fallback, transcribed from docs/recovery_model_card.md at seed 42
#: (batch 6000, threshold 0.5, isotonic calibration on validation).
_FALLBACK_RESULTS: list[dict] = [
    {
        "model": "rules-based",
        "auc": 0.732,
        "average_precision": 0.861,
        "precision": 0.842,
        "recall": 0.606,
        "f1": 0.705,
        "brier": 0.2130,
        "ece": 0.167,
        "shipped": False,
    },
    {
        "model": "logreg-recovery",
        "auc": 0.774,
        "average_precision": 0.872,
        "precision": 0.762,
        "recall": 0.926,
        "f1": 0.836,
        "brier": 0.1706,
        "ece": 0.031,
        "shipped": False,
    },
    {
        "model": "xgb-recovery",
        "auc": 0.779,
        "average_precision": 0.866,
        "precision": 0.765,
        "recall": 0.924,
        "f1": 0.837,
        "brier": 0.1700,
        "ece": 0.033,
        "shipped": True,
    },
    {
        "model": "mlp-recovery",
        "auc": 0.767,
        "average_precision": 0.857,
        "precision": 0.782,
        "recall": 0.844,
        "f1": 0.812,
        "brier": 0.1743,
        "ece": 0.033,
        "shipped": False,
    },
]

_FALLBACK_BINS: list[dict] = [
    {"lower": 0.3, "upper": 0.4, "count": 118, "predicted": 0.345, "observed": 0.314, "gap": 0.032},
    {"lower": 0.6, "upper": 0.7, "count": 247, "predicted": 0.647, "observed": 0.599, "gap": 0.047},
    {"lower": 0.7, "upper": 0.8, "count": 173, "predicted": 0.775, "observed": 0.763, "gap": 0.012},
    {"lower": 0.8, "upper": 0.9, "count": 287, "predicted": 0.887, "observed": 0.909, "gap": -0.023},
]

_FALLBACK_HEAD_TO_HEAD: dict = {
    "challenger": "xgb-recovery",
    "incumbent": "rules-based",
    "challenger_auc": 0.779,
    "incumbent_auc": 0.732,
    "auc_delta": 0.047,
    "challenger_brier": 0.1700,
    "incumbent_brier": 0.2130,
    "challenger_value_at_risk_at_k": 15900000.0,
    "incumbent_value_at_risk_at_k": 13500000.0,
    "value_delta": 2460000.0,
    "k": 171,
    "top_fraction": 0.2,
}

_FALLBACK_IMPORTANCE: list[dict] = [
    {"feature": "customer_broken_promise_rate", "mean_abs_shap": 0.420, "direction": "negative"},
    {"feature": "customer_avg_days_late", "mean_abs_shap": 0.248, "direction": "negative"},
    {"feature": "recency_weighted_on_time_score", "mean_abs_shap": 0.187, "direction": "positive"},
    {"feature": "customer_on_time_ratio_90d", "mean_abs_shap": 0.160, "direction": "positive"},
    {"feature": "prior_promise_kept", "mean_abs_shap": 0.142, "direction": "positive"},
    {"feature": "days_overdue_at_scoring", "mean_abs_shap": 0.132, "direction": "negative"},
    {"feature": "customer_dispute_rate", "mean_abs_shap": 0.131, "direction": "negative"},
    {"feature": "customer_on_time_ratio_all_time", "mean_abs_shap": 0.129, "direction": "positive"},
    {"feature": "invoice_amount", "mean_abs_shap": 0.114, "direction": "negative"},
    {"feature": "customer_invoice_count", "mean_abs_shap": 0.101, "direction": ""},
]

#: Verbatim from the model card's Limitations section, shortened to one line
#: each for rendering. The full prose stays in the card.
_FALLBACK_LIMITATIONS: list[str] = [
    "The data is synthetic — every number measures whether the pipeline recovers a signal deliberately put into the generator, not real-world accuracy.",
    "The labels are observational, not causal — the model predicts who will pay, not who will pay because the agent acted.",
    "One horizon only (30 days) — multi-horizon survival modelling is a deliberate stretch item.",
    "No online retraining — sensible once real webhook-confirmed payment data accumulates; not before.",
]


def _artifact_model_version() -> str | None:
    """Latest trained recovery-model version, if an artifact exists."""

    try:
        from src.ml.recovery.artifacts import MODEL_NAME
        from src.ml.artifacts import LATEST_FILENAME

        settings = MLSettings()
        latest = settings.ml_artifacts_dir / MODEL_NAME / LATEST_FILENAME
        if latest.exists():
            return latest.read_text(encoding="utf-8").strip() or None
    except Exception as exc:  # missing dir, bad settings -- not fatal
        logger.debug("No recovery artifact version available", error=str(exc))
    return None


def _read_json(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.debug("Could not read model artifact JSON", path=str(path), error=str(exc))
        return None


def _card_from_model_card_json(payload: dict) -> RecoveryCardOut | None:
    """Parse the small artifact written by scripts/train_recovery_model.py."""

    try:
        return RecoveryCardOut(
            model_version=payload.get("model_version"),
            shipped_model=payload.get("shipped_model", SHIPPED_MODEL),
            threshold=float(payload.get("threshold", 0.5)),
            calibration=str(payload.get("calibration", "isotonic")),
            test_rows=int(payload.get("test_rows", 856)),
            source="model_card.json",
            results=[RecoveryModelRow(**row) for row in payload.get("results", [])],
            calibration_bins=[
                CalibrationBinOut(**b) for b in payload.get("calibration_bins", [])
            ],
            head_to_head=(
                HeadToHeadOut(**payload["head_to_head"])
                if payload.get("head_to_head")
                else None
            ),
            global_importance=[
                ShapImportanceOut(**item) for item in payload.get("global_importance", [])
            ],
        )
    except Exception as exc:
        logger.warning("model_card.json failed validation, ignoring", error=str(exc))
        return None


def _card_from_evaluation_report(payload: dict) -> RecoveryCardOut | None:
    """Parse the full training report into the same small shape."""

    try:
        test_rows = payload.get("test", [])
        shipped = payload.get("selected_model", SHIPPED_MODEL)
        results = [
            RecoveryModelRow(
                model=row["model_name"],
                auc=row["roc_auc"],
                average_precision=row["average_precision"],
                precision=row["precision"],
                recall=row["recall"],
                f1=row["f1"],
                brier=row["brier_score"],
                ece=row["expected_calibration_error"],
                shipped=row["model_name"] == shipped,
            )
            for row in test_rows
        ]
        shipped_row = next((r for r in test_rows if r["model_name"] == shipped), None)
        bins = [
            CalibrationBinOut(
                lower=b["lower"],
                upper=b["upper"],
                count=b["count"],
                predicted=b["mean_predicted"],
                observed=b["observed_rate"],
                gap=round(b["mean_predicted"] - b["observed_rate"], 6),
            )
            for b in (shipped_row or {}).get("calibration_bins", [])
            if b["count"] >= 15
        ]
        h2h = payload.get("head_to_head", {})
        head_to_head = HeadToHeadOut(
            challenger=h2h.get("challenger", shipped),
            incumbent=h2h.get("incumbent", "rules-based"),
            challenger_auc=h2h["challenger_auc"],
            incumbent_auc=h2h["incumbent_auc"],
            auc_delta=round(h2h["challenger_auc"] - h2h["incumbent_auc"], 6),
            challenger_brier=h2h["challenger_brier"],
            incumbent_brier=h2h["incumbent_brier"],
            challenger_value_at_risk_at_k=h2h["challenger_value_at_risk_at_k"],
            incumbent_value_at_risk_at_k=h2h["incumbent_value_at_risk_at_k"],
            value_delta=round(
                h2h["challenger_value_at_risk_at_k"]
                - h2h["incumbent_value_at_risk_at_k"],
                2,
            ),
            k=h2h["k"],
        )
        raw_importance = payload.get("global_importance") or []
        # Written as a list of [feature, value] pairs; accept a mapping too.
        pairs = (
            list(raw_importance.items())
            if isinstance(raw_importance, dict)
            else [(item[0], item[1]) for item in raw_importance]
        )
        importance = [
            ShapImportanceOut(
                feature=str(name),
                mean_abs_shap=float(value),
                direction="",
            )
            for name, value in pairs
        ][:10]
        return RecoveryCardOut(
            model_version=payload.get("model_version"),
            shipped_model=shipped,
            threshold=float(payload.get("threshold", 0.5)),
            calibration=str(payload.get("calibration", "isotonic")),
            test_rows=int(shipped_row["rows"]) if shipped_row else 856,
            source="evaluation_report.json",
            results=results,
            calibration_bins=bins,
            head_to_head=head_to_head,
            global_importance=importance,
        )
    except Exception as exc:
        logger.warning("evaluation_report.json failed validation, ignoring", error=str(exc))
        return None


def _fallback_card() -> RecoveryCardOut:
    return RecoveryCardOut(
        shipped_model=SHIPPED_MODEL,
        test_rows=856,
        source="model_card_fallback",
        results=[RecoveryModelRow(**row) for row in _FALLBACK_RESULTS],
        calibration_bins=[CalibrationBinOut(**b) for b in _FALLBACK_BINS],
        head_to_head=HeadToHeadOut(**_FALLBACK_HEAD_TO_HEAD),
        global_importance=[ShapImportanceOut(**item) for item in _FALLBACK_IMPORTANCE],
        limitations=list(_FALLBACK_LIMITATIONS),
    )


@router.get("/recovery/card", response_model=RecoveryCardOut)
async def recovery_card() -> RecoveryCardOut:
    """The recovery model card's numbers as JSON for the studio page."""

    card: RecoveryCardOut | None = None

    small = _read_json(RECOVERY_REPORT_DIR / "model_card.json")
    if small is not None:
        card = _card_from_model_card_json(small)

    if card is None:
        full = _read_json(RECOVERY_REPORT_DIR / "evaluation_report.json")
        if full is not None:
            card = _card_from_evaluation_report(full)

    if card is None:
        card = _fallback_card()

    # Live flags, always fresh regardless of where the metrics came from.
    artifact_version = _artifact_model_version()
    if artifact_version:
        card = card.model_copy(update={"model_version": artifact_version})
    card = card.model_copy(update={"use_model_scorer": MLSettings().use_model_scorer})
    if not card.limitations:
        card = card.model_copy(update={"limitations": list(_FALLBACK_LIMITATIONS)})
    return card
