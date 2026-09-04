# Load Test Report — `run-batch` Capacity, Multi-Replica Advisory Lock, and `BATCH_MAX_INVOICES`

## 1. SLO Under Test

As defined in Wave 3 (`docs/slo.md`):
* **Batch Run Latency SLO:** `run_batch_cycle` **wall p95 < 30.0s**, rolling 24-hour window, for batches up to `BATCH_MAX_INVOICES`.
* **Single Execution Invariant:** Exactly **one** batch worker may execute for a given tenant at any moment. Concurrent triggers must yield exactly one active run (`ran=true`), with all competing triggers gracefully returning `ran=false` ("already in progress").

This load test evaluates the platform across book sizes $N \in [50, 2400]$, observes DB connection pooling and provider mock latency, measures the scaling cliff where p95 batch duration approaches the 30s SLO, and establishes the operational ceiling: **`BATCH_MAX_INVOICES = 1920`** (80% of $N \approx 2400$).

---

## 2. Load Testing Harness & Staging Execution

### 2.1 Staging Safeguards (STAGING ONLY)

> [!CAUTION]
> **Never load-test production webhooks or production databases.**
> Seeding load-test invoices or hammering production webhooks triggers real provider rate limits, pollutes the production audit trail, and creates self-inflicted outages.
>
> All test runners (`run_batch_load.py`, `locustfile.py`, `load_test.js`) enforce preconditions:
> 1. Host validation: Immediate abort if hostname matches production markers (`prod`, `api.recoup`, `recoup.in`).
> 2. Status verification: Query `GET /api/v1/tasks/status` and assert `dry_run=true` and `sending_enabled=true`.

### 2.2 Test Runners

Three load testing harnesses are checked in:

1. **Python / AsyncIO Runner:**
   ```bash
   python scripts/load_test/run_batch_load.py \
       --base-url http://staging:8000 \
       --api-key $STAGING_API_KEY \
       --task-key $STAGING_TASK_KEY \
       --sizes 50 100 200 400 800 1600 2400 \
       --repeats 3 \
       --out data/load_test/results.json
   ```

2. **Locust (Multi-User Distributed / Headless):**
   ```bash
   locust -f scripts/load_test/locustfile.py --headless \
       -u 10 -r 5 --run-time 2m \
       --host http://staging:8000
   ```

3. **k6 (Concurrent Scenario & Lock Verification):**
   ```bash
   k6 run --env BASE_URL=http://staging:8000 \
       --env TASK_API_KEY=$STAGING_TASK_KEY \
       scripts/load_test/load_test.js
   ```

---

## 3. Measured Results & Metrics

### 3.1 Scaling Latency vs Batch Size $N$

*Configuration: Staging cluster (Python 3.13, FastAPI, async SQLAlchemy engine, PostgreSQL with connection pool, Razorpay DRY_RUN test doubles).*

| $N$ (Invoices) | Wall Mean (s) | Wall p95 (s) | Per-Invoice p95 (ms) | Invoices Considered | Status vs SLO (< 30s) |
|---|---|---|---|---|---|
| **50** | 0.54 | 0.55 | 11.0 | 50 | **PASS** (1.8% of budget) |
| **100** | 1.05 | 1.06 | 10.6 | 100 | **PASS** (3.5% of budget) |
| **200** | 2.12 | 2.15 | 10.8 | 200 | **PASS** (7.2% of budget) |
| **400** | 4.26 | 4.55 | 11.4 | 400 | **PASS** (15.2% of budget) |
| **800** | 8.59 | 9.39 | 11.7 | 800 | **PASS** (31.3% of budget) |
| **1600** | 18.70 | 20.60 | 12.9 | 1600 | **PASS** (68.7% of budget) |
| **2400** | 26.00 | **28.90** | 12.1 | 2400 | **PASS** (**96.3% of budget — CLIFF**) |
| **3200** *(Projected)* | 35.80 | 39.20 | 12.2 | 3200 | **FAIL** (Exceeds 30s SLO) |

### 3.2 Key Bottleneck & Latency Breakdown

1. **Linear Evaluation Overhead:**
   Processing time scales linearly at approximately **11.5–12.5 ms per invoice**. Each invoice undergoes case snapshot construction, recovery scoring, policy rule evaluation, and dry-run template rendering.
2. **SLO Cliff Identification:**
   At **$N \approx 2400$**, the p95 wall clock latency reaches **28.90s**, dangerously close to the 30.0s SLO ceiling. Batches exceeding 2400 invoices risk violating the SLO due to transient CPU or network fluctuations.
3. **Operational Cap:**
   To provide an adequate 20% safety buffer for cron jitter, database pool queuing, and network latency, the operational cap is set to:
   $$\text{BATCH\_MAX\_INVOICES} = 2400 \times 0.80 = \mathbf{1920}$$

---

## 4. Component & Resource Observations Under Load

### 4.1 Database Connection Pool
* **Configuration:** SQLAlchemy async connection pool (`pool_size=20`, `max_overflow=10`, `pool_pre_ping=True`, `pool_recycle=1800`).
* **Observed Peak Active Connections:** 7 connections during peak $N=2400$ processing.
* **Connection Checkout Latency:** Mean = 0.8 ms, p95 = 2.4 ms.
* **Pool Starvation:** Zero timeouts or pool exhaustion observed across all test sizes.

### 4.2 Razorpay Mock Latency
* **Test Double Behavior:** In `DRY_RUN=true` mode, payment links and customer fetch calls execute against local in-memory doubles.
* **Latency Profile:**
  - Mean latency: **1.1 ms**
  - p95 latency: **1.8 ms**
  - Max recorded: **4.2 ms**
* *Note:* When operating in production with live Razorpay API calls, network round-trips add 120–250 ms per invoice unless batching/caching is utilized.

---

## 5. Multi-Replica Advisory Lock Contention Proof

### 5.1 Architecture
Recoup implements advisory locking via PostgreSQL `pg_try_advisory_lock` (with an in-process asyncio fallback for single-node SQLite environments). When a batch run is triggered:
* Worker attempts non-blocking lock acquisition for `(TENANT_HASH, BATCH_NAMESPACE)`.
* If acquired: worker executes the batch run and releases the lock in a `finally` block upon completion.
* If unacquired (lock held by another worker/replica): worker returns immediately with HTTP 200 `{"ran": false, "reason": "Batch run already in progress"}`.

### 5.2 Contention Test: 5 Concurrent Triggers

During testing across two active replicas on staging, 5 simultaneous triggers were fired at $T_0$:

```json
[
  {"worker": "replica-1", "trigger": 1, "status": 200, "ran": true,  "invoices_considered": 1920},
  {"worker": "replica-2", "trigger": 2, "status": 200, "ran": false, "reason": "Batch run already in progress"},
  {"worker": "replica-1", "trigger": 3, "status": 200, "ran": false, "reason": "Batch run already in progress"},
  {"worker": "replica-2", "trigger": 4, "status": 200, "ran": false, "reason": "Batch run already in progress"},
  {"worker": "replica-1", "trigger": 5, "status": 200, "ran": false, "reason": "Batch run already in progress"}
]
```

**Result:**
* **Fired:** 5 triggers
* **Winners (`ran=true`):** **Exactly 1**
* **Blocked (`ran=false`):** 4
* **Double-send or Duplicate Actions:** **0**
* **Advisory lock integrity:** **VERIFIED (100% single-winner invariant preserved)**.

---

## 6. Operating Guidelines & Runbook Rules

1. **Fixed Cap Enforcement:** `BATCH_MAX_INVOICES = 1920` is configured in `app/core/config.py` and `.env.example`.
2. **Book Growth Beyond 1920:** If a tenant's overdue portfolio exceeds 1920 open invoices, the solution is **temporal sharding** (e.g., dispatching two staggered half-hour crons with distinct filters) or **tenant partitioning**, never blindly increasing `BATCH_MAX_INVOICES`.
3. **Re-Test Requirement:** If the primary recovery scorer switches from rules-based to heavy tree/neural inference in production, or if provider network latency shifts significantly, this load test must be re-run before adjusting `BATCH_MAX_INVOICES`.
