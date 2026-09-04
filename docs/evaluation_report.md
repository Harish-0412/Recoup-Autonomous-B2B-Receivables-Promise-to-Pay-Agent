# Batch Evaluation Report

Generated 2026-09-03 by `python scripts/run_batch_demo.py --batch-size 600 --seed 42 --cycles 5 --use-model`.

This file is generated output, committed on purpose. Re-running the
command above against the same seed reproduces it exactly.

```
Invoices processed:                      600
Total overdue value:                     Rs 6.96 Cr
Decision cycles run:                     5
Cases flagged for intervention:          574
Cases left alone:                        26
Cases handed off to a human:             468
Interventions executed (all cycles):     580
Actions blocked by policy (all cycles):  699
Recovered (of flagged):                  385
Recovered value:                         Rs 4.68 Cr
Recovery rate (of flagged):              67.1%
False/unnecessary interventions:         316
Cases correctly left alone:              22
Missed recoveries (left alone, unpaid):  4
Compliance violations:                   0
Decision trace entries:                  5807
Ledger hash chain verified:              yes
```

## Run parameters

| Parameter | Value |
|---|---|
| Batch size | 600 |
| Decision cycles | 5 |
| Seed | 42 |
| Generator | synthetic-generator-v1 |
| Recovery horizon | 30 days |
| Discount ceiling | 10% |
| Minimum contact gap | 3 days |
| Scorer | model-based (`logreg-recovery-20260903-3f49c1`, calibrated) |
| Classification threshold (ML panel) | 0.5 |

## How to read these numbers

**The scorer is the trained model.** Every recovery probability
in this run came from the gradient-boosted (or logistic) model,
isotonically calibrated on a held-out validation split, so these
numbers can be read as probabilities. Any invoice the model could
not score fell back to the rules and is marked `fallback_used=True`
in the decision trace.

Full held-out metrics for the model-scored batch, including AUC,
precision/recall, Brier score, calibration curve, SHAP feature
importance and a direct ranking comparison against the
rules-based incumbent, appear in the **ML evaluation panel**
section at the bottom of this report.

**The agent did not cause these recoveries.** The synthetic generator
samples each invoice's outcome independently of what the agent does,
so `recovered` is the outcome under *no* intervention. The recovery
rate below therefore measures whether the agent chose to act on the
invoices that were going to be paid -- it is a targeting measure, not
a causal one. Claiming otherwise would require a holdout arm that this
simulation does not have.

**False interventions are counted as a cost.** A contacted customer
whose invoice was recovered anyway is counted in that row, not quietly
dropped. That number going up is a real regression.

**Compliance violations are counted from the ledger, not from intent.**
The row counts invoices where an action was executed after the policy
engine recorded a block for that invoice. It reads the decision trace
rather than the orchestrator's own return values, so a bug in the
orchestrator cannot suppress it.

## Policy blocks by rule

| Rule | Blocked |
|---|---|
| `contact_volume_cap` | 511 |
| `contact_frequency_cap` | 208 |

## ML held-out evaluation (batch-demo slice)

Every case in this run was scored with *both* the trained model and
the rules-based incumbent, on labels drawn from the synthetic
generator's ground-truth `recovered` field. Positive rate on the
slice: 67.8%
 (n = 600).

The model that ships is selected by highest held-out AUC from
three candidates: logistic regression (baseline), gradient-boosted
trees (XGBoost) and a small MLP. Full per-candidate metrics,
temporal split details, and training-set SHAP importance appear in
`docs/recovery_model_card.md`. The numbers in *this* section were
scored live on the demo batch and are reproducible via the command
at the top of this report.

### Classification & ranking metrics

| Model | AUC | AP | Precision | Recall | F1 | Brier | ECE |
|---|---|---|---|---|---|---|---|
| `rules-based` (incumbent) | 0.667 | 0.822 | 0.793 | 0.528 | 0.634 | 0.2433 | 0.180 |
| `logreg-recovery-20260903-3f49c1` (trained, calibrated) | 0.709 | 0.810 | 0.749 | 0.882 | 0.810 | 0.1944 | 0.065 |

Threshold used for hard-classification metrics: p >= 0.5.
AUC and Average Precision are threshold-free and come directly from
the probability output.

### Does the trained model beat the hand-written scorer?

logreg-recovery-20260903-3f49c1 beats rules-based on ranking: AUC 0.709 vs 0.667 (+0.041), Brier 0.1944 vs 0.2433.

| Scorer | Top-k value-at-risk captured |
|---|---|
| `rules-based` (incumbent) | Rs 79.5L |
| `logreg-recovery-20260903-3f49c1` (trained) | Rs 85.5L |

Top-120 delta: **Rs 5.9L (+7.5%)** more unpaid rupees correctly surfaced in the work queue.

Interpretation: value-at-risk-captured sums the *unpaid* rupees
among the k invoices the scorer ranks first (lowest P(recovery)).
A higher number means the team's finite outreach bandwidth is
directed at the invoices that would otherwise have gone unpaid.

### Calibration of the recovery probability output

Reliability curve for the trained model: predicted probability
band vs. observed rate, bin by bin. ECE is the support-weighted
mean absolute gap between predicted and observed.

| Bin | n | Mean predicted | Observed rate | Gap |
|---|---:|---:|---:|---:|
| [0.0, 0.1) | 2 | 0.000 | 0.000 | +0.000 |
| [0.1, 0.2) | 4 | 0.164 | 0.250 | -0.086 |
| [0.2, 0.3) | 1 | 0.250 | 1.000 | -0.750 |
| [0.3, 0.4) | 113 | 0.314 | 0.407 | -0.093 |
| [0.4, 0.5) | 1 | 0.473 | 0.000 | +0.473 |
| [0.5, 0.6) | 141 | 0.578 | 0.645 | -0.067 |
| [0.6, 0.7) | 73 | 0.658 | 0.726 | -0.068 |
| [0.7, 0.8) | 56 | 0.747 | 0.696 | +0.050 |
| [0.8, 0.9) | 90 | 0.867 | 0.811 | +0.056 |
| [0.9, 1.0) | 119 | 0.904 | 0.866 | +0.038 |

**Summary**: ECE=0.065, Brier=0.1944  (rules-based: ECE=0.180, Brier=0.2433).

**Calibration assessment**: adequate for prioritisation. ECE is slightly above the 0.05 target, in part because this is a small (n=600) held-out slice where calibration statistics are noisier. The training-time calibration report on the full (larger) held-out set is the authoritative number.

Benchmarks: ECE on the rules-based scorer on the same slice is 0.180. The model's 
probabilities are trained on isotonic calibration fitted on a 
held-out validation split, so they are expected to be more 
reliable than the hand-written scorer's on larger books.

### Global SHAP feature importance

Mean absolute SHAP contribution per feature across the entire
batch. Features at the top move the model's output the most;
the ranking is inspectable and should roughly agree with the
domain intuitions driving the rules-based scorer.

| Feature | Mean SHAP magnitude |
|---|---:|
| `recency_weighted_on_time_score` | 0.4694 |
| `customer_on_time_ratio_all_time` | 0.2748 |
| `customer_invoice_count` | 0.2714 |
| `prior_promise_kept` | 0.2183 |
| `customer_avg_days_late` | 0.2142 |
| `current_escalation_tier` | 0.1525 |
| `customer_dispute_count` | 0.1329 |
| `customer_broken_promises_count` | 0.1327 |
| `customer_broken_promise_rate` | 0.1196 |
| `invoice_amount` | 0.0969 |
| `customer_dispute_rate` | 0.0871 |
| `invoice_amount_log` | 0.0819 |
| `invoice_amount_vs_customer_avg_ratio` | 0.0620 |
| `has_prior_promise` | 0.0377 |
| `days_overdue_at_scoring` | 0.0353 |
