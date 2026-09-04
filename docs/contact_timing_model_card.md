# Model Card — Contact-Timing Optimizer (Thompson Sampling Bandit)

Reproduce every number below with:

```bash
python scripts/generate_contact_timing_data.py --customers 1200 --touches-per-customer 12 --seed 42
python scripts/train_contact_timing.py --seed 42
```

Every metric is deterministic at seed 42. The artifact *name* is not:
`model_version` carries a date + random suffix (`timingbandit-<date>-<hash>`)
so two runs never overwrite each other. Trained binaries are gitignored — a
fresh clone has no artifact and the scheduler serves a deterministic fallback
slot until you run the commands above.

## What it predicts

Given a customer at the moment a reminder is due, which of 15 send-time slots
(5 weekdays × morning / midday / late IST) is most likely to get a response
within 48 hours. The output is a slot plus its posterior response rate, which
the scheduler records on the contact row — it does not decide *whether* to
contact (the policy gate owns that) or *what* to say (templates own that).

## Model — segmented Thompson Sampling, custom implementation

One independent Beta-Bernoulli posterior per (segment, arm). Serving draws one
sample per arm and pulls the argmax — exploring early, exploiting late, with
no epsilon to tune. A global posterior per arm backs every thin segment, so a
new segment starts from population behaviour rather than a coin flip.

Custom lightweight implementation rather than pymab: the whole model is a dict
of (alpha, beta) counts, which keeps the artifact inspectable, the online
update a single addition, and the dependency list unchanged. Prior Beta(2, 2):
centred at 0.5 with the weight of four pseudo-observations, so the first real
touches move a new arm quickly instead of drowning in a strong wrong prior.

## Data — and the provenance caveat first

14,400 touches across 1,200 synthetic customers (12 touches each), logged under
a **uniform random policy** so the offline replay evaluation is unbiased
(every arm had propensity 1/15). Segment features are identical to production
`Customer` rows: on-time ratio, average days late, broken-promise and dispute
rates.

**The response labels are simulated.** They come from a behavioural response
model keyed off the generator's hidden archetype (`RESPONSE_MODEL` in
`src/ml/contact_timing/dataset.py`: reliable customers answer mornings,
erratic inboxes surface late, and so on). No public B2B reminder-response
dataset exists to train on instead. So these numbers measure whether the
pipeline *learns a planted signal* — evidence the machinery is correct, not
evidence of real-world lift. The honest-to-production path is the online loop
below: genuine inbound replies are real engagement events, and every one of
them updates the posterior it came from.

Two structural guarantees, both tested:

- **The archetype is never a feature.** Segments use observable history only;
  `tests/ml/test_contact_timing_dataset.py` asserts the training frame cannot
  reach the archetype column.
- **The split is by customer, never by row.** Touches from one customer never
  appear on both sides — the same grouped-split honesty as the reply
  classifier, for the same reason (per-customer habits would otherwise leak
  across the split).

## Segments (18, `contact-timing-v1`)

| Axis | Buckets |
|---|---|
| On-time ratio (90d) | reliable ≥ 0.8 / mixed 0.4–0.8 / strained < 0.4 |
| Average days late | prompt ≤ 3 / slow ≤ 15 / verylate > 15 |
| Risk flag | flagged (any broken-promise or dispute rate) / clean |

Rates are normalised by invoice count, not raw counts — long-standing
customers are not punished for being long-standing, the same normalisation the
scorer uses for the same reason.

## Results — offline replay on 360 held-out customers (4,320 touches)

| Policy | Response rate | Notes |
|---|---|---|
| Uniform random (logging policy) | 0.450 | what the log itself earned |
| **Segmented Thompson Sampling (shipped)** | **0.504** | **+0.053 over random** |
| Best single fixed arm (`thu_morning`) | 0.522 | the personalization premium not yet earned |

Replay matched 282 rows (policy arm == logged arm; expectation under uniform
logging is 288, so the log is behaving). Standard error on the matched set is
≈ 0.030 — the +0.053 lift is directionally solid and marginally resolved, not
overwhelming. Stated plainly: at ~37 training touches per (segment, arm) cell,
the posteriors are still noisy, and a single good global slot (`thu_morning`)
trails the personalised policy by less than 2 points. The bandit earns its
keep as online replies densify the cells it actually serves — which is
precisely what the update hook below is for.

Simulator-truth check: posterior means vs the latent response rates the
simulator knows, MAE **0.1167** over observed (segment, arm) pairs. Labelled
as a simulator check — it confirms the posteriors track the planted signal
directionally, nothing about real inboxes.

### Architecture choice, honestly reported

- **Segmented Beta-Bernoulli beat uniform random (+0.053).** Context carries
  signal: different habits genuinely prefer different slots in this data.
- **It did not beat the best fixed arm (−0.018).** With thin per-cell data,
  shrinkage toward the global prior leaves money next to a well-chosen static
  slot. Shipping the bandit anyway is a bet on the online loop, stated as such:
  the fixed arm cannot learn; the bandit does, one genuine reply at a time.
- **No neural contextual model was tried.** Fifteen discrete arms with count
  posteriors dominate small-data regimes; a function approximator here would
  be decoration.

## Serving and fallback

`GET /schedule/next_time?customer_id=…` maps the customer row to a segment,
Thompson-samples the arms, and returns the next calendar occurrence of the
winning slot plus its posterior rate and observation count. Three fallback
layers, each labelled in the response:

| Failure | `fallback.reason` |
|---|---|
| no trained artifact on this clone | `model_unavailable` (deterministic next-weekday-morning slot) |
| unknown customer id | 404, no guess |
| thin segment (no direct observations) | served from the global posterior, `backed_off_to_global: true` |

## Online learning — the loop that makes this production-real

Every contact row records the arm it was sent in (`timing_arm`). When a
customer reply arrives for that invoice, ingestion folds reward = 1 into the
posterior for (customer segment, arm) — guarded, silent on failure, never
blocking the reply path. Contacts that earn no reply are the implicit zeros
already in the log; the posterior is a living count, not a frozen model.

Opt-out replies are excluded from the update: a STOP is a response, but
teaching the bandit that a slot "works" because it provoked an opt-out would
optimise for harassment. Per exclusion, documented here rather than buried.

## Limitations

- **Simulated labels.** Every number above measures learning a planted signal.
  Real-world lift is unknown until genuine reply traffic accumulates.
- **Thin cells.** ~37 training touches per (segment, arm) cell; the +0.053
  lift sits at ~1.8 standard errors. More touches per cell, or fewer arms,
  would resolve it further.
- **Reward is response, not payment.** A reply is engagement, not settlement.
  Optimising purely for replies could favour slots that provoke disputes; the
  promise tracker and recovery scorer remain the counterweights.
- **No delayed-reward modelling.** Responses after the 48h window count as
  misses. A survival-style extension is future work, not shipped here.
- **Weekdays only.** Weekend sending is excluded by design (B2B goodwill),
  so Friday-late arms absorb end-of-week effects the model cannot separate.
