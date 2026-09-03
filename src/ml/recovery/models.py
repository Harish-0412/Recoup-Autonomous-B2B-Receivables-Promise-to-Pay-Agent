"""The recovery models: a linear baseline, gradient boosting, and a small net.

Three models on the same features and the same splits, so the comparison means
something:

* **Logistic regression** -- the baseline that must always be trained. If the
  gradient-boosted model cannot beat it, that is a finding to report, not an
  embarrassment to hide: it says the signal in these features is essentially
  linear and the simpler model should ship.
* **XGBoost** -- the right default for tabular data at this scale. Handles the
  interactions a linear model cannot, trains in seconds on CPU, and supports
  SHAP for per-prediction explanations.
* **A small MLP** -- trained for the honest comparison, not because it is
  expected to win. On a few thousand rows of tabular data a neural network
  usually loses to gradient boosting, and reporting that with numbers is a
  better answer than reaching for the biggest model available.

Every model is wrapped in probability calibration fitted on the *validation*
split, never on training data. The output is multiplied straight into a rupee
figure by the expected-value formula, so a probability that is merely
well-ranked is not good enough -- 0.7 has to mean 0.7.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING

import numpy as np
import pandas as pd
from sklearn.calibration import CalibratedClassifierCV
from sklearn.linear_model import LogisticRegression
from sklearn.neural_network import MLPClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from src.ml.features.recovery_features import FEATURE_COLUMNS_V1

if TYPE_CHECKING:  # pragma: no cover - annotation only
    from xgboost import XGBClassifier

BASELINE_NAME = "logreg-recovery"
PRIMARY_NAME = "xgb-recovery"
NEURAL_NAME = "mlp-recovery"


@dataclass(frozen=True)
class FittedModel:
    """A trained, calibrated estimator with the metadata to serve it."""

    name: str
    estimator: object
    feature_columns: tuple[str, ...]
    calibrated: bool
    calibration_method: str | None
    #: A sample of training rows, saved with the model so SHAP has a real
    #: reference distribution at inference time. Without it a linear
    #: explanation is measured against an all-zeros row, which is not a
    #: customer that exists and makes every attribution look enormous.
    background: pd.DataFrame | None = None

    def predict_proba(self, features: pd.DataFrame | np.ndarray) -> np.ndarray:
        """P(recovered within horizon) for each row."""

        frame = _as_frame(features, self.feature_columns)
        probabilities = self.estimator.predict_proba(frame)  # type: ignore[attr-defined]
        return np.asarray(probabilities)[:, 1]

    def predict_one(self, features: dict[str, float]) -> float:
        frame = pd.DataFrame([features], columns=list(self.feature_columns)).astype("float64")
        return float(self.predict_proba(frame)[0])


def _as_frame(
    features: pd.DataFrame | np.ndarray,
    columns: Sequence[str],
) -> pd.DataFrame:
    if isinstance(features, pd.DataFrame):
        missing = [column for column in columns if column not in features.columns]
        if missing:
            raise ValueError(f"missing feature columns: {missing}")
        return features[list(columns)].astype("float64")
    array = np.asarray(features, dtype="float64")
    if array.ndim == 1:
        array = array.reshape(1, -1)
    if array.shape[1] != len(columns):
        raise ValueError(f"expected {len(columns)} features, got {array.shape[1]}")
    return pd.DataFrame(array, columns=list(columns))


def build_logistic_regression(seed: int = 42) -> Pipeline:
    """Scaled logistic regression -- the baseline every run must beat."""

    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                LogisticRegression(
                    max_iter=2000,
                    C=1.0,
                    class_weight="balanced",
                    random_state=seed,
                ),
            ),
        ]
    )


def build_gradient_boosting(seed: int = 42, *, n_estimators: int = 400) -> "XGBClassifier":
    """XGBoost, tuned conservatively for a few thousand rows.

    Shallow trees and a low learning rate: the dataset is small enough that a
    deep forest would memorise the training slice and lose on the temporal
    test split, which is exactly the failure the split exists to expose.
    """

    from xgboost import XGBClassifier

    return XGBClassifier(
        n_estimators=n_estimators,
        max_depth=4,
        learning_rate=0.05,
        subsample=0.85,
        colsample_bytree=0.85,
        min_child_weight=5,
        reg_lambda=1.5,
        objective="binary:logistic",
        eval_metric="logloss",
        random_state=seed,
        n_jobs=1,
        tree_method="hist",
    )


def build_neural_network(seed: int = 42) -> Pipeline:
    """A small feedforward net, for the honest architecture comparison."""

    return Pipeline(
        [
            ("scale", StandardScaler()),
            (
                "model",
                MLPClassifier(
                    hidden_layer_sizes=(64, 32),
                    activation="relu",
                    alpha=1e-3,
                    learning_rate_init=1e-3,
                    max_iter=600,
                    early_stopping=True,
                    n_iter_no_change=20,
                    random_state=seed,
                ),
            ),
        ]
    )


def calibrate(
    estimator: object,
    x_validation: pd.DataFrame,
    y_validation: np.ndarray,
    *,
    method: str = "isotonic",
) -> CalibratedClassifierCV:
    """Fit probability calibration on held-out validation data.

    Freezing the estimator matters: it is already trained on the training
    split, and the calibrator must be fitted on validation data it has never
    seen. Calibrating on training data produces a confident, wrong mapping.

    scikit-learn 1.6 replaced ``cv="prefit"`` with ``FrozenEstimator``; both
    spellings are supported here because pyproject allows 1.4 and up.
    """

    if method not in {"isotonic", "sigmoid"}:
        raise ValueError("method must be 'isotonic' or 'sigmoid'")

    try:
        from sklearn.frozen import FrozenEstimator

        calibrator = CalibratedClassifierCV(FrozenEstimator(estimator), method=method)
    except ImportError:  # scikit-learn < 1.6
        calibrator = CalibratedClassifierCV(estimator, method=method, cv="prefit")

    calibrator.fit(x_validation, y_validation)
    return calibrator


def train_model(
    name: str,
    estimator: object,
    x_train: pd.DataFrame,
    y_train: np.ndarray,
    *,
    x_validation: pd.DataFrame | None = None,
    y_validation: np.ndarray | None = None,
    calibration_method: str | None = "isotonic",
    feature_columns: tuple[str, ...] = FEATURE_COLUMNS_V1,
    background_rows: int = 200,
) -> FittedModel:
    """Fit an estimator and, when validation data is supplied, calibrate it."""

    estimator.fit(x_train[list(feature_columns)], y_train)  # type: ignore[attr-defined]

    # A capped sample keeps the artifact small while still being a real
    # reference distribution rather than a synthetic origin point.
    background = x_train[list(feature_columns)].head(background_rows).copy()

    if calibration_method and x_validation is not None and y_validation is not None:
        calibrated = calibrate(
            estimator,
            x_validation[list(feature_columns)],
            y_validation,
            method=calibration_method,
        )
        return FittedModel(
            name=name,
            estimator=calibrated,
            feature_columns=feature_columns,
            calibrated=True,
            calibration_method=calibration_method,
            background=background,
        )

    return FittedModel(
        name=name,
        estimator=estimator,
        feature_columns=feature_columns,
        calibrated=False,
        calibration_method=None,
        background=background,
    )


def uncalibrated(model: FittedModel) -> object:
    """The raw estimator inside a calibrated wrapper.

    SHAP needs the tree model itself, not the calibration layer wrapped around
    it. The attribution is computed on the ranking the trees produce; the
    calibrator is a monotone map applied afterwards and does not change which
    feature pushed a prediction which way.
    """

    estimator = model.estimator
    if isinstance(estimator, CalibratedClassifierCV):
        calibrated_list = getattr(estimator, "calibrated_classifiers_", [])
        if calibrated_list:
            estimator = getattr(calibrated_list[0], "estimator", estimator)
        else:
            estimator = getattr(estimator, "estimator", estimator)

    # scikit-learn >= 1.6 wraps a prefit estimator in FrozenEstimator; peel it
    # so SHAP sees the pipeline rather than the freezing shim.
    inner = getattr(estimator, "estimator", None)
    if inner is not None and type(estimator).__name__ == "FrozenEstimator":
        estimator = inner
    return estimator
