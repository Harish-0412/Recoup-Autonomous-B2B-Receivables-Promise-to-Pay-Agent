"""The recovery-probability model: P(paid within the horizon).

Replaces the hand-written probability in the expected-value formula with a
trained, calibrated one, without giving up either explainability or the
fallback that keeps a batch running when a model misbehaves.

* ``dataset``    -- temporal split, mature labels only
* ``models``     -- logistic baseline, XGBoost primary, MLP comparison
* ``evaluation`` -- AUC/PR/F1, Brier, calibration, and the head-to-head
                    against the rules-based scorer it replaces
* ``explain``    -- SHAP drivers for the Decision Trace
* ``scorer``     -- the RecoveryScorer interface and its two implementations
"""

from src.ml.recovery.artifacts import (
    MODEL_NAME,
    clear_recovery_cache,
    load_recovery_model,
    recovery_model_is_available,
    save_recovery_model,
)
from src.ml.recovery.dataset import (
    RecoveryDataset,
    RecoverySplit,
    build_cases,
    build_dataset,
    features_frame,
)
from src.ml.recovery.evaluation import (
    RankingComparison,
    RecoveryMetrics,
    calibration_bins,
    compare_rankings,
    evaluate,
    rules_based_scores,
    value_at_risk_captured_at_k,
)
from src.ml.recovery.explain import RecoveryExplainer
from src.ml.recovery.models import (
    BASELINE_NAME,
    NEURAL_NAME,
    PRIMARY_NAME,
    FittedModel,
    build_gradient_boosting,
    build_logistic_regression,
    build_neural_network,
    train_model,
)
from src.ml.recovery.scorer import (
    ModelBasedScorer,
    RecoveryScorer,
    RulesBasedScorer,
    get_recovery_scorer,
)

__all__ = [
    "BASELINE_NAME",
    "MODEL_NAME",
    "NEURAL_NAME",
    "PRIMARY_NAME",
    "FittedModel",
    "ModelBasedScorer",
    "RankingComparison",
    "RecoveryDataset",
    "RecoveryExplainer",
    "RecoveryMetrics",
    "RecoveryScorer",
    "RecoverySplit",
    "RulesBasedScorer",
    "build_cases",
    "build_dataset",
    "build_gradient_boosting",
    "build_logistic_regression",
    "build_neural_network",
    "calibration_bins",
    "clear_recovery_cache",
    "compare_rankings",
    "evaluate",
    "features_frame",
    "get_recovery_scorer",
    "load_recovery_model",
    "value_at_risk_captured_at_k",
    "recovery_model_is_available",
    "rules_based_scores",
    "save_recovery_model",
    "train_model",
]
