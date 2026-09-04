"""The ``RecoveryScorer`` interface and its two implementations.

``RulesBasedScorer`` wraps the hand-written scorer that already ships;
``ModelBasedScorer`` wraps the trained model. A config flag selects between
them, and both return the same ``RecoveryScorePrediction``, so nothing
downstream branches on which one ran -- it reads ``model_version`` and
``top_drivers`` either way.

The fallback is the point. If the artifact will not load, if the model throws,
or if it returns a NaN or an out-of-range probability, the failure is recorded
on the prediction and that invoice is scored by the rules instead. One bad
model does not stop a batch, and the batch report can still say exactly which
invoices were scored by what.
"""

import math
from datetime import date
from typing import Protocol, runtime_checkable

from app.core.domain import CaseSnapshot
from src.ml.config import MLSettings
from src.ml.features.recovery_features import FEATURE_SET_VERSION, build_recovery_features
from src.ml.recovery.artifacts import load_recovery_model
from src.ml.recovery.explain import RecoveryExplainer
from src.ml.recovery.models import FittedModel
from src.ml.schemas import (
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    RecoveryScorePrediction,
)
from src.ml.versioning import utc_now


@runtime_checkable
class RecoveryScorer(Protocol):
    """Anything that can put a calibrated recovery probability on an invoice."""

    def score(self, case: CaseSnapshot, as_of: date | None = None) -> RecoveryScorePrediction: ...


class RulesBasedScorer:
    """The incumbent: the hand-written logistic in ``app.core.scorer``.

    Always reports ``fallback_used=True`` with ``resolved_by=RULES_BASED_SCORER``
    so a hand-tuned score can never be mistaken for a fitted one downstream.
    """

    name = "rules-based"

    def score(self, case: CaseSnapshot, as_of: date | None = None) -> RecoveryScorePrediction:
        from app.core.scorer import SCORER_VERSION, estimate_recovery_probability

        probability, drivers = estimate_recovery_probability(case)
        return RecoveryScorePrediction(
            invoice_id=case.invoice.invoice_id,
            p_recovery_30d=round(float(probability), 6),
            calibrated=False,
            top_drivers=drivers[:3],
            model_version=SCORER_VERSION,
            confidence=round(abs(probability - 0.5) * 2.0, 6),
            fallback_used=True,
            fallback=FallbackResult(
                triggered=True,
                reason=FallbackReason.MODEL_LOAD_FAILED,
                resolved_by=FallbackResolver.RULES_BASED_SCORER,
            ),
            scored_at=utc_now(),
        )


class ModelBasedScorer:
    """The trained, calibrated recovery model, with the rules underneath it."""

    name = "model-based"

    def __init__(
        self,
        model: FittedModel | None = None,
        *,
        model_version: str = "latest",
        settings: MLSettings | None = None,
        explainer: RecoveryExplainer | None = None,
        fallback: RecoveryScorer | None = None,
    ) -> None:
        self.settings = settings or MLSettings()
        self.fallback = fallback or RulesBasedScorer()
        self._load_error: str | None = None

        if model is not None:
            self.model: FittedModel | None = model
            self.metadata = None
        else:
            try:
                self.model, self.metadata = load_recovery_model(
                    model_version, settings=self.settings
                )
            except Exception as exc:
                self.model = None
                self.metadata = None
                self._load_error = str(exc)

        self.explainer = explainer
        if self.explainer is None and self.model is not None:
            self.explainer = RecoveryExplainer(self.model)

    @property
    def is_available(self) -> bool:
        return self.model is not None

    def _degrade(
        self,
        case: CaseSnapshot,
        as_of: date | None,
        reason: FallbackReason,
        detail: str,
    ) -> RecoveryScorePrediction:
        prediction = self.fallback.score(case, as_of)
        return prediction.model_copy(
            update={
                "fallback_used": True,
                "fallback": FallbackResult(
                    triggered=True,
                    reason=reason,
                    resolved_by=FallbackResolver.RULES_BASED_SCORER,
                ),
                "model_version": f"{prediction.model_version} (after {detail})",
            }
        )

    def score(self, case: CaseSnapshot, as_of: date | None = None) -> RecoveryScorePrediction:
        """Score one invoice, falling back to the rules on any failure."""

        if self.model is None:
            return self._degrade(
                case,
                as_of,
                FallbackReason.MODEL_LOAD_FAILED,
                f"model load failed: {self._load_error}",
            )

        try:
            features = build_recovery_features(case, as_of)
            probability = self.model.predict_one(features)
        except Exception as exc:
            return self._degrade(
                case, as_of, FallbackReason.UNEXPECTED_ERROR, f"scoring raised: {exc}"
            )

        if not math.isfinite(probability) or not 0.0 <= probability <= 1.0:
            return self._degrade(
                case,
                as_of,
                FallbackReason.INVALID_OUTPUT,
                f"model returned {probability!r}",
            )

        drivers = []
        if self.explainer is not None:
            drivers = self.explainer.top_drivers(features, limit=3)

        version = self.metadata.model_version if self.metadata is not None else self.model.name

        return RecoveryScorePrediction(
            invoice_id=case.invoice.invoice_id,
            p_recovery_30d=round(probability, 6),
            calibrated=self.model.calibrated,
            top_drivers=drivers,
            model_version=version,
            # Distance from the coin-flip: a 0.95 or a 0.05 is a confident read,
            # a 0.5 is the model declining to commit.
            confidence=round(abs(probability - 0.5) * 2.0, 6),
            fallback_used=False,
            fallback=None,
            scored_at=utc_now(),
        )


#: Marker written into artifact ``notes`` by the trainer when the labels
#: came from the allocation-settled warehouse ETL. Only an artifact carrying
#: this marker may serve traffic -- a synthetic-trained model is CI evidence,
#: not a production scorer.
LIVE_LABEL_MARKER = "labels:live-webhooks"


def live_recovery_artifact_available(
    settings: MLSettings | None = None,
) -> bool:
    """Whether a live-webhooks recovery artifact exists, without unpickling it."""

    import json

    from src.ml.artifacts import LATEST_FILENAME
    from src.ml.recovery.artifacts import MODEL_NAME

    active_settings = settings or MLSettings()
    try:
        latest_path = active_settings.ml_artifacts_dir / MODEL_NAME / LATEST_FILENAME
        if not latest_path.exists():
            return False
        version = latest_path.read_text(encoding="utf-8").strip()
        meta_path = active_settings.ml_artifacts_dir / MODEL_NAME / version / "meta.json"
        metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        notes = metadata.get("notes") or ""
        return LIVE_LABEL_MARKER in notes
    except Exception:
        return False


def get_recovery_scorer(
    *,
    use_model: bool | None = None,
    settings: MLSettings | None = None,
) -> RecoveryScorer:
    """Select a scorer by config, degrading to the rules if the model is absent.

    ``use_model`` overrides the configured flag, which is what the tests and
    the batch demo use to force one path or the other.

    Wave 6: the flag alone is not enough. A model serves traffic only when a
    live-webhooks artifact exists -- i.e. xgb beat the rules on a real holdout
    and the trainer saved it. Until then ``USE_MODEL_SCORER`` stays
    effectively off and the rules decide, whatever the environment says.
    """

    active_settings = settings or MLSettings()
    enabled = active_settings.use_model_scorer if use_model is None else use_model

    if not enabled:
        return RulesBasedScorer()

    scorer = ModelBasedScorer(settings=active_settings)
    if not scorer.is_available:
        # Nothing has been trained yet: say so by returning the rules directly
        # rather than a model scorer that degrades on every single invoice.
        return RulesBasedScorer()
    if not live_recovery_artifact_available(active_settings):
        # A synthetic-trained artifact is CI evidence, not a production
        # scorer. Stay on the rules until the live card exists.
        return RulesBasedScorer()
    return scorer


def feature_set_version() -> str:
    return FEATURE_SET_VERSION
