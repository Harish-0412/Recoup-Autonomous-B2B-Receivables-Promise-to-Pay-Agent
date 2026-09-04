"""Honest evaluation for an unsupervised detector.

There are no drift labels, so "accuracy" is not on the menu. Three checks
that actually mean something:

* **Flag rate** on holdout normals should sit near ``contamination`` -- a
  detector that flags 40% of normal payers is a pager that never stops.
* **Proxy precision**: flagged holdout customers should default next month
  materially more often than unflagged ones. The default flag is a proxy,
  not ground truth, and is reported as such.
* **Injected-drift recall**: take holdout customers the model calls normal,
  degrade their recent periods (see ``degrade_series``), and measure how
  many flip to flagged. This is the only check with real ground truth,
  because the degradation is applied by the test itself.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from src.ml.drift.dataset import build_feature_frame, degrade_series, row_to_series
from src.ml.drift.models import FittedDrift


def flag_rate(model: FittedDrift, features: pd.DataFrame) -> float:
    """Fraction of rows scoring below the frozen threshold."""
    if len(features) == 0:
        return 0.0
    return float(np.mean(model.is_flagged(features)))


def proxy_precision(
    model: FittedDrift, features: pd.DataFrame, proxy: pd.Series
) -> dict[str, float]:
    """Default-next-month rate among flagged vs unflagged holdout rows."""

    flags = model.is_flagged(features)
    proxy_values = np.asarray(proxy, dtype="float64")
    flagged_rate = float(proxy_values[flags].mean()) if bool(flags.any()) else 0.0
    unflagged_rate = float(proxy_values[~flags].mean()) if bool((~flags).any()) else 0.0
    return {
        "flagged_default_rate": flagged_rate,
        "unflagged_default_rate": unflagged_rate,
        "lift": (flagged_rate / unflagged_rate) if unflagged_rate > 0 else 0.0,
        "flagged_count": float(int(flags.sum())),
    }


def injected_drift_recall(
    model: FittedDrift,
    source_rows: pd.DataFrame,
    *,
    max_customers: int = 2000,
    seed: int = 42,
) -> dict[str, float]:
    """Recall on customers degraded on purpose.

    Only customers the model currently calls *normal* are degraded, so the
    denominator is "should newly flag" and the metric cannot be inflated by
    customers that were already flagged.
    """

    features = build_feature_frame(source_rows)
    normal_mask = np.asarray(model.is_flagged(features), dtype=bool)
    normal_idx = np.flatnonzero(~normal_mask)[:max_customers]
    if normal_idx.shape[0] == 0:
        return {"degraded": 0.0, "newly_flagged": 0.0, "recall": 0.0}

    degraded_rows = []
    for pos, (_, row) in enumerate(source_rows.iloc[normal_idx].iterrows()):
        degraded_rows.append(degrade_series(row_to_series(row), seed=seed + pos))
    # Rebuilt through the shared feature function to keep parity exact.
    from src.ml.drift.features import from_period_series

    degraded_features = pd.DataFrame(
        [from_period_series(d) for d in degraded_rows],
        columns=list(model.feature_columns),
    ).astype("float64")
    newly = model.is_flagged(degraded_features)
    return {
        "degraded": float(normal_idx.shape[0]),
        "newly_flagged": float(int(newly.sum())),
        "recall": float(np.mean(newly)) if normal_idx.shape[0] else 0.0,
    }
