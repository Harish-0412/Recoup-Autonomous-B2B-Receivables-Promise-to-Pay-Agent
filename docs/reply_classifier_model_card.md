# Model Card — Reply Intent Classifier (Stage C)

**Artifact** `tfidf-svm-intent` · **Corpus** `reply-intent-corpus-v1` · **Prompt (Stage A)** `reply-intent-fewshot-v1`

Regenerate everything below with:

```bash
python scripts/train_reply_classifier.py --size 1400 --seed 42 --repeats 5
```

---

## What it predicts

One of eight intents for a raw customer reply, plus a calibrated confidence.
Entities (`promised_amount`, `promised_date`, `currency`, `dispute_reason`) are
**not** predicted by this model — they come from the deterministic extractors in
`src/ml/reply/entity_extraction.py`, which are more reliable here and can be
audited line by line.

| | |
|---|---|
| Task | 8-class single-label classification |
| Model | TF-IDF (word + char n-grams) → linear SVM, calibrated |
| Role | Stage C of a cascade; the LLM is Stage A |
| Never decides | What action to take. It reports what the customer said; the policy engine disposes |

---

## Data

1,400 label-first synthetic examples from 145 templates, 175 per intent.
"Label-first" means the intent, amount and date are chosen *before* the text is
rendered, so ground truth is correct by construction rather than by annotation.

| Property | Value |
|---|---|
| Examples | 1,400 (175 per intent, exactly balanced) |
| Templates | 145 |
| Registers | formal, curt, apologetic, annoyed, Hinglish |
| Edge cases | multi-intent, ambiguous, wrong-invoice, sarcasm, noisy shorthand, dispute-adjacent |
| Splits | 70 / 15 / 15 train / val / test |

### The corpus size is derived, not chosen

1,400 ≈ 145 templates × ~15 renderings. It is not a round number picked for
looks: adding templates without raising the size thins every template's
representation. When seven hard negatives were added at the old size of 1,200,
accuracy fell from 0.711 to 0.661 and OPT_OUT recall from 0.962 to 0.808 purely
from dilution.

### Quality gate

`audit_corpus` samples 10% of the corpus and checks every example against its
declared label and entities, and against the opt-out guard. The last run:
**140/1400 sampled, 0 findings, 0 opt-out guard misses**, with the sample
written to `spot_check_sample.jsonl` for human review.

The audit has already earned its place twice. It flagged an OPT_OUT example
rendered as *"Hello, bn karo ye messages."* — vowel-dropped Hinglish for "stop
these messages" — that the deterministic guard did not match. That was a real
compliance gap, and the guard now covers the shortened spelling.

Running the audit over the **whole** corpus rather than the 10% sample finds one
remaining guard miss, and it is worth stating rather than hiding:

> *"We will pay next week. Do not messaeg me again about it."*

A transposed typo in "message". A regex guard cannot chase arbitrary
misspellings, and widening it until it could would start matching things that
are not opt-outs. The defence is layered instead: **the classifier reads this
one as OPT_OUT at 0.955 confidence**, and Stage A is behind that. The guard is
the floor for exact phrasing, not the whole protection.

### Split — by template, never at random

No template appears in two splits. Renderings of one template are near-duplicates,
so a random split puts near-identical sentences in train and test and measures
memorisation.

The gap is not subtle, and both numbers are reported every run:

| Split strategy | macro-F1 |
|---|---|
| Random (stratified) | **1.000** |
| Grouped by template | **0.764** |

A random split scores a perfect 1.000 here. Quoting it would be the single most
misleading number this project could publish.

---

## Results — grouped split, 202 held-out replies

| class | precision | recall | F1 | support |
|---|---|---|---|---|
| PROMISE_TO_PAY | 1.000 | 0.741 | 0.851 | 27 |
| DISPUTE | 0.821 | 0.793 | 0.807 | 29 |
| OPT_OUT | 0.917 | 0.957 | 0.936 | 23 |
| NEGOTIATION_REQUEST | 0.676 | 1.000 | 0.807 | 23 |
| PARTIAL_PAYMENT_CLAIM | 0.692 | 0.581 | 0.632 | 31 |
| ALREADY_PAID_CLAIM | 0.960 | 1.000 | 0.980 | 24 |
| GENERAL_QUERY | 0.697 | 0.767 | 0.730 | 30 |
| OTHER | 0.417 | 0.333 | 0.370 | 15 |
| **accuracy** | | | **0.782** | 202 |
| **macro F1** | | | **0.764** | |

### One split is not a measurement

A grouped split leaves roughly 20 templates in test, so which ones land there
moves per-class precision by tens of points. Over five independent grouped
splits (seeds 42–46):

| class | precision (mean ± sd) | observed range |
|---|---|---|
| OPT_OUT | 0.836 ± 0.184 | 0.52 – 1.00 |
| PROMISE_TO_PAY | 0.791 ± 0.176 | 0.58 – 1.00 |
| GENERAL_QUERY | 0.742 ± 0.124 | 0.57 – 0.95 |
| PARTIAL_PAYMENT_CLAIM | 0.710 ± 0.160 | 0.55 – 1.00 |
| DISPUTE | 0.667 ± 0.354 | 0.11 – 1.00 |
| ALREADY_PAID_CLAIM | 0.653 ± 0.190 | 0.38 – 0.96 |
| NEGOTIATION_REQUEST | 0.645 ± 0.218 | 0.31 – 0.97 |
| OTHER | 0.616 ± 0.251 | 0.24 – 0.89 |
| **accuracy** | **0.679 ± 0.097** | |

**Read the spread, not just the mean.** A ±0.35 standard deviation on DISPUTE
means no single-split DISPUTE number from this corpus should be quoted as a
result, in either direction.

---

## DISPUTE: the class whose errors cost the most

A false DISPUTE freezes escalation and routes the invoice to a human — collection
silently stops on a customer who never disputed anything. A false negative only
costs one more reminder. The errors are not symmetric, so the handling is not
either.

**Two changes, measured separately.**

*1. Hard negatives.* Seven GENERAL_QUERY templates that use dispute vocabulary
(GST, HSN, PO, GRN, quantity, freight, rate, credit note) while asking rather
than contesting — "Quick query on the GST line: is this 12% or 18% for this HSN
code?". Averaged over five grouped splits:

| | DISPUTE precision | OPT_OUT recall | accuracy |
|---|---|---|---|
| No hard negatives | 0.444 | 0.888 | 0.655 |
| **+7 GENERAL_QUERY (shipped)** | **0.667** | 0.865 | **0.679** |
| +14 (also OTHER) — rejected | 0.506 | 0.835 | 0.656 |

Seven matching OTHER templates were written, measured, and **removed**: they made
every metric worse. OTHER is already the most lexically diffuse class, and
widening it further pulled OPT_OUT across the boundary. The measurement is the
reason they are not in the corpus.

*2. A raised bar in the cascade.* DISPUTE must reach **0.85** confidence before
Stage C acts on it, against 0.60 for everything else. Same models, same data,
only the routing policy changed, pooled over five splits (1,065 replies):

| policy | DISPUTEs acted on without LLM review | Stage C resolves |
|---|---|---|
| Uniform 0.60 | 46 | 52.8% |
| **DISPUTE ≥ 0.85** | **1** | 48.7% |

Exposure to an unreviewed DISPUTE decision falls by roughly 98%, and the price
is 4.1 percentage points of cascade efficiency — measurably more LLM calls.

**OPT_OUT deliberately does not get the same treatment.** There the expensive
error runs the other way: missing a genuine opt-out is a compliance failure,
while a false positive costs one silenced reminder. It stays on the base
threshold and is backed by a deterministic guard in `service.py` that never
consults confidence at all.

---

## Cascade efficiency

| | |
|---|---|
| Resolved by Stage C | 72 / 202 (35.6%) |
| Accuracy on what it keeps | **0.917** |
| Escalated to Stage A (LLM) | 130 (64.4%) |
| Stage C accuracy on what it escalated | 0.708 |

The pair of accuracies is the point. Stage C is markedly better on what it keeps
than on what it hands off, which is the evidence that the confidence signal is
carrying real information rather than splitting traffic arbitrarily.

---

## Calibration

ECE **0.215** over five populated buckets on the held-out split (validation:
0.131). The model is **under-confident**: it is right more often than it claims.

| stated | observed |
|---|---|
| 0.32 | 0.47 |
| 0.49 | 0.75 |
| 0.69 | 0.91 |
| 0.85 | 1.00 |

Under-confidence is the safe direction for a cascade — it escalates work that
Stage C would have got right, buying accuracy with latency and LLM spend rather
than the reverse. It is still a defect: at these gaps the 0.60 threshold is
effectively stricter than intended, which is part of why only 35.6% of traffic
is resolved locally. Isotonic recalibration on a larger validation split is the
obvious next step.

---

## Entity extraction (deterministic, not learned)

Over the whole corpus:

| | correct | false positives |
|---|---|---|
| Amounts | 351 / 351 (1.000) | 0 |
| Dates | 303 / 303 (1.000) | 28 |

The 28 date false positives are dates found in replies that carry no promise —
"our office is closed until next week". **Zero of them survive the intent gate**,
because `service.py` only attaches `promised_date` for intents where a promise is
meaningful. A date in small talk can never become a promise-to-pay record.

---

## Explainability

Every Stage C prediction names the n-grams that drove it, taken from the linear
model's coefficients: `Classified as OPT_OUT on 'do', 'do not', 'again'.` This is
not a post-hoc approximation — for a linear model on TF-IDF features it *is* the
decision. Stage A returns a one-sentence explanation quoting the decisive words.

---

## Serving and fallback

`understand_reply` never raises. Every failure — no provider configured, timeout,
prose instead of JSON, an unknown label, a classifier that throws — returns a
valid `ReplyIntentPrediction` with `fallback_used=True` and a reason, so a
strangely worded reply becomes a human-review item rather than a stack trace.

Routing, in order:

1. **Opt-out guard** — deterministic, overrides any classifier output at any confidence.
2. **Stage C** — if confidence clears the bar for its predicted intent.
3. **Stage A (LLM)** — anything below the bar.
4. **Human review** — Stage A also failed, or confidence is still below threshold.

---

## Limitations

- **Synthetic data.** 145 templates cannot cover how real customers write. Every
  number here is an upper bound on real-world performance, not an estimate of it.
- **OTHER is weak** (F1 0.370, recall 0.333). It is the residual class and absorbs
  what the other seven reject. Its errors are mostly toward GENERAL_QUERY, which
  is the benign direction — a query gets answered instead of ignored.
- **PARTIAL_PAYMENT_CLAIM recall is 0.581**, confused with NEGOTIATION_REQUEST and
  OTHER. A missed partial-payment claim means chasing for the full amount when
  part has been paid, which is a customer-experience failure worth fixing next.
- **Wide variance across splits.** With 145 templates, five splits is the minimum
  honest sample; ten would be better.
- **Calibration is under-confident**, costing cascade efficiency (above).
- **No real-traffic validation.** Until real replies arrive, the cascade's
  escalation rate — and therefore its cost — is a projection.
