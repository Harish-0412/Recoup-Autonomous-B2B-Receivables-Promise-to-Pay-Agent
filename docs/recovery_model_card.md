# Model Card — Recovery-Probability Model

Reproduce every number below with:

```bash
python scripts/train_recovery_model.py --batch-size 6000 --seed 42
```

Every metric on this page is deterministic at seed 42 and was re-verified
against a from-scratch retrain. The artifact *name* is not: `model_version`
carries a random suffix (`xgb-recovery-<date>-<hash>`) so two runs never
overwrite each other, so your local id will differ from the ones quoted below.
Trained binaries are gitignored — a fresh clone has no artifact and scores with
the rules until you run the command above.

## What it predicts

Given an overdue invoice at the moment it is scored, the probability it is
**paid within 30 days of that moment** (`recovered_within_30d`). The output is a
calibrated probability, not a class label, because it is multiplied straight
into a rupee figure:

    EV = p_recovery_30d × outstanding × urgency_weight(days_overdue) − intervention_cost

This replaces the hand-written probability in `app.core.scorer`. The rest of
that formula is unchanged — swapping the probability was the entire point.

## Data

Synthetic, from `src/data/synthetic_generator.py` (`synthetic-generator-v1`,
seed 42). Each customer is drawn from a hidden behavioural archetype
(reliable / late-but-pays / erratic / new-unknown / risk-escalating) that drives
*both* their observable history and a latent payment probability. The outcome is
sampled from that latent probability; the archetype name, the true probability
and the eventual payment delay are then discarded before the row reaches the
model.

Two properties make the label legitimate rather than circular:

- **The archetype is never a feature.** It is declared `exclude=True`, and
  `tests/ml/test_recovery_features.py` asserts no ground-truth field is
  reachable from a `CaseSnapshot`.
- **Outcomes are not perfectly predictable.** An irreducible noise term is added
  to the latent logit on purpose. A simulator whose labels are a deterministic
  function of its features would produce an implausible AUC and teach nothing.

Of 6,000 generated invoices, **5,659 have a mature 30-day label**. The other 341
were flagged too recently for the window to have closed and are excluded —
including them would label ordinary invoices "not recovered" purely because time
had not passed, teaching the model that recent invoices fail.

### Split — by flag date, never at random

| Split | Rows | Positive rate | Flag-date range |
|---|---|---|---|
| Train | 3,960 | 0.673 | 2025-03-10 → 2026-02-26 |
| Validation | 843 | 0.683 | 2026-02-27 → 2026-05-16 |
| Test | 856 | 0.690 | 2026-05-17 → 2026-08-02 |

A random split would let the model learn from invoices flagged *after* the ones
it is tested on — a future it will never have at inference time. Cuts land on
dates rather than row indices, so every invoice flagged on a given day stays in
one split.

## Features (23, `recovery-features-v1`)

| Group | Features |
|---|---|
| Customer history | on-time ratio (90-day, all-time), average days late, invoice count, tenure months, broken-promise count and rate, dispute count and rate |
| Invoice attributes | amount, log amount, amount vs. customer average, days overdue at scoring, invoice age, payment terms, issued day-of-week, issued day-of-month |
| Behavioural | days since last contact, prior reminders sent, has prior promise, prior promise kept, current escalation tier |
| Derived | recency-weighted on-time score (exponentially weighted, recent invoices count more) |

Computed by one pure function that both the training and serving paths import,
with a test asserting byte-identical output from each — the structural defence
against train/serve skew.

## Results — held-out test split (856 most recent invoices)

| Model | AUC | AP | Precision | Recall | F1 | Brier | ECE |
|---|---|---|---|---|---|---|---|
| rules-based (incumbent) | 0.732 | 0.861 | 0.842 | 0.606 | 0.705 | 0.2130 | 0.167 |
| logreg-recovery (baseline) | 0.774 | 0.872 | 0.762 | 0.926 | 0.836 | 0.1706 | 0.031 |
| **xgb-recovery (shipped)** | **0.779** | 0.866 | 0.765 | 0.924 | **0.837** | **0.1700** | 0.033 |
| mlp-recovery | 0.767 | 0.857 | 0.782 | 0.844 | 0.812 | 0.1743 | 0.033 |

Threshold 0.5, isotonic calibration fitted on the validation split.

### Head to head against the scorer it replaces

**xgb-recovery beats the rules-based scorer on ranking: AUC 0.779 vs 0.732
(+0.047), Brier 0.1700 vs 0.2130.** In the top 171 invoices of the work queue
(top 20%), it surfaces **Rs 1.59 Cr** of genuinely at-risk value against the
rules' **Rs 1.35 Cr** — about Rs 24.6 L more money correctly prioritised.

This is the number that matters. It is the evidence that the ML addition is
real rather than decorative.

### Architecture choice, honestly reported

- **Gradient boosting beat the logistic baseline, but barely** — 0.779 vs 0.774.
  The nonlinearity is real but small; most of the signal in these features is
  close to linear. The baseline is worth keeping for exactly this reason.
- **The neural network did not win** (0.767). On a few thousand rows of tabular
  data a small MLP usually loses to gradient boosting, and saying so with
  numbers is a better answer than reaching for the biggest available model.
- Both facts are printed by the training script on every run, not curated
  afterwards.

### Calibration

Isotonic regression fitted on validation, never on training data. ECE drops from
**0.167 (rules) to 0.033 (model)** — a fivefold improvement — and the reliability
curve tracks the diagonal closely in the populated bins:

| Bin | n | Predicted | Observed | Gap |
|---|---|---|---|---|
| [0.3, 0.4) | 118 | 0.345 | 0.314 | +0.032 |
| [0.6, 0.7) | 247 | 0.647 | 0.599 | +0.047 |
| [0.7, 0.8) | 173 | 0.775 | 0.763 | +0.012 |
| [0.8, 0.9) | 287 | 0.887 | 0.909 | −0.023 |

Sparse bins (n < 15) sit at the extremes and their gaps are noise, not
miscalibration. This matters more than AUC here: a well-ranked but badly scaled
probability would still multiply into the wrong rupee figure.

## Explainability

Global SHAP importance on the test split, top 10:

| Feature | Mean \|SHAP\| |
|---|---|
| customer_broken_promise_rate | 0.420 |
| customer_avg_days_late | 0.248 |
| recency_weighted_on_time_score | 0.187 |
| customer_on_time_ratio_90d | 0.160 |
| prior_promise_kept | 0.142 |
| days_overdue_at_scoring | 0.132 |
| customer_dispute_rate | 0.131 |
| customer_on_time_ratio_all_time | 0.129 |
| invoice_amount | 0.114 |
| customer_invoice_count | 0.101 |

Payment history dominates, which is what a collections analyst would expect —
a useful sanity check that the model learned behaviour rather than an artefact.

Every prediction also carries its **top 3 drivers**, written into that invoice's
Decision Trace, so each prioritisation decision is backed by a specific,
inspectable reason:

```json
{
  "invoice_id": "INV-2026-00001",
  "p_recovery_30d": 0.612903,
  "model_version": "xgb-recovery-20260903-ae0033",
  "calibrated": true,
  "top_drivers": [
    {"feature": "customer_broken_promise_rate",   "value": 0.10,   "shap_contribution": -0.557},
    {"feature": "customer_dispute_rate",          "value": 0.30,   "shap_contribution": -0.474},
    {"feature": "recency_weighted_on_time_score", "value": 0.7886, "shap_contribution":  0.234}
  ],
  "fallback_used": false,
  "scored_at": "2026-09-03T07:24:06Z"
}
```

## Serving and fallback

Two implementations behind one `RecoveryScorer` interface, selected by the
`USE_MODEL_SCORER` config flag:

- `RulesBasedScorer` — the hand-written logistic, always reporting
  `fallback_used=True` so it can never be mistaken for a fitted score.
- `ModelBasedScorer` — this model, with the rules underneath it.

If the artifact will not load, if scoring raises, or if the model returns a NaN
or an out-of-range value, that **one invoice** is scored by the rules instead,
the reason is recorded on the prediction, and the batch continues. All four
paths are covered in `tests/ml/test_scorer_integration.py`:

| Failure | `fallback.reason` |
|---|---|
| artifact missing / unloadable | `model_load_failed` |
| `predict` raises | `unexpected_error` |
| returns NaN | `invalid_output` |
| returns out of [0, 1] | `invalid_output` |

## Limitations

- **The data is synthetic.** Every number here measures whether the pipeline
  recovers a signal that was deliberately put into the generator. It is evidence
  the machinery is correct, not evidence of real-world accuracy.
- **The labels are observational, not causal.** The generator samples each
  outcome independently of what the agent does, so `recovered` is the outcome
  under *no* intervention. The model predicts who will pay, not who will pay
  *because* the agent acted. Measuring the latter needs a holdout arm this
  simulation does not have.
- **One horizon only.** A multi-horizon (7/14/30/60-day) survival-style version
  is a deliberate stretch item, not shipped here.
- **No online retraining.** Sensible once real webhook-confirmed payment data
  accumulates; not before.
