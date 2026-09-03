"""Reply understanding: raw customer text in, structured intent out.

The feature is a three-stage cascade:

* **Stage A** (``llm_baseline``) -- LLM zero-shot. Works on day one, no
  training data. Now the fallback rather than the primary.
* **Stage B** (``dataset``) -- the label-first labelled corpus.
* **Stage C** (``classifier``) -- TF-IDF + calibrated linear SVM, trained on
  Stage B. Fast, interpretable, and the primary classifier.

``cascade`` routes between C and A on calibrated confidence; ``service`` is the
entry point that adds deterministic entity extraction, intent gating and the
binding opt-out guard on top.
"""

from src.ml.reply.cascade import classify_reply_cascade, install_cascade_classifier
from src.ml.reply.classifier import (
    MODEL_NAME,
    IntentPrediction,
    ReplyIntentClassifier,
    classifier_is_available,
    clear_classifier_cache,
    load_classifier,
    save_classifier,
)
from src.ml.reply.dataset import (
    ALL_TEMPLATES,
    DATASET_VERSION,
    LabelledReply,
    ReplyCorpus,
    SplitName,
    audit_corpus,
    build_corpus,
    corpus_texts_and_labels,
)
from src.ml.reply.entity_extraction import (
    extract_amount,
    extract_date,
    extract_dispute_reason,
    extract_entities,
    normalize_currency,
)
from src.ml.reply.evaluation import (
    calibration_report,
    cascade_report,
    classification_report,
    entity_report,
)
from src.ml.reply.llm_baseline import ReplyIntentLLMOutput, classify_reply_llm
from src.ml.reply.prompts import PROMPT_VERSION, build_classification_prompt
from src.ml.reply.service import (
    ENTITY_BEARING_INTENTS,
    looks_like_opt_out,
    set_primary_classifier,
    understand_reply,
    understand_reply_sync,
)

__all__ = [
    "ALL_TEMPLATES",
    "DATASET_VERSION",
    "ENTITY_BEARING_INTENTS",
    "MODEL_NAME",
    "PROMPT_VERSION",
    "IntentPrediction",
    "LabelledReply",
    "ReplyCorpus",
    "ReplyIntentClassifier",
    "ReplyIntentLLMOutput",
    "SplitName",
    "audit_corpus",
    "build_classification_prompt",
    "build_corpus",
    "calibration_report",
    "cascade_report",
    "classification_report",
    "classifier_is_available",
    "classify_reply_cascade",
    "classify_reply_llm",
    "clear_classifier_cache",
    "corpus_texts_and_labels",
    "entity_report",
    "extract_amount",
    "extract_date",
    "extract_dispute_reason",
    "extract_entities",
    "install_cascade_classifier",
    "load_classifier",
    "looks_like_opt_out",
    "normalize_currency",
    "save_classifier",
    "set_primary_classifier",
    "understand_reply",
    "understand_reply_sync",
]
