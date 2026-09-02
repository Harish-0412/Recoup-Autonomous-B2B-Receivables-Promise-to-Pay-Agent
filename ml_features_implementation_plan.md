# ML/DL Implementation Plan — Recoup

*Covers two features: (1) Customer Reply Understanding (NLP intent extraction), (2) Recovery-Probability Model (replacing the rules-based scorer). Both are designed to slot into the existing "propose, then dispose" architecture without breaking it — every prediction below is a signal into the reasoning lane; the policy engine still has sole authority to act.*

---

## Shared engineering principles (apply to both models)

Get these right and both features read as genuine ML engineering, not a hackathon shortcut — get them wrong and a judge will find the crack immediately.

1. **No label leakage.** The synthetic data generator must produce features and outcomes from the *same* underlying archetype logic without ever exposing the archetype itself (or any variable derived only from the outcome) as a model input. If "reliable payer" is directly encoded as a feature the model can see, the model isn't learning anything — it's cheating.
2. **Event-time-aware splits, never random splits.** Train on invoices flagged before a cutoff date, test on invoices flagged after it. A random shuffle silently leaks future information into training and produces metrics that look great and mean nothing — this is the single most common mistake in this kind of project, and Track 02's own "held-out test set" language is explicitly about getting this right.
3. **Train/serve feature parity.** The exact same feature-engineering function must run at training time and at inference time (one shared module, imported by both the training script and the API). Two separate implementations of "days overdue" that drift apart is the classic silent-bug source.
4. **Every prediction is explainable, not just accurate.** Both models must emit a reason, not only a number — SHAP-based top drivers for the recovery model, and a confidence + evidence span for the intent classifier. This directly extends the Decision Trace pillar from the architecture doc; a model that can't explain itself doesn't belong in this system.
5. **Calibrated, not just ranked.** Both outputs feed downstream arithmetic (expected value, auto-update decisions) — a probability that's rank-ordered correctly but poorly calibrated (a model that says "90%" and is right 60% of the time) will quietly corrupt the EV formula and the batch evaluation report.
6. **Always have a deterministic fallback.** If a model fails to load, times out, or returns a malformed output, the system falls back to the original rules-based logic rather than blocking or guessing. This keeps the "bounded and gated" story true even when ML is in the loop.
7. **Version everything that produced a decision.** Every score carries a `model_version` string, logged into the Decision Trace, so a judge (or you, in six months) can answer "which model made this call" exactly.

---

## Feature 1 — Customer Reply Understanding (NLP Intent Extraction)

### Objective

Turn a raw, unstructured customer reply — email, SMS, or WhatsApp text — into a structured object the agent can act on automatically, replacing the manual work of a person reading every reply and deciding what it means.

### What exactly is being predicted

| Output | Type | Values |
|---|---|---|
| `intent` | multi-class classification | `PROMISE_TO_PAY`, `DISPUTE`, `OPT_OUT`, `NEGOTIATION_REQUEST`, `PARTIAL_PAYMENT_CLAIM`, `ALREADY_PAID_CLAIM`, `GENERAL_QUERY`, `OTHER` |
| `intent_confidence` | probability | 0–1, per predicted class |
| `promised_amount` | entity extraction | currency value or null |
| `promised_date` | entity extraction | normalized ISO date or null |
| `dispute_reason` | entity extraction (if `DISPUTE`) | short category or free text |

### Recommended approach: a cascade, not a single model

Build this in three stages — each stage is independently useful, so the feature works from day one and gets cheaper/faster/more defensible as you add stages.

**Stage A — LLM zero-shot baseline (build first, ships immediately).** Use the existing Groq/Gemini client with structured/JSON-mode output and a tightly specified schema and few-shot examples covering all eight intent classes. This is your working feature on day one — no training data needed, and it's what the demo can run on if later stages don't finish in time.

**Stage B — Synthetic labeled dataset (the ML rigor step).** Real B2B collection replies aren't available for a public demo, so generate them deliberately, *label-first*: instead of generating text and then guessing its label, generate text **from** a known label so the ground truth is correct by construction. For example, prompt the LLM with: *"Write a WhatsApp reply from a slightly annoyed Indian small-business customer, promising to pay ₹45,000 by next Tuesday, referencing a delivery delay as the reason for being late."* The intent (`PROMISE_TO_PAY`), amount (₹45,000), and date (next Tuesday → resolved to an ISO date at generation time) are all known before the text exists. Repeat across:
   - All 8 intent classes, with 15–20 phrasing/tone templates each (formal, curt, apologetic, annoyed, mixed English-Hinglish — realistic for Indian B2B correspondence).
   - Deliberate edge cases: multi-intent replies ("we'll pay, but this invoice has an error"), ambiguous replies, replies referencing the wrong invoice, sarcasm.
   - Target 800–1,500 labeled examples — enough to fine-tune a small model credibly.
   - **Manually spot-check ~10% of generated examples** before training and correct any mislabeled ones — state this step explicitly in your evaluation writeup; skipping it is the kind of shortcut a sharp judge will ask about directly.
   - Split 70/15/15 (train/val/test), stratified by intent class so rare classes like `OPT_OUT` aren't starved in any split.

**Stage C — Train a lightweight supervised classifier.** Two realistic options, pick based on remaining time:
   - **Fast path (recommended default):** TF-IDF vectorization + Logistic Regression or linear SVM via scikit-learn. Trains in seconds, fully interpretable (you can point at the exact words driving a classification), and a completely legitimate, defensible ML pipeline — don't feel obligated to reach for a transformer just because "DL" sounds more impressive.
   - **Stronger path (if time allows):** fine-tune a small transformer (DistilBERT or MiniLM) on the same dataset. Meaningfully better generalization to phrasing you didn't anticipate, at the cost of a training step needing more compute — Google Colab's free GPU tier is the free option here if local CPU training is too slow.

Deploy Stage C as the primary classifier. Route to Stage A (the LLM) only when Stage C's confidence falls below a threshold (e.g., 0.6) — a **cascade architecture**: the cheap, fast, fully-explainable model handles the common cases, the slower/costlier LLM handles the genuinely ambiguous ones. This is a legitimate advanced-architecture point to make in the discussion: most submissions pick one model; this one routes intelligently between two.

### Entity extraction — don't train a model for this

Dates and amounts are far more reliably extracted with deterministic tools than a trained NER model, especially at this scale:
- **Amounts:** a currency regex (`₹\s?[\d,]+(?:\.\d+)?`, plus written-number handling via a small library) — deterministic, doesn't need training data, and gets audited as easily as any other rule.
- **Dates:** the `dateparser` Python library resolves "next Tuesday," "Friday," "by the 15th" against the message's actual timestamp — also deterministic.
- Only fall back to LLM-based extraction when regex/dateparser both return null and the intent is one where an entity is expected (e.g., `PROMISE_TO_PAY` with no date found).

This keeps entity extraction fast, free, and — importantly — trivially explainable, which a trained NER model on a small dataset would not reliably be.

### Evaluation plan

- **Per-class precision, recall, F1** on the held-out test split — report all eight classes individually; an aggregate accuracy number alone will hide poor performance on rarer classes like `OPT_OUT`, which matters most for compliance.
- **Confusion matrix** — expect the most confusion between `NEGOTIATION_REQUEST` and `PROMISE_TO_PAY` (a customer saying "I'll pay if you drop the late fee" straddles both); document how you handle this.
- **Entity accuracy** — exact match for amounts; date match after normalization (a promised date resolved to the correct calendar date, not just "a date was found").
- **Calibration check** — bucket predictions by confidence and check whether "90% confidence" predictions are actually right ~90% of the time.
- **Cascade efficiency** — report what fraction of replies Stage C resolves directly vs. escalates to Stage A, and the latency/cost difference. This number is itself a nice piece of evidence for the architecture discussion.

### Output schema

```json
{
  "reply_id": "rpl_8f21ac",
  "invoice_id": "INV-1043",
  "raw_text": "will clear 1.8L by fri, waive the late fee pls",
  "intent": "NEGOTIATION_REQUEST",
  "intent_confidence": 0.88,
  "entities": {
    "promised_amount": 180000,
    "promised_date": "2026-09-11",
    "currency": "INR"
  },
  "model_used": "tfidf-svm-intent-v1",
  "fallback_triggered": false,
  "scored_at": "2026-09-05T09:42:11Z"
}
```

### Integration into the agent loop

- `PROMISE_TO_PAY` above the confidence threshold → auto-creates a Promise record with the extracted amount/date, no human step.
- `DISPUTE` → freezes escalation for that invoice and flags it for human review; the agent must never keep escalating a customer who is actively disputing the charge.
- `OPT_OUT` → writes immediately and permanently to the opt-out registry — this path should never depend on model confidence; treat any plausible opt-out signal as binding.
- `NEGOTIATION_REQUEST` → routed to the policy engine exactly like any other proposed offer; the classifier identifying intent never grants itself authority to approve terms.
- Low confidence on any class → escalate to Stage A (LLM) before falling back further to a human-review queue; never auto-act on an uncertain read of what a customer wants.

### Tools (all free)

`scikit-learn`, `sentence-transformers` or `transformers` (if fine-tuning), `dateparser`, plain `re` for currency regex, Google Colab free tier if local fine-tuning is too slow.

---

## Feature 2 — Recovery-Probability Model (replacing the rules-based scorer)

### Objective

Replace the hand-written `P(recovery)` term in the expected-value formula — `EV = P(recovery) × outstanding_amount × urgency_weight − intervention_cost` — with a trained model, so prioritization is learned from data rather than authored by hand, while keeping every score explainable and calibrated enough to trust in that formula.

### What exactly is being predicted

**Primary target (build this):** binary classification — *will this invoice be paid within 30 days of the point it was scored?* (`recovered_within_30d`: 0/1). Output as a calibrated probability, not just a class label.

**Stretch target (if time allows):** a multi-horizon version predicting probability of payment within 7 / 14 / 30 / 60+ days, giving a fuller recovery-time picture instead of one cutoff. This is closer to survival analysis and is a legitimate "we went further" flourish — but ship the binary version first; it alone is enough to replace the rules-based scorer credibly.

### Label construction (the part most likely to be done wrong)

The synthetic data generator must be extended so that, for every invoice, it also simulates a ground-truth outcome (whether and when it was actually paid), driven by the same archetype logic that drives the visible features — but the archetype label itself must never be exposed as a feature. Concretely: the generator assigns each customer a hidden archetype (reliable / late-but-pays / erratic / new-unknown / risk-escalating) that determines a *true* payment-probability distribution; it then (a) emits observable features consistent with that archetype (on-time ratio, historical lateness, etc.) and (b) samples an outcome from the true distribution — but the archetype name itself is discarded before the row reaches the model. This is what makes the label legitimate rather than circular.

### Feature set

| Group | Features |
|---|---|
| Customer history | On-time-payment ratio (90-day and all-time), average days late historically, invoice count with this customer, customer tenure (months), count of previously broken promises, count of previous disputes |
| Invoice attributes | Amount, amount relative to this customer's average invoice size, days overdue at scoring time, invoice age since issue date, day-of-week/month issued |
| Behavioral / interaction | Days since last contact, number of prior reminders sent for this invoice, whether a prior promise exists for this invoice and whether it was kept, current escalation tier |
| Derived | Recency-weighted on-time score (exponentially weighted average, recent invoices count more than old ones) |

### Train/validation/test split

Split by **invoice flag date**, not randomly: e.g., train on everything flagged before day 400 of the synthetic timeline, validate on days 400–460, test on days 460+. This mirrors how the model will actually be used (predicting forward from what's known so far) and is the same discipline Track 02 explicitly asks for with its held-out test set language.

### Model architecture and training approach

- **Baseline (train this first, always):** Logistic Regression. Cheap, instant to train, fully interpretable coefficients, and a legitimate sanity check — if a fancier model can't beat this, that's a real finding worth reporting, not a failure to hide.
- **Primary model:** Gradient-boosted trees — XGBoost or LightGBM. This is the right tool for tabular data at this scale: handles nonlinear interactions well, trains in seconds to minutes even on a laptop, and produces feature importances plus SHAP values for per-prediction explainability.
- **Stretch / DL comparison:** train a small feedforward neural network (2–3 hidden layers) on the same features and report how it compares to the gradient-boosted model. Be honest in the writeup: a tiny neural net will often *not* beat gradient boosting on a small tabular dataset, and saying so — with numbers — is a stronger architecture-discussion moment than claiming victory for "deep learning" that didn't actually win. Judges respect a documented, correct choice of tool far more than a bigger model used for its own sake.
- **Calibration:** after training, apply Platt scaling or isotonic regression on the validation split so the output probability is trustworthy on its own, not just correctly ranked — this matters because the EV formula multiplies this number directly into a rupee amount.

### Evaluation plan

- **AUC-ROC** and **Precision/Recall/F1** at a chosen operating threshold on the held-out test split.
- **Brier score** and a **calibration (reliability) curve** — this is the check that a predicted 70% actually resolves to roughly 70% of the time in the test data.
- **Feature importance / SHAP summary** — both a global ranking and per-prediction top-3 drivers.
- **Head-to-head comparison against the original rules-based scorer**, on the same held-out set: does the trained model actually rank invoices better than the hand-written formula it's replacing? Report this explicitly — it's the single most convincing piece of evidence that the ML addition is real, not decorative.

### Output schema

```json
{
  "invoice_id": "INV-1043",
  "p_recovery_30d": 0.71,
  "model_version": "xgb-recovery-v1",
  "calibrated": true,
  "top_drivers": [
    { "feature": "customer_on_time_ratio_90d", "value": 0.42, "shap_contribution": -0.18 },
    { "feature": "invoice_amount_vs_avg_ratio", "value": 2.4, "shap_contribution": -0.11 },
    { "feature": "days_overdue", "value": 12, "shap_contribution": -0.07 }
  ],
  "fallback_used": false,
  "scored_at": "2026-09-05T10:15:00Z"
}
```

This feeds directly into the existing formula — `EV = p_recovery_30d × outstanding_amount × urgency_weight(days_overdue) − intervention_cost` — and `top_drivers` gets written straight into that invoice's Decision Trace, so every prioritization decision the batch report shows is backed by a specific, inspectable reason, exactly like the rules-based version was, just learned instead of authored.

### Integration and fallback behavior

Implement a `RecoveryScorer` interface with two implementations — `RulesBasedScorer` (already built) and `ModelBasedScorer` (this feature) — selected by a config flag. At inference time, if the model fails to load, returns a NaN/out-of-range value, or throws, the system logs the failure and falls back to `RulesBasedScorer` for that invoice rather than blocking the batch. This keeps the safety story intact even with ML in the loop, and it's a legitimate resilience point to raise in the architecture discussion.

### Tools (all free)

`scikit-learn`, `xgboost` or `lightgbm`, `shap`, `pandas`. All train comfortably on a laptop CPU at this data scale — no GPU or paid compute needed.

---

## Suggested build order

1. Extend the synthetic data generator to emit ground-truth recovery outcomes and reply-intent-labeled text (both features depend on this).
2. Ship Stage A of the reply classifier (LLM zero-shot) — immediately useful, unblocks the promise-tracking demo.
3. Train the Logistic Regression baseline for the recovery model — cheap, and gives you an early "does the pipeline even work" checkpoint.
4. Train the primary models: gradient-boosted recovery scorer, TF-IDF/SVM (or fine-tuned transformer) intent classifier.
5. Run both evaluation suites and write the model cards (metrics, calibration plots, SHAP/feature-importance summaries, and the rules-vs-model comparison for the recovery scorer).
6. Wire both into the live pipeline behind their fallback-safe interfaces, and confirm the Decision Trace correctly logs `model_version` and top drivers for every score.

## Roadmap items deliberately not in this plan

- Multi-horizon survival-style recovery prediction (stretch only, after the binary version is solid and evaluated).
- Fine-tuned NER model for entity extraction — regex + `dateparser` covers this reliably at far lower cost; revisit only if real usage data shows they're missing cases the deterministic tools can't handle.
- Online/continual retraining from real outcomes — sensible once real webhook-confirmed payment data accumulates post-buildathon, not before.
