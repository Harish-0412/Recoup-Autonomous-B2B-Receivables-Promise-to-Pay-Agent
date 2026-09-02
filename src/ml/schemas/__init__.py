"""Shared ML schemas.

Import prediction contracts from here rather than from the individual modules,
so call sites stay stable if a schema moves.
"""

from src.ml.schemas.base import (
    FallbackReason,
    FallbackResolver,
    FallbackResult,
    PredictionEnvelope,
)
from src.ml.schemas.meta import ModelMetadata
from src.ml.schemas.recovery import FeatureDriver, RecoveryScorePrediction
from src.ml.schemas.reply import ExtractedEntities, IntentLabel, ReplyIntentPrediction

__all__ = [
    "ExtractedEntities",
    "FallbackReason",
    "FallbackResolver",
    "FallbackResult",
    "FeatureDriver",
    "IntentLabel",
    "ModelMetadata",
    "PredictionEnvelope",
    "RecoveryScorePrediction",
    "ReplyIntentPrediction",
]
