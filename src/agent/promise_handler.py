"""Promise handler and broken-promise risk scorer integration.

Evaluates payment promises against customer behavioral history and invoice context
using the trained LightGBM ONNX model (predictor.onnx).
Guarantees graceful degradation: if an input field is missing or the model
fails, defaults safely to 0.5 without rejecting the promise.
"""

from __future__ import annotations

import math
from datetime import date, datetime
from pathlib import Path
from typing import Any

import numpy as np

REPO_ROOT = Path(__file__).resolve().parents[2]
ONNX_MODEL_PATH = REPO_ROOT / "models" / "broken_promise" / "predictor.onnx"

FEATURE_COLUMNS = (
    "customer_broken_promise_rate",
    "customer_broken_promises_count",
    "customer_avg_days_late",
    "customer_on_time_ratio_90d",
    "customer_on_time_ratio_all_time",
    "recency_weighted_on_time_score",
    "customer_dispute_rate",
    "customer_invoice_count",
    "customer_tenure_months",
    "invoice_amount",
    "invoice_amount_log",
    "invoice_amount_vs_customer_avg_ratio",
    "days_overdue_at_scoring",
    "payment_terms_days",
    "days_since_last_contact",
    "prior_reminders_sent",
    "promise_amount_ratio",
    "promise_horizon_days",
    "current_escalation_tier",
)

_SESSION: Any | None = None


def get_onnx_session() -> Any:
    """Lazy-load and cache ONNX inference session."""
    global _SESSION
    if _SESSION is None:
        if not ONNX_MODEL_PATH.exists():
            # If not yet trained, return None so fallback can be used
            return None
        import onnxruntime as ort
        # Configure session with single thread for minimal latency in async loop
        opts = ort.SessionOptions()
        opts.intra_op_num_threads = 1
        opts.inter_op_num_threads = 1
        _SESSION = ort.InferenceSession(str(ONNX_MODEL_PATH), sess_options=opts)
    return _SESSION


def recency_weighted_on_time_score(
    on_time_ratio_90d: float,
    on_time_ratio_all_time: float,
    invoice_count: int,
    *,
    half_life_invoices: float = 12.0,
) -> float:
    weight = 1.0 - math.exp(-max(invoice_count, 0) / max(half_life_invoices, 1e-6))
    recent_weight = 0.35 + 0.45 * weight
    return float(recent_weight * on_time_ratio_90d + (1.0 - recent_weight) * on_time_ratio_all_time)


def build_broken_promise_features(payload: dict[str, Any], as_of: date | None = None) -> list[float]:
    """Extract and validate the 19 features required by the ONNX predictor.

    Accepts raw dictionaries, nested customer/invoice records, or flat feature sets.
    """
    ref_date = as_of or date.today()

    customer = payload.get("customer") or {}
    invoice = payload.get("invoice") or {}

    def _get(key: str, default: float) -> float:
        val = payload.get(key)
        if val is None and isinstance(customer, dict):
            val = customer.get(key)
        if val is None and isinstance(invoice, dict):
            val = invoice.get(key)
        if val is None:
            return default
        try:
            return float(val)
        except (ValueError, TypeError):
            return default

    # Customer punctuality and history
    on_time_90d = _get("customer_on_time_ratio_90d", _get("on_time_ratio_90d", 0.85))
    on_time_all = _get("customer_on_time_ratio_all_time", _get("on_time_ratio_all_time", 0.88))
    inv_count = int(_get("customer_invoice_count", _get("invoice_count", 10)))

    recency_score = payload.get("recency_weighted_on_time_score")
    if recency_score is None:
        recency_score = recency_weighted_on_time_score(on_time_90d, on_time_all, inv_count)
    else:
        recency_score = float(recency_score)

    broken_promises_count = _get("customer_broken_promises_count", _get("prior_broken_promises_count", 0.0))
    broken_promise_rate = _get("customer_broken_promise_rate", _get("broken_promise_rate", -1.0))
    if broken_promise_rate < 0:
        broken_promise_rate = broken_promises_count / max(inv_count * 0.35, 1.0)
    broken_promise_rate = min(max(broken_promise_rate, 0.0), 1.0)

    avg_days_late = _get("customer_avg_days_late", _get("avg_days_late", 5.0))
    dispute_rate = _get("customer_dispute_rate", _get("dispute_rate", 0.05))
    tenure_months = _get("customer_tenure_months", _get("tenure_months", 12.0))

    # Invoice attributes
    invoice_amount = _get("invoice_amount", _get("amount", 50000.0))
    invoice_amount_log = _get("invoice_amount_log", math.log1p(max(invoice_amount, 0.0)))
    avg_customer_amount = _get("avg_invoice_amount", max(invoice_amount, 1.0))
    ratio = _get("invoice_amount_vs_customer_avg_ratio", invoice_amount / max(avg_customer_amount, 1.0))
    ratio = min(max(ratio, 0.0), 50.0)

    # Days overdue
    days_overdue = _get("days_overdue_at_scoring", _get("days_overdue", 0.0))
    if "due_date" in payload and days_overdue == 0:
        try:
            d_val = payload["due_date"]
            if isinstance(d_val, str):
                d_date = datetime.fromisoformat(d_val).date()
            elif isinstance(d_val, (date, datetime)):
                d_date = d_val if isinstance(d_val, date) else d_val.date()
            else:
                d_date = ref_date
            days_overdue = max((ref_date - d_date).days, 0)
        except Exception:
            pass

    payment_terms = _get("payment_terms_days", 30.0)
    days_since_contact = _get("days_since_last_contact", 5.0)
    prior_reminders = _get("prior_reminders_sent", 1.0)
    escalation_tier = _get("current_escalation_tier", _get("ladder_index", 0.0))

    # Promise attributes
    promised_amount = _get("promised_amount", invoice_amount)
    promise_amount_ratio = min(max(promised_amount / max(invoice_amount, 1.0), 0.0), 5.0)

    promise_horizon = _get("promise_horizon_days", 5.0)
    if "promised_date" in payload:
        try:
            p_val = payload["promised_date"]
            if isinstance(p_val, str):
                p_date = datetime.fromisoformat(p_val).date()
            elif isinstance(p_val, (date, datetime)):
                p_date = p_val if isinstance(p_val, date) else p_val.date()
            else:
                p_date = ref_date
            promise_horizon = max((p_date - ref_date).days, 0)
        except Exception:
            pass

    vec = [
        float(broken_promise_rate),
        float(broken_promises_count),
        float(avg_days_late),
        float(on_time_90d),
        float(on_time_all),
        float(recency_score),
        float(dispute_rate),
        float(inv_count),
        float(tenure_months),
        float(invoice_amount),
        float(invoice_amount_log),
        float(ratio),
        float(days_overdue),
        float(payment_terms),
        float(days_since_contact),
        float(prior_reminders),
        float(promise_amount_ratio),
        float(promise_horizon),
        float(escalation_tier),
    ]
    return vec


def score_broken_promise(payload: dict[str, Any], as_of: date | None = None) -> float:
    """Compute risk score (0.0 to 1.0) that a customer will break their promise.

    A score near 0 means high likelihood of keeping the promise (honoring it).
    A score near 1 means high risk of breaking the promise.
    Safe fallback: returns 0.5 on any unexpected runtime failure.
    """
    try:
        sess = get_onnx_session()
        features = build_broken_promise_features(payload, as_of)
        if sess is None:
            # Fallback heuristic if ONNX not yet compiled
            broken_rate = features[0]
            horizon = features[17]
            heuristic = 0.35 + 0.40 * broken_rate + 0.01 * min(horizon, 30)
            return float(min(max(heuristic, 0.05), 0.95))

        arr = np.array([features], dtype=np.float32)
        outputs = sess.run(None, {"float_input": arr})
        # outputs[1] is a list of dicts mapping label {0: p0, 1: p1}
        prob_dict = outputs[1][0]
        risk_score = float(prob_dict.get(1, 0.5))
        return round(float(np.clip(risk_score, 0.0, 1.0)), 4)
    except Exception:
        # Never let scoring crash the agent or reject a promise
        return 0.50
