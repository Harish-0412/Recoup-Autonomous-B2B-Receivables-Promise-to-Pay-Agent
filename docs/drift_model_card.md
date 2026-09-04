# Drift Model Card — `iforest-drift`

Payment-behavior drift detector: an Isolation Forest that learns what normal
repayment looks like and flags customers whose trailing behavior no longer
resembles it. A flag is a suggestion that a human look — it changes no
invoice state, freezes nothing, and sends nothing.

## 1. Dataset (real, not synthetic)

**"Default of Credit Card Clients" (Yeh, 2009), UCI ML Repository id 350.**
30,000 credit-card customers in Taiwan, April–September 2005.

- DOI `10.24432/C55S3H`, license **CC BY 4.0** (sharing/adaptation allowed
  with credit — cited below).
- 25 columns, zero missing values (verified on load): `ID`, `LIMIT_BAL`,
  demographics (`SEX`, `EDUCATION`, `MARRIAGE`, `AGE`), six months of
  repayment status (`PAY_0`, `PAY_2`…`PAY_6`: −2/−1 paid duly, 0 revolving,
  1–8 months of delay), bill statements (`BILL_AMT1`…`6`), actual payments
  (`PAY_AMT1`…`6`), and the target `default payment next month`
  (positive rate **22.12%**).
- The training script downloads the archive on first run into
  `data/drift/raw/` (gitignored); re-runs reuse the cache. No dataset file
  is committed.

Why this dataset: it contains genuine repayment behavior — delays that
lengthen, coverage that thins, months with no payment — rather than a
synthetic generator whose planted signal would only prove the pipeline can
recover its own assumptions.

## 2. Features (one schema, training and serving)

Nine columns, computed by the single shared function
`src.ml.drift.features.from_period_series`, imported by both the trainer
and the backend:

| Feature | Meaning |
|---|---|
| `payment_delay_mean` / `payment_delay_max` | Level of lateness, in 30-day months |
| `payment_delay_trend` | OLS slope of delay across periods (the degradation signal) |
| `delinquent_rate` | Fraction of periods with any delay |
| `pay_ratio_mean` / `pay_ratio_trend` | Paid/billed coverage level and slope |
| `bill_volatility` | Coefficient of variation of billed amounts |
| `utilization` | Billing relative to credit limit |
| `missed_rate` | Fraction of billed periods with zero payment |

Disputes are **excluded by design**: a dispute already freezes escalation
and routes to human review through the deterministic reply path, so scoring
it would double-count it.

## 3. Training

- Command: `python scripts/train_drift_model.py --seed 42`
- Split: 24,000 train / 6,000 holdout, stratified by the proxy flag.
- Fit: Isolation Forest (`n_estimators=200`, seed 42) on the **18,691
  train customers who did not default** — the working definition of normal.
- Threshold: frozen at the contamination quantile of train scores
  (**0.0**) and shipped inside the artifact, so serving never re-derives
  it from live data.
- Shipped contamination **0.10** (top-decile review queue); 0.05/0.15
  reported for comparison, not shipped.
- Artifact: `iforest-drift-20260904-f990b2` in `src/ml/artifacts_store/`
  (gitignored); metrics in `data/drift_model/` (gitignored).

## 4. Evaluation (seed 42, holdout n=6,000)

Drift has no ground-truth label, so there is no accuracy number. Three
checks that mean something:

| Contamination | Threshold | Holdout flag rate | Normal flag rate | Flagged default rate | Unflagged default rate | Lift |
|---|---|---|---|---|---|---|
| 0.05 | 0.0 | 9.1% | 5.1% | 56.6% (n=548) | 18.7% | **3.03×** |
| **0.10 (shipped)** | 0.0 | 16.1% | 10.0% | 51.7% (n=968) | 16.4% | **3.14×** |
| 0.15 | 0.0 | 21.5% | 14.8% | 46.4% (n=1,289) | 15.5% | **3.00×** |

- **Calibration**: the normal-payer flag rate tracks contamination (10.0%
  vs 10%), so the queue size is predictable.
- **Forward-risk lift (proxy)**: flagged customers default next month at
  51.7% vs 16.4% unflagged — a 3.14× lift. The default flag is a proxy,
  not drift truth, and is reported as such.
- **Injected-drift recall (real ground truth)**: 2,000 holdout customers
  the model called normal were degraded on purpose (+~2 months delay,
  halved recent payments); **1,388 newly flagged → recall 0.694**.
- **Global drivers** (share of flagged holdout customers where the feature
  led the explanation): `payment_delay_max` 99.6%, `payment_delay_mean`
  98.4%, `delinquent_rate` 61.4%, `payment_delay_trend` 21.6%,
  `missed_rate` 10.4%.

## 5. Backend integration

| Piece | Location |
|---|---|
| Nightly job | `scripts/run_drift_detection.py` (cron; `--limit`, `--window-days`, `--as-of`) |
| Serve-side aggregation | `app/services/drift.py` (`customer_series`, `InvoiceFacts`) |
| Scorer (fallback-safe) | `src/ml/drift/service.py` (`DriftScorer`: no artifact → unknown, not flagged) |
| Verdicts table | `customer_drift_flags` (migration `0004`, merged by `0005`) |
| Review queue | `GET /api/v1/drift/flags` + `GET /api/v1/drift/flags/{customer_id}` (bearer) |
| Model evidence | `GET /api/v1/models/drift/card` (public, with committed fallback) |
| Health signal | `customers_flagged_drift_7d` in `GET /api/v1/tasks/status` |
| Alerts | rendered + logged always; emailed only when `DRIFT_ALERT_EMAIL` is set and `DRY_RUN` is off |

Live proof (dev DB, 2026-09-04): 27 customers with actionable invoices
scored, 23 flagged with per-flag drivers, all persisted and served by the
flags endpoint. The high flag share is selection, not miscalibration —
only holders of overdue/actionable invoices are scored, and the demo book
is distressed by design (see §6).

## 6. Limitations, stated plainly

1. **Monthly training grain vs trailing-90-day serving grain.** The schema
   is rate/ratio-based so columns stay comparable, but trend slopes over
   six months and three windows are not identical quantities.
2. **The default flag is a forward-risk proxy, not drift truth.** Only the
   injected-degradation check (recall 0.694) has real ground truth.
3. **Consumer credit stands in for B2B receivables.** Same behavioral
   signals, different population. Revisit once webhook-confirmed payment
   outcomes accumulate.
4. **Serve-side approximations**: whole-invoice paid amounts attributed to
   the settlement period; open overdue invoices age into the latest
   period; credit limit proxied by lifetime billing scale.
5. **Threshold 0.0 exactly**: the score distribution has a point mass at
   zero from identical perfect-payer rows; the strict `<` cut applies and
   effective rates (above) are what is reported, not the nominal 10%.
6. **Explanations rank, they don't attribute**: drivers are
   median-relative deviations (Isolation Forest has no SHAP-style
   attribution); used for ranking only, never for the flag decision.

## 7. Reproduce

```bash
pip install -e ".[ml]"      # xlrd lives in the ml extra (training only)
python scripts/train_drift_model.py --seed 42   # downloads UCI, trains, saves
python scripts/run_drift_detection.py --limit 500
curl localhost:8000/api/v1/models/drift/card
```

## 8. Citation

Yeh, I. (2009). Default of Credit Card Clients [Dataset]. UCI Machine
Learning Repository. https://doi.org/10.24432/C55S3H. CC BY 4.0.
