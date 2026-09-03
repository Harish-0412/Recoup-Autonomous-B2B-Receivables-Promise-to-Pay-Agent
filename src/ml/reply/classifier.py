"""Stage C: the trained reply-intent classifier.

TF-IDF plus a linear SVM, calibrated. Trains in seconds on CPU, and -- the
part that matters for this system -- stays interpretable: every prediction can
name the exact n-grams that drove it, which is what lets a classification go
into a Decision Trace next to the rules the policy engine applied.

Why this shape:

* **Word and character n-grams together.** Character n-grams carry most of the
  weight on Hinglish and SMS shorthand, where word features are sparse and
  misspelt ("pls snd inv copy", "mat bhejo"). Words carry the formal register.
* **LinearSVC wrapped in CalibratedClassifierCV.** A bare SVM has no
  ``predict_proba``, and the cascade needs a probability it can threshold on.
  Sigmoid calibration on cross-validation folds gives one that means something,
  which the calibration report then checks.

Sigmoid rather than isotonic, chosen on the validation split rather than by
taste. Isotonic had marginally the better expected calibration error (0.101 vs
0.115) and let the cascade keep far more traffic (71% vs 53% at a 0.6
threshold), but it was right on only 86.5% of what it kept, against sigmoid's
98%. In a system where a confident classification auto-creates a promise-to-pay
record without a human step, precision on kept traffic is worth more than
throughput: the traffic sigmoid gives up is not lost, it goes to the LLM.
"""

from collections.abc import Sequence
from pathlib import Path

import numpy as np
from pydantic import BaseModel, ConfigDict
from sklearn.calibration import CalibratedClassifierCV
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.pipeline import FeatureUnion, Pipeline
from sklearn.svm import LinearSVC

from src.ml.artifacts import load_artifact, save_artifact
from src.ml.config import MLSettings
from src.ml.schemas import IntentLabel, ModelMetadata
from src.ml.versioning import new_model_version, utc_now

MODEL_NAME = "tfidf-svm-intent"
FEATURE_VERSION = "word12-charwb35-v1"


class TermContribution(BaseModel):
    """One n-gram's contribution to a classification."""

    model_config = ConfigDict(frozen=True)

    term: str
    contribution: float


class IntentPrediction(BaseModel):
    """What the trained classifier returns for a single reply."""

    model_config = ConfigDict(protected_namespaces=())

    intent: IntentLabel
    confidence: float
    probabilities: dict[str, float]
    top_terms: list[TermContribution] = []

    def explanation(self) -> str:
        if not self.top_terms:
            return f"Classified as {self.intent.value}."
        terms = ", ".join(f"'{t.term}'" for t in self.top_terms)
        return f"Classified as {self.intent.value} on {terms}."


def build_pipeline(*, seed: int = 42, calibration_folds: int = 5) -> Pipeline:
    """Assemble the vectoriser and calibrated linear SVM."""

    features = FeatureUnion(
        [
            (
                "word",
                TfidfVectorizer(
                    analyzer="word",
                    ngram_range=(1, 2),
                    sublinear_tf=True,
                    min_df=1,
                    lowercase=True,
                ),
            ),
            (
                "char",
                TfidfVectorizer(
                    analyzer="char_wb",
                    ngram_range=(3, 5),
                    sublinear_tf=True,
                    min_df=2,
                    lowercase=True,
                ),
            ),
        ]
    )
    estimator = CalibratedClassifierCV(
        LinearSVC(C=1.0, class_weight="balanced", random_state=seed),
        cv=calibration_folds,
        method="sigmoid",
    )
    return Pipeline([("features", features), ("classifier", estimator)])


class ReplyIntentClassifier:
    """A fitted TF-IDF + calibrated linear SVM intent classifier."""

    def __init__(self, pipeline: Pipeline, *, feature_version: str = FEATURE_VERSION) -> None:
        self.pipeline = pipeline
        self.feature_version = feature_version
        self._feature_names: np.ndarray | None = None
        self._coefficients: np.ndarray | None = None

    # --- training ---------------------------------------------------------

    @classmethod
    def fit(
        cls,
        texts: Sequence[str],
        labels: Sequence[str],
        *,
        seed: int = 42,
        calibration_folds: int = 5,
    ) -> "ReplyIntentClassifier":
        if len(texts) != len(labels):
            raise ValueError("texts and labels must be the same length")
        if not texts:
            raise ValueError("cannot fit on an empty corpus")

        # Calibration needs at least `cv` members of the rarest class.
        rarest = min(labels.count(label) for label in set(labels))
        folds = max(2, min(calibration_folds, rarest))

        pipeline = build_pipeline(seed=seed, calibration_folds=folds)
        pipeline.fit(list(texts), list(labels))

        model = cls(pipeline)
        model._cache_explanation_weights()
        return model

    def _cache_explanation_weights(self) -> None:
        """Average the SVM coefficients across calibration folds.

        ``CalibratedClassifierCV`` fits one estimator per fold; averaging their
        coefficients gives a single stable weight per (class, n-gram) to build
        explanations from. If the internals ever change shape, explanations
        degrade to empty rather than raising.
        """

        try:
            features: FeatureUnion = self.pipeline.named_steps["features"]
            calibrated: CalibratedClassifierCV = self.pipeline.named_steps["classifier"]
            coefficient_stack = [
                inner.estimator.coef_ for inner in calibrated.calibrated_classifiers_
            ]
            self._coefficients = np.mean(np.stack(coefficient_stack), axis=0)
            self._feature_names = np.asarray(features.get_feature_names_out())
        except Exception:  # explanations are a nicety, never a failure mode
            self._coefficients = None
            self._feature_names = None

    # --- inference --------------------------------------------------------

    @property
    def classes(self) -> list[str]:
        return [str(c) for c in self.pipeline.named_steps["classifier"].classes_]

    def predict_proba(self, texts: Sequence[str]) -> np.ndarray:
        return np.asarray(self.pipeline.predict_proba(list(texts)))

    def predict_labels(self, texts: Sequence[str]) -> list[str]:
        return [str(label) for label in self.pipeline.predict(list(texts))]

    def top_terms(self, text: str, intent: str, *, limit: int = 3) -> list[TermContribution]:
        """The n-grams that pushed this text towards ``intent``.

        Contribution is the TF-IDF weight of a term times that term's learned
        coefficient for the predicted class -- so it names words actually
        present in this reply, not just globally important ones.
        """

        if self._coefficients is None or self._feature_names is None:
            return []

        try:
            classes = self.classes
            row = self.pipeline.named_steps["features"].transform([text])
            if self._coefficients.shape[0] == 1:
                # Binary problem: one coefficient row, sign depends on the class.
                weights = self._coefficients[0]
                if classes.index(intent) == 0:
                    weights = -weights
            else:
                weights = self._coefficients[classes.index(intent)]

            coo = row.tocoo()
            scored = [
                (str(self._feature_names[column]), float(value * weights[column]))
                for column, value in zip(coo.col, coo.data, strict=False)
            ]
            scored = [item for item in scored if item[1] > 0]
            scored.sort(key=lambda item: item[1], reverse=True)

            # Word n-grams read as an explanation a person can check; character
            # n-grams are what actually carries Hinglish and shorthand. Show
            # words first and fall back to characters only to fill the quota.
            word_terms = [item for item in scored if item[0].startswith("word__")]
            char_terms = [item for item in scored if item[0].startswith("char__")]
            ordered = word_terms + char_terms or scored

            seen: set[str] = set()
            results: list[TermContribution] = []
            for term, contribution in ordered:
                cleaned = term.split("__", 1)[-1].strip()
                if not cleaned or cleaned in seen:
                    continue
                seen.add(cleaned)
                results.append(TermContribution(term=cleaned, contribution=round(contribution, 6)))
                if len(results) >= limit:
                    break
            return results
        except Exception:
            return []

    def predict_one(self, text: str, *, top_k_terms: int = 3) -> IntentPrediction:
        probabilities = self.predict_proba([text])[0]
        classes = self.classes
        best_index = int(np.argmax(probabilities))
        best_label = classes[best_index]

        return IntentPrediction(
            intent=IntentLabel(best_label),
            confidence=float(probabilities[best_index]),
            probabilities={
                label: round(float(p), 6) for label, p in zip(classes, probabilities, strict=False)
            },
            top_terms=self.top_terms(text, best_label, limit=top_k_terms),
        )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

_CACHE: dict[tuple[str, str], tuple[ReplyIntentClassifier, ModelMetadata]] = {}


def save_classifier(
    model: ReplyIntentClassifier,
    *,
    train_rows: int,
    metrics: dict[str, float] | None = None,
    notes: str | None = None,
    version: str | None = None,
    settings: MLSettings | None = None,
) -> tuple[Path, ModelMetadata]:
    """Persist the classifier and its metadata to the versioned artifact store."""

    resolved_version = version or new_model_version(MODEL_NAME)
    metadata = ModelMetadata(
        model_name=MODEL_NAME,
        model_version=resolved_version,
        trained_at=utc_now(),
        train_rows=train_rows,
        feature_columns=[model.feature_version],
        metrics=metrics or {},
        notes=notes,
    )
    path = save_artifact(
        model,
        name=MODEL_NAME,
        version=resolved_version,
        metadata=metadata,
        settings=settings,
    )
    _CACHE.clear()
    return path, metadata


def load_classifier(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
    use_cache: bool = True,
) -> tuple[ReplyIntentClassifier, ModelMetadata]:
    """Load a trained classifier, caching it so the cascade does not reload per reply."""

    key = (MODEL_NAME, version)
    if use_cache and key in _CACHE:
        return _CACHE[key]

    model, metadata = load_artifact(MODEL_NAME, version=version, settings=settings)
    if not isinstance(model, ReplyIntentClassifier):
        raise TypeError(f"Artifact {MODEL_NAME}:{version} is not a ReplyIntentClassifier")

    if use_cache:
        _CACHE[key] = (model, metadata)
    return model, metadata


def clear_classifier_cache() -> None:
    _CACHE.clear()


def classifier_is_available(
    version: str = "latest",
    *,
    settings: MLSettings | None = None,
) -> bool:
    """Whether a trained artifact exists, without raising if it does not."""

    try:
        load_classifier(version, settings=settings)
    except Exception:
        return False
    return True
