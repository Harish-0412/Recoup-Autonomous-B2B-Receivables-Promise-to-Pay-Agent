"""Comprehensive validation and verification script for all 4 ML models in Recoup.

Tests:
1. Broken-Promise Risk Scorer (LightGBM ONNX)
2. Payment-Behavior Drift Detector (Isolation Forest)
3. Contact-Timing Optimizer (Thompson Sampling Contextual Bandit)
4. Receivables-Specific Cash Forecast (Monte Carlo simulation over P(recovery) x Timing)
5. Razorpay integration verification with test key (rzp_test_TXVM95IjmdDy4d)
"""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from pathlib import Path
from typing import Any

import httpx
from app.core.config import get_settings
from app.services.razorpay_client import get_razorpay_client

REPO_ROOT = Path(__file__).resolve().parents[1]
SETTINGS = get_settings()
BASE_URL = "http://127.0.0.1:8000"
OPERATOR_HEADERS = {"Authorization": f"Bearer {SETTINGS.TASK_API_KEY}"}


def verify_razorpay_gateway() -> dict[str, Any]:
    print("\n--- 0. Razorpay Gateway Verification ---")
    client = get_razorpay_client()
    key_id = SETTINGS.RAZORPAY_KEY_ID
    print(f"Razorpay Key ID configured: {key_id}")

    # Verify signature logic
    raw_payload = b'{"entity":"event","event":"payment_link.paid"}'
    secret = SETTINGS.RAZORPAY_WEBHOOK_SECRET or "whsec_test_secret_123"
    valid_sig = hmac.new(secret.encode("utf-8"), raw_payload, hashlib.sha256).hexdigest()

    sig_ok = client.verify_webhook_signature(raw_payload, valid_sig, secret=secret)
    sig_bad = client.verify_webhook_signature(raw_payload, "invalid_signature", secret=secret)

    print(f"Valid signature accepted: {sig_ok}")
    print(f"Tampered signature rejected: {not sig_bad}")
    assert sig_ok is True
    assert sig_bad is False

    return {
        "key_id": key_id,
        "signature_verification": "PASSED",
        "tamper_resistance": "PASSED",
    }


def verify_broken_promise_scorer() -> dict[str, Any]:
    print("\n--- 1. Broken-Promise Risk Scorer ---")
    start = time.perf_counter()

    # 1. Low risk: reliable payer, zero broken promises, 2-day horizon
    r_low = httpx.post(f"{BASE_URL}/api/score/broken_promise", json={
        "customer_broken_promise_rate": 0.02,
        "customer_on_time_ratio_90d": 0.96,
        "days_overdue_at_scoring": 1,
        "promise_horizon_days": 2,
    })
    low_data = r_low.json()
    low_score = low_data["risk_score"]

    # 2. High risk: serial promise breaker, 20% on-time, 60 days overdue, 25-day horizon
    r_high = httpx.post(f"{BASE_URL}/api/score/broken_promise", json={
        "customer_broken_promise_rate": 0.85,
        "customer_on_time_ratio_90d": 0.20,
        "days_overdue_at_scoring": 60,
        "promise_horizon_days": 25,
    })
    high_data = r_high.json()
    high_score = high_data["risk_score"]

    duration_ms = (time.perf_counter() - start) * 1000

    print(f"Low risk sample:  P(Broken) = {low_score:.4f} -> Tier: {low_data['risk_tier']}")
    print(f"High risk sample: P(Broken) = {high_score:.4f} -> Tier: {high_data['risk_tier']}")
    print(f"Roundtrip inference latency: {duration_ms / 2:.2f} ms")

    assert low_score < 0.33, f"Low risk score {low_score} was not < 0.33"
    assert high_score > 0.66, f"High risk score {high_score} was not > 0.66"
    assert low_score < high_score, "Monotonicity failure"

    r_card = httpx.get(f"{BASE_URL}/api/score/broken_promise/card")
    card = r_card.json()
    auc = card["metrics"]["roc_auc"]
    acc = card["metrics"]["accuracy"]
    print(f"Model holdout ROC-AUC: {auc:.4f} | Accuracy: {acc:.2%}")

    return {
        "status": "HEALTHY",
        "low_risk_score": low_score,
        "high_risk_score": high_score,
        "roc_auc": auc,
        "accuracy": acc,
        "brier_score": card["metrics"]["brier_score"],
        "latency_ms": round(duration_ms / 2, 2),
    }


def verify_drift_detector() -> dict[str, Any]:
    print("\n--- 2. Payment-Behavior Drift Detector ---")
    r_flags = httpx.get(f"{BASE_URL}/api/v1/drift/flags?limit=10", headers=OPERATOR_HEADERS)
    flags_data = r_flags.json()
    flag_count = flags_data.get("count", 0)
    print(f"Recent drift flags recorded in DB: {flag_count}")

    r_card = httpx.get(f"{BASE_URL}/api/v1/models/drift/card")
    card = r_card.json()
    model_version = card.get("model_version")
    shipped = card.get("results", [{}])[0]
    lift = shipped.get("lift", 1.0)
    flagged_def_rate = shipped.get("flagged_default_rate", 0.0)
    unflagged_def_rate = shipped.get("unflagged_default_rate", 0.0)
    injected_recall = card.get("injected_drift", {}).get("recall", 0.0)

    print(f"Drift Model Version: {model_version}")
    print(f"Flagged vs Unflagged Default Lift: {lift:.2f}x ({flagged_def_rate:.1%} vs {unflagged_def_rate:.1%})")
    print(f"Injected Synthetic Drift Recall: {injected_recall:.1%}")

    assert lift > 2.0, f"Drift lift {lift} is below 2.0x threshold"

    return {
        "status": "HEALTHY",
        "model_version": model_version,
        "flags_evaluated": flag_count,
        "lift": round(lift, 2),
        "injected_drift_recall": round(injected_recall, 3),
        "flagged_default_rate": round(flagged_def_rate, 3),
        "unflagged_default_rate": round(unflagged_def_rate, 3),
    }


def verify_contact_timing_optimizer() -> dict[str, Any]:
    print("\n--- 3. Contact-Timing Optimizer ---")
    r_cust = httpx.get(f"{BASE_URL}/api/v1/invoices?page=1&page_size=1", headers=OPERATOR_HEADERS)
    cust_id = r_cust.json()["items"][0]["customer_id"]

    r_opt = httpx.get(f"{BASE_URL}/api/v1/schedule/next_time?customer_id={cust_id}")
    rec = r_opt.json()

    print(f"Optimal Send-Time for {cust_id} ({rec['segment']}):")
    print(f"  Recommended Arm: {rec['arm']} (Scheduled: {rec['scheduled_for']})")
    print(f"  Expected Response Rate: {rec['expected_response_rate']:.2%}")

    assert "arm" in rec
    assert rec["arm"] is not None
    assert rec["expected_response_rate"] > 0.0

    return {
        "status": "HEALTHY",
        "sample_customer": cust_id,
        "segment": rec["segment"],
        "recommended_arm": rec["arm"],
        "expected_response_rate": rec["expected_response_rate"],
        "scheduled_for": rec["scheduled_for"],
    }


def verify_cash_forecast() -> dict[str, Any]:
    print("\n--- 4. Receivables-Specific Cash Forecast ---")
    r_forecast = httpx.get(f"{BASE_URL}/api/v1/forecast/cash?draws=1000", headers=OPERATOR_HEADERS)
    fc = r_forecast.json()
    windows = fc.get("windows", [])
    print(f"Monte Carlo Forecast Windows evaluated: {len(windows)}")

    w7 = next((w for w in windows if w["window_days"] == 7), None)
    w30 = next((w for w in windows if w["window_days"] == 30), None)

    if w7:
        print(f"7-Day Expected Cash Recovery:  Rs. {w7['mean']:,.2f} (5th-95th percentile: Rs. {w7['p5']:,.2f} - Rs. {w7['p95']:,.2f})")
    if w30:
        print(f"30-Day Expected Cash Recovery: Rs. {w30['mean']:,.2f} (5th-95th percentile: Rs. {w30['p5']:,.2f} - Rs. {w30['p95']:,.2f})")

    r_card = httpx.get(f"{BASE_URL}/api/v1/forecast/cash/card", headers=OPERATOR_HEADERS)
    card = r_card.json()
    cov_30 = card.get("coverage", {}).get("30", 0.0)
    bias_30 = card.get("bias", {}).get("30", 0.0)
    print(f"Historical Calibration: 30-day CI Coverage = {cov_30:.1%}, Bias = {bias_30:+.1%}")

    assert w7 is not None and w30 is not None
    assert w7["mean"] <= w30["mean"], "7-day forecast cannot exceed 30-day cumulative recovery"
    assert w30["p5"] <= w30["median"] <= w30["p95"], "Percentiles violated monotonicity"

    return {
        "status": "HEALTHY",
        "window_7_mean_inr": w7["mean"],
        "window_7_range_inr": [w7["p5"], w7["p95"]],
        "window_30_mean_inr": w30["mean"],
        "window_30_range_inr": [w30["p5"], w30["p95"]],
        "ci_30_coverage": cov_30,
        "ci_30_bias": bias_30,
    }


def run_all() -> None:
    print("=================================================================")
    print("RECOUP ML MODELS VALIDATION & RAZORPAY VERIFICATION SUITE")
    print("=================================================================")

    report: dict[str, Any] = {}
    try:
        report["razorpay"] = verify_razorpay_gateway()
        report["broken_promise_scorer"] = verify_broken_promise_scorer()
        report["drift_detector"] = verify_drift_detector()
        report["contact_timing_optimizer"] = verify_contact_timing_optimizer()
        report["cash_forecast"] = verify_cash_forecast()
        report["overall_verdict"] = "ALL_MODELS_OPERATIONAL"
    except Exception as exc:
        report["overall_verdict"] = f"VALIDATION_FAILED: {exc}"
        raise exc

    print("\n=================================================================")
    print("FINAL SUMMARY REPORT:")
    print("=================================================================")
    print(json.dumps(report, indent=2))

    out_file = REPO_ROOT / "docs" / "ml_models_validation_report.json"
    out_file.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nReport written to: {out_file}")


if __name__ == "__main__":
    run_all()
