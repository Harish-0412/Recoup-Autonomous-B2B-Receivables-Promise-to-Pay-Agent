"""SHAP attributions for the recovery model.

The rules-based scorer returns the contribution of every term because it *is*
a sum of terms. Replacing it with gradient-boosted trees must not lose that:
``top_drivers`` goes straight into the invoice's Decision Trace, so a
prioritisation decision stays inspectable after the model is learned rather
than authored.

SHAP is computed against the estimator inside the calibration wrapper, not the
wrapper itself. The calibrator is a monotone map applied to the output, so it
changes what the number is, not which features pushed it which way.

Which explainer is used follows whichever model actually won on held-out AUC:
``TreeExplainer`` for gradient boosting, ``LinearExplainer`` for a logistic
regression behind a scaler. Both paths have to work, because the model is
chosen on evidence rather than on which one is convenient to explain.
"""

from collections.abc import Sequence

import numpy as np
import pandas as pd

from src.ml.features.recovery_features import FEATURE_COLUMNS_V1
from src.ml.recovery.models import FittedModel, uncalibrated
from src.ml.schemas import FeatureDriver


class RecoveryExplainer:
    """Per-prediction and global SHAP attributions, with a safe fallback.

    Explanations are a contract, not a luxury -- but a failure to *explain* a
    score must never become a failure to *produce* one. If SHAP cannot run,
    every method here degrades to an empty list and the caller still returns a
    prediction.
    """

    def __init__(self, model: FittedModel, background: pd.DataFrame | None = None) -> None:
        self.model = model
        self.feature_columns = tuple(model.feature_columns)
        self._explainer = None
        self._transform = None
        self.kind = "none"
        self._build(background)

    def _build(self, background: pd.DataFrame | None) -> None:
        """Pick an explainer that suits the estimator that actually won.

        Which model ships is decided by held-out AUC, not by which one is
        easiest to explain -- so both paths have to work. Trees get
        ``TreeExplainer``; a linear model inside a scaling pipeline gets
        ``LinearExplainer`` on the final step, with the same scaling applied to
        the background data.
        """

        estimator = uncalibrated(self.model)

        try:
            import shap
        except Exception:
            return

        # Tree models first: exact and fast.
        try:
            self._explainer = shap.TreeExplainer(estimator)
            self.kind = "tree"
            return
        except Exception:
            self._explainer = None

        # Linear model, possibly behind a scaler.
        try:
            from sklearn.pipeline import Pipeline

            final = estimator
            transform = None
            if isinstance(estimator, Pipeline):
                final = estimator.steps[-1][1]
                transform = estimator[:-1]

            if not hasattr(final, "coef_"):
                return

            reference = background if background is not None else self.model.background
            if reference is None:
                # A zero row is a valid, if blunt, reference point; a caller
                # with training data should pass it for a better baseline.
                reference = pd.DataFrame(
                    np.zeros((1, len(self.feature_columns))),
                    columns=list(self.feature_columns),
                )
            matrix = (
                transform.transform(reference[list(self.feature_columns)])
                if transform is not None
                else reference[list(self.feature_columns)].to_numpy()
            )

            self._explainer = shap.LinearExplainer(final, np.asarray(matrix))
            self._transform = transform
            self.kind = "linear"
        except Exception:
            self._explainer = None
            self._transform = None
            self.kind = "none"

    @property
    def available(self) -> bool:
        return self._explainer is not None

    def _shap_values(self, frame: pd.DataFrame) -> np.ndarray | None:
        if self._explainer is None:
            return None
        try:
            ordered = frame[list(self.feature_columns)]
            matrix = self._transform.transform(ordered) if self._transform is not None else ordered
            values = self._explainer.shap_values(matrix)
            array = np.asarray(values)
            if array.ndim == 3:
                # (rows, features, classes) -- take the positive class.
                array = array[:, :, -1]
            return array
        except Exception:
            return None

    def top_drivers(
        self,
        features: dict[str, float],
        *,
        limit: int = 3,
    ) -> list[FeatureDriver]:
        """The features that moved this single prediction furthest."""

        frame = pd.DataFrame([features], columns=list(self.feature_columns)).astype("float64")
        values = self._shap_values(frame)
        if values is None:
            return []

        row = values[0]
        ranked = sorted(
            (
                FeatureDriver(
                    feature=column,
                    value=round(float(features[column]), 4),
                    shap_contribution=round(float(row[index]), 6),
                )
                for index, column in enumerate(self.feature_columns)
            ),
            key=lambda driver: abs(driver.shap_contribution),
            reverse=True,
        )
        return ranked[:limit]

    def global_importance(self, frame: pd.DataFrame, *, limit: int = 15) -> list[tuple[str, float]]:
        """Mean absolute SHAP value per feature: the global ranking."""

        values = self._shap_values(frame)
        if values is None:
            return []

        magnitudes = np.abs(values).mean(axis=0)
        ranked = sorted(
            zip(self.feature_columns, magnitudes, strict=False),
            key=lambda item: item[1],
            reverse=True,
        )
        return [(name, round(float(value), 6)) for name, value in ranked[:limit]]


def format_global_importance(
    importances: Sequence[tuple[str, float]],
    *,
    width: int = 40,
) -> str:
    """Render the global ranking as a text bar chart."""

    if not importances:
        return "(SHAP unavailable for this model)"

    largest = max(value for _, value in importances) or 1.0
    lines = []
    for name, value in importances:
        bar = "#" * max(1, int(round(width * value / largest)))
        lines.append(f"{name:<42}{value:>9.4f}  {bar}")
    return "\n".join(lines)


def default_feature_columns() -> tuple[str, ...]:
    return FEATURE_COLUMNS_V1
