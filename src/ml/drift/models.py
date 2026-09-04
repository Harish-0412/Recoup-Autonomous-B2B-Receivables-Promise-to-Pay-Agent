"""The drift model: an Isolation Forest over behavioral features.

Isolation Forest fits the problem better than a classifier here because
there is no drift label to supervise on -- the data says who defaulted
next month, not whose behavior *changed*. The forest is fit on customers
who did not default (the working definition of "normal repayment
behavior"); at serving time a customer whose trailing window scores below
the calibrated threshold is behaving like nobody in the normal population,
and gets flagged for a human.

The threshold is a score cut, not a label: it is set on the training
normals so that ``contamination`` of them falls below it, then frozen into
the artifact. ``FittedDrift`` carries it, so serving never re-derives it
from live data (which would let a book-wide downturn move the goalposts).
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest

from src.ml.drift.features import FEATURE_COLUMNS

MODEL_NAME = "iforest-drift"
PRIMARY_NAME = "iforest-drift"


@dataclass(frozen=True)
class FittedDrift:
    """A fitted Isolation Forest plus everything serving needs."""

    name: str
    estimator: IsolationForest
    feature_columns: tuple[str, ...]
    contamination: float
    #: Cut on ``decision_function``: scores strictly below this are flags.
    threshold: float
    #: Median feature vector of the training normals, for driver explanations.
    reference_median: tuple[float, ...]

    def anomaly_score(self, features: pd.DataFrame) -> np.ndarray:
        """Higher means more normal; the threshold cuts below."""

        frame = _as_frame(features, self.feature_columns)
        return np.asarray(self.estimator.decision_function(frame), dtype="float64")

    def is_flagged(self, features: pd.DataFrame) -> np.ndarray:
        """Boolean flag per row: True means behavior drift detected."""
        return self.anomaly_score(features) < self.threshold

    def score_one(self, features: dict[str, float]) -> float:
        frame = pd.DataFrame([features], columns=list(self.feature_columns)).astype("float64")
        return float(self.anomaly_score(frame)[0])


def _as_frame(features: pd.DataFrame | np.ndarray, columns: tuple[str, ...]) -> pd.DataFrame:
    if isinstance(features, pd.DataFrame):
        missing = [column for column in columns if column not in features.columns]
        if missing:
            raise ValueError(f"missing drift feature columns: {missing}")
        return features[list(columns)].astype("float64")
    array = np.asarray(features, dtype="float64")
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.shape[1] != len(columns):
        raise ValueError(f"expected {len(columns)} features, got {array.shape[1]}")
    return pd.DataFrame(array, columns=list(columns))


def build_iforest(
    *,
    contamination: float = 0.10,
    n_estimators: int = 200,
    seed: int = 42,
) -> IsolationForest:
    """An unfitted Isolation Forest with the shipped hyperparameters."""
    return IsolationForest(
        n_estimators=n_estimators,
        contamination=contamination,
        random_state=seed,
    )


def fit_drift_model(
    normals: pd.DataFrame,
    *,
    contamination: float = 0.10,
    n_estimators: int = 200,
    seed: int = 42,
) -> FittedDrift:
    """Fit on normal-payer features; calibrate the threshold on the same rows."""

    frame = _as_frame(normals, FEATURE_COLUMNS)
    estimator = build_iforest(contamination=contamination, n_estimators=n_estimators, seed=seed)
    estimator.fit(frame)
    scores = np.asarray(estimator.decision_function(frame), dtype="float64")
    threshold = float(np.quantile(scores, contamination))
    median = tuple(float(frame[column].median()) for column in FEATURE_COLUMNS)
    return FittedDrift(
        name=PRIMARY_NAME,
        estimator=estimator,
        feature_columns=FEATURE_COLUMNS,
        contamination=contamination,
        threshold=threshold,
        reference_median=median,
    )
