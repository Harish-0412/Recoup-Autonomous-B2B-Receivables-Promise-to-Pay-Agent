# Batch Evaluation Report

Generated 2026-09-03 by `python scripts/run_batch_demo.py --batch-size 600 --seed 42 --cycles 5`.

This file is generated output, committed on purpose. Re-running the
command above against the same seed reproduces it exactly.

```
Invoices processed:                      600
Total overdue value:                     Rs 6.96 Cr
Decision cycles run:                     5
Cases flagged for intervention:          513
Cases left alone:                        87
Cases handed off to a human:             402
Interventions executed (all cycles):     505
Actions blocked by policy (all cycles):  626
Recovered (of flagged):                  326
Recovered value:                         Rs 3.97 Cr
Recovery rate (of flagged):              63.5%
False/unnecessary interventions:         263
Cases correctly left alone:              81
Missed recoveries (left alone, unpaid):  6
Compliance violations:                   0
Decision trace entries:                  5452
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
| Scorer | rules-based (`fallback_used=True`) |

## How to read these numbers

**The scorer is rules-based, not trained.** Every recovery probability
in this run came from a hand-tuned logistic model, returned with
`fallback_used=True` and `resolved_by=rules_based_scorer`. Phase 4
replaces it with a trained, calibrated model; until then these
probabilities are not calibrated and should not be read as such.

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
| `contact_volume_cap` | 469 |
| `contact_frequency_cap` | 176 |
