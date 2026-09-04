"""Training script for Broken-Promise Risk Scorer.

Trains a Gradient Boosted Trees classifier (LightGBM) to predict whether a
customer will honor or break a promised payment commitment.
Evaluates model on a holdout test set, exports to ONNX for low-latency
production inference, and writes a comprehensive model_card.json.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import lightgbm as lgb
import numpy as np
import onnx
import onnxmltools
import onnxruntime as ort
import pandas as pd
from onnxmltools.convert.common.data_types import FloatTensorType
from sklearn.metrics import (
    accuracy_score,
    average_precision_score,
    brier_score_loss,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import train_test_split

REPO_ROOT = Path(__file__).resolve().parents[2]
DATA_PATH = REPO_ROOT / "data" / "broken_promise_training.csv"
MODEL_DIR = REPO_ROOT / "models" / "broken_promise"
ONNX_PATH = MODEL_DIR / "predictor.onnx"
TXT_PATH = MODEL_DIR / "lgbm_model.txt"
CARD_PATH = MODEL_DIR / "model_card.json"

FEATURE_COLUMNS = [
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
]


def train_and_export() -> dict:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)

    if not DATA_PATH.exists():
        print(f"Dataset not found at {DATA_PATH}. Running ETL first...")
        from scripts.etl.broken_promise_etl import run as run_etl
        run_etl()

    print(f"Loading training data from {DATA_PATH}...")
    df = pd.read_csv(DATA_PATH)
    print(f"Loaded {len(df)} rows. Features: {len(FEATURE_COLUMNS)}")

    X = df[FEATURE_COLUMNS].values.astype(np.float32)
    y = df["is_broken"].values.astype(int)

    X_train, X_test, y_train, y_test = train_test_split(
        X, y, test_size=0.20, random_state=42, stratify=y
    )

    print(f"Train size: {len(X_train)}, Test size: {len(X_test)}")
    print(f"Train positive rate (broken): {y_train.mean():.3f}, Test positive rate: {y_test.mean():.3f}")

    clf = lgb.LGBMClassifier(
        n_estimators=300,
        learning_rate=0.03,
        num_leaves=31,
        max_depth=6,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_samples=25,
        random_state=42,
        n_jobs=-1,
        verbose=-1,
    )

    print("Fitting LightGBM classifier...")
    clf.fit(X_train, y_train)

    # Predictions & probabilities
    y_prob = clf.predict_proba(X_test)[:, 1]
    y_pred = (y_prob >= 0.5).astype(int)

    # Metrics
    auc = float(roc_auc_score(y_test, y_prob))
    avg_prec = float(average_precision_score(y_test, y_prob))
    acc = float(accuracy_score(y_test, y_pred))
    prec = float(precision_score(y_test, y_pred))
    rec = float(recall_score(y_test, y_pred))
    f1 = float(f1_score(y_test, y_pred))
    brier = float(brier_score_loss(y_test, y_prob))

    print("\n--- Model Evaluation Results (Test Set) ---")
    print(f"ROC-AUC:            {auc:.4f}")
    print(f"Average Precision:  {avg_prec:.4f}")
    print(f"Accuracy:           {acc:.4f}")
    print(f"Precision:          {prec:.4f}")
    print(f"Recall:             {rec:.4f}")
    print(f"F1-Score:           {f1:.4f}")
    print(f"Brier Score:        {brier:.4f}")

    # Feature importances
    importances = clf.feature_importances_
    sorted_idx = np.argsort(importances)[::-1]
    importance_list = [
        {"feature": FEATURE_COLUMNS[i], "importance": int(importances[i])}
        for i in sorted_idx
    ]
    print("\nTop 5 Feature Importances:")
    for item in importance_list[:5]:
        print(f"  {item['feature']:<38} {item['importance']}")

    # Save native model using Python file IO to avoid Windows path issues
    model_str = clf.booster_.model_to_string()
    TXT_PATH.write_text(model_str, encoding="utf-8")
    print(f"\nSaved native LightGBM model to {TXT_PATH}")

    # Export to ONNX
    print("Exporting model to ONNX format...")
    initial_type = [("float_input", FloatTensorType([None, len(FEATURE_COLUMNS)]))]
    onnx_model = onnxmltools.convert_lightgbm(
        clf,
        initial_types=initial_type,
        target_opset=15,
    )
    onnx.save_model(onnx_model, str(ONNX_PATH))
    print(f"Saved ONNX model to {ONNX_PATH}")

    # Parity verification with ONNX Runtime
    print("Verifying ONNX runtime parity against LightGBM...")
    sess = ort.InferenceSession(str(ONNX_PATH))
    test_sample = X_test[:100]
    onnx_out = sess.run(None, {"float_input": test_sample})
    onnx_probs = np.array([p[1] for p in onnx_out[1]])
    lgb_probs = clf.predict_proba(test_sample)[:, 1]
    max_diff = float(np.max(np.abs(onnx_probs - lgb_probs)))
    print(f"Max absolute probability difference (ONNX vs LGBM): {max_diff:.6e}")
    assert max_diff < 1e-4, f"Parity check failed: max difference {max_diff} >= 1e-4"
    print("ONNX parity check PASSED!")

    # Model card metadata
    card_data = {
        "model_name": "broken_promise_risk_scorer",
        "model_version": "v1.0.0",
        "algorithm": "Gradient Boosted Trees (LightGBM)",
        "exported_format": "ONNX (opset 15)",
        "training_date": datetime.now(timezone.utc).isoformat(),
        "training_rows": len(X_train),
        "test_rows": len(X_test),
        "feature_count": len(FEATURE_COLUMNS),
        "features": FEATURE_COLUMNS,
        "metrics": {
            "roc_auc": round(auc, 4),
            "average_precision": round(avg_prec, 4),
            "accuracy": round(acc, 4),
            "precision": round(prec, 4),
            "recall": round(rec, 4),
            "f1_score": round(f1, 4),
            "brier_score": round(brier, 4),
        },
        "feature_importances": importance_list,
        "onnx_verified": True,
        "max_onnx_diff": max_diff,
    }

    with CARD_PATH.open("w", encoding="utf-8") as f:
        json.dump(card_data, f, indent=2)
    print(f"Saved model card metadata to {CARD_PATH}")

    return card_data


if __name__ == "__main__":
    train_and_export()
