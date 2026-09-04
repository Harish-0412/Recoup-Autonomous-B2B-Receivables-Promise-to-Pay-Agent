# Backend Status

Where the Recoup backend actually stands, what was decided to build, what is in
progress, and what is left. Written against the code on `main`, not against
intentions — every "done" below was verified by running it, and the way it was
verified is stated so you can re-run it.

Last updated: 2026-09-04 · Phases 0–5 of the eight-phase backend plan complete.

---

## 1. At a glance

| # | Phase | Status | Evidence |
|---|---|---|---|
| 00 | Make a clean clone installable | ✅ **Done** | Fresh venv → `pip install -e ".[dev]"` → 552 tests, zero manual installs |
| 01 | One source of schema truth | ✅ **Done** | Empty DB → `upgrade head` → `downgrade base` → `upgrade head`, all 3 migrations |
| 02 | Wire the executor | ✅ **Done** | Real Razorpay link `plink_TXVSdg7K0skWhG` created and fetched back |
| 03 | Close the promise loop | ✅ **Done** | "we will clear this by Friday" → promise row with parsed date |
| 04 | Make it autonomous | ✅ **Done** | 5 concurrent triggers → exactly 1 run, 0 duplicate contacts; external cron ships (Actions + Render) |
| 05 | Guard the perimeter | ✅ **Done** | All operator routes bearer-locked, rate-limited, 16-test perimeter suite |
| 06 | Test the layer that talks to the world | 🟡 **Partial** | API perimeter covered; provider-bound failure paths still thin |
| 07 | Ship it | 🟡 **Partial** | Dockerfile + Render blueprint + runbook land; no live deploy yet |

**Health:** 593 tests passing · `ruff check` clean · `ruff format` clean ·
`mypy app/` clean (51 source files).

---

## 2. What is done

### Phase 0 — the project installs

`pyproject.toml` is the only dependency manifest. `requirements.txt` was deleted
rather than kept in sync, because two lists that must agree are one list that
eventually does not.

Three things were actively broken before this:

- `pyproject` pinned **asyncpg** while `app/core/config.py` rewrites every URL
  to `postgresql+psycopg`, so `import app.models` raised `ModuleNotFoundError`
  on any clean install. CI was red for reasons unrelated to the code.
- It pinned `razorpay==1.4.1`, which imports `pkg_resources` — gone from modern
  setuptools, so `import razorpay` raised outright.
- It pinned `groq==0.6.0`, which passes `proxies=` to httpx ≥0.28, which
  removed it.

ML dependencies moved to an `[ml]` extra so the API container does not carry
~500 MB of wheels it never imports; `[dev]` depends on `[ml]` because the ML
tests import it directly.

### Phase 1 — Alembic owns the schema

`init_db()` used to run `Base.metadata.create_all` on every boot *alongside* an
Alembic baseline. Two sources of truth that agree until the first model change —
and a database built by `create_all` carries no version row, so
`alembic upgrade head` can never be applied to it without a manual stamp.

- `create_all` removed. Migrations are a release step: `alembic upgrade head`.
- Startup now checks the database is **reachable** instead, so a bad
  `DATABASE_URL` fails the release rather than the first request.
- `DB_POOL_MODE=null` for serverless Postgres — a client-side pool behind
  PgBouncer holds connections the pooler is also trying to manage.
- `scripts/seed_demo.py` loads a seeded synthetic batch into a real database.
  It creates no tables and writes no ground truth.

### Phase 2 — the executor

**The gap this closed:** `trigger_cycle` decided correctly, gated correctly, and
then wrote a `ContactLog` row for a message nobody had sent. The contact caps,
the frequency gate and every recovery figure were computed from rows describing
events that never happened.

`app/services/executor/` — four modules, each testable without the others:

| Module | Responsibility |
|---|---|
| `contract.py` | `ExecutionIntent`, constructible **only** from an approved `PolicyDecision` |
| `templates.py` | Pure per-rung rendering. No I/O, so dry run produces byte-identical copy |
| `gateways.py` | Async adapters over the sync SDKs, with timeouts and dry-run peers |
| `service.py` | The send-then-persist ordering |

Properties that hold:

- **The gate cannot be bypassed.** There is no way to hand the executor a bare
  proposal — no `ExecutionIntent` can be built from one. Non-contacting actions
  (`HAND_OFF`, `CLOSE`) and mismatched invoice IDs are refused too.
- **Send first, persist second.** A crash between write and send would otherwise
  leave a database claiming contact was made. The reverse failure — a delivered
  message with no row — merely makes the next cap check conservative.
- **A failed send costs nothing.** `FAILED` row written, `ladder_index`
  untouched, next run retries the same rung. `contacts_sent_count` excludes
  `FAILED`, so an outage cannot burn through the caps while sending nothing.
- **Links are reused while payable**, so a customer never holds several live
  links for one invoice.
- **`DRY_RUN` defaults to true.** A deployment that has not deliberately said
  "send real mail to real customers" does not.

**A flaw the live API found:** Razorpay caps payment links at **₹5,00,000**
(₹5,00,000 accepted, ₹5,20,000 refused). The executor required a link before
sending, so an invoice over the cap got *no reminder at all* — the largest debts
in the book were silently exempt. Now the cap is checked before the call and the
message goes out without a link, asking for bank transfer details.

### Phase 3 — the promise loop

`POST /api/v1/replies` — the half of Promise-to-Pay that listens.

```
verify signature → dedupe → route to invoice → classify → act
```

- **Svix signature verification**, implemented rather than pulled in, with a
  freshness window — a signature with no timestamp check is a replay waiting to
  happen.
- **Deduped on provider message ID**, through the same `webhook_events` pattern
  Razorpay uses, plus a unique `reply_id`.
- **Routed by a signed reply-to address** — `reply+<invoice>.<hmac>@domain` —
  never the subject line. The HMAC is the point: without it, anyone who guessed
  the format could record a promise against any invoice and silence the agent on
  a live debt.
- **Low confidence goes to a person.** `NEEDS_REVIEW` and no promise;
  `GET /api/v1/replies/review` is a real queue. Unroutable replies are kept, not
  dropped — those are the ones most worth a human's time.
- **Opt-out is handled ahead of classification** and honoured even when the reply
  cannot be matched to an invoice.

`install_cascade_classifier()` is now called at startup. Its own docstring said
to call it there and nothing did, so the trained classifier had been dead code
and every reply fell back to the LLM.

### Phase 4 — autonomous runs

`POST /api/v1/tasks/run-batch`, driven by an **external** cron.

An in-process scheduler is one line to add and double-fires the moment a second
replica starts — precisely when the service is busy enough to need one. So the
schedule lives outside, and the app defends itself:

- **Postgres advisory lock.** A second concurrent trigger is a no-op, not a
  queued rerun (waiting would simply do the double-send later). Session-scoped,
  so a process that dies holding it releases it on disconnect — no stale-lock
  timeout to guess at.
- **Promise sweep runs first.** Lapsed unpaid promises are marked `BROKEN` and
  the invoice is released from `PROMISED`. This is what re-arms escalation: the
  policy gate stays quiet while a promise is open, so an unbroken lapsed promise
  silences the agent on that invoice *forever*.
- **Kill switch** (`SENDING_ENABLED=false`) halts all outbound with no redeploy,
  and **advances nothing** — so turning it back on resumes where the agent left
  off rather than finding every invoice a rung further along.
- **Bounded batch** (`BATCH_MAX_INVOICES`) and a full `RunSummary`: scored,
  acted, blocked, left alone, failed, handed off, promises checked/broken/kept.
- One bad invoice is recorded and skipped; a batch that dies on invoice 40 of
  200 would leave 160 untouched with no record of why.

---

## 3. What was verified, and how

Nothing below is mocked unless it says so.

| Claim | How it was checked |
|---|---|
| A clean clone installs | Fresh venv, `pip install -e ".[dev]"`, 552 tests, zero manual installs, `asyncpg` confirmed absent |
| Migrations reverse | Empty DB → `upgrade head` → `downgrade base` → `upgrade head`, real Postgres |
| Payment links are real | `plink_TXVSdg7K0skWhG` → `https://rzp.io/rzp/skBiePkr`, fetched back `status=created` with `invoice_id` in its notes |
| A failed send costs nothing | Real Razorpay auth failure ×3: all on `reminder_1`, `ladder_index` still 0, contacts-counted still 0 |
| Payment closes the invoice | Webhook against that real link ID → `PAID`, `chain_verified: true` |
| A reply becomes a promise | "we will clear this by Friday" → `PROMISE_TO_PAY` @ 0.63 → ₹5,20,000 by 2026-09-04, invoice `PROMISED` |
| Low confidence is queued | Vague replies → `NEEDS_REVIEW`, no promise |
| Opt-out is enforced | "Please unsubscribe" → `OptOut` → next cycle blocked: *"Customer has opted out of contact on this channel."* |
| Forged reply addresses fail | Tampered tag and plain addresses both resolve to `None` |
| Webhooks reject forgeries | Unsigned and tampered → 401, both providers |
| Concurrent triggers | 5 simultaneous → exactly 1 ran, 4 no-ops, **0** extra contacts |
| Kill switch | 40 scored, 0 acted, 0 contact rows, ladder sum 0 — then flipped on, delivered exactly those 24 |
| Promise sweep | Lapsed-unpaid → `BROKEN` + released; lapsed-paid → `KEPT`; future → untouched |

### Bugs found by running it that no test had caught

Each of these lived in code that had never actually executed.

1. **`GET /invoices/{id}` always 500'd** — read a lazy relationship, which
   raises `MissingGreenlet` under asyncio.
2. **Razorpay signature verification never worked** — called on the class rather
   than the client's instance, so the payload bound to `self`. It failed closed,
   but no webhook was ever processed.
3. **Every duplicate webhook 500'd** — the log line passed `event=` to structlog,
   which reserves that name for the message.
4. **`chain_verified` was false for every untouched ledger** — worse than no
   integrity check, because it teaches people to ignore the one signal meant to
   matter. Two independent causes: the digest covered `seq`, which
   `persist_ledger` re-bases on write; and `recorded_at` was hashed as UTC but
   read back in the session timezone.
5. **The payment-link cap** (above) — silently exempting the largest invoices.

---

## 4. What is in progress

### Phase 5 — Guard the perimeter ✅

**Done 2026-09-04.** The remaining open routes are closed, and the one
regression this phase caught is worth stating plainly: an uncommitted change
had widened `require_task_key` to accept `RAZORPAY_KEY_ID` and two hardcoded
dev strings, so the publishable half of the Razorpay pair unlocked the task
endpoints and an unconfigured deploy answered 401 instead of the documented
503. `test_an_unconfigured_key_denies_rather_than_allows` caught it; the fix
removes every fallback from the code (the only credential source is
`TASK_API_KEY`, with `API_KEY` as an optional dashboard alias), rotates the
leaked value out of `.env`, and strips the same string from the frontend and
`frontend/.env.local`.

What landed:

- Bearer-token auth on **every** operator route (`invoices/*`, `reports/*`,
  `policy/*`, `replies/review`, `replies/*/reviewed`,
  `replies/classify-preview`) via `require_api_key`, which falls back to
  `TASK_API_KEY` so a single-operator deploy needs one secret. Health and
  the recovery model card stay public; both webhooks stay signature-authed.
- **Rate limiting** (`app/core/ratelimit.py`, `RATE_LIMIT_PER_MINUTE=60`):
  per-IP sliding windows on ingest and both webhooks, 429 + `Retry-After`
  instead of falling over. Process-local by design — documented as such.
- **Multi-tenancy decided:** deliberately single-tenant in v1, recorded in
  `docs/tenancy.md` with the migration cost spelled out. `BUSINESS_ID`
  reserves the owner key; `/tasks/status` surfaces it.
- `tests/test_api_security.py`: 16 tests asserting 401-without-key on all 11
  protected route shapes, 401-for-publishable-ID, 503-when-unconfigured,
  and limiter window semantics.
- Frontend sends the operator key on every call (`operatorHeaders()` backed
  by tab-session + env) and ships no credential fallback.

Still open (accepted, not overlooked): per-tenant credentials arrive with
tenancy itself; the limiter is per-process until a shared one is deployed.

---

## 5. What remains

### Phase 6 — Test the layer that talks to the world 🟡

The HTTP perimeter is now covered (`tests/test_api_security.py`: auth on all
11 protected route shapes, backdoor refusal, 503 semantics, limiter windows),
and every bug in section 3's list would have been caught one layer earlier.
What remains is provider-bound failure-path depth: webhook replay
idempotency under concurrency, `run-cycle` trace/contact atomicity, and a
failed send not advancing the ladder — all still exercised by hand, not by
test. Coverage gates on `app/api` and `app/services` are the remaining item.

### Phase 7 — Ship it 🟡

The provable-to-an-outsider half is landed; the running-somewhere half is
not:

- Multi-stage `Dockerfile` on `python:3.11-slim`, non-root, no build
  toolchain in the final layer. Release runs `alembic upgrade head`; the web
  process runs Uvicorn; health checks hit `/api/v1/health`.
- `render.yaml` blueprint (web + Postgres + optional cron) and the GitHub
  `scheduler` workflow (every 15 min, `workflow_dispatch` for manual runs).
  Enable ONE scheduler, not both. `scripts/trigger_batch.py` is the local
  form of the same call for pre-schedule smoke tests.
- `docs/runbook.md`: first deploy, rotating a leaked key, replaying a failed
  webhook, the kill switch, rate limits.
- `aiosqlite` declared in `pyproject.toml` so SQLite dev/test installs work
  from a clean clone; `.env.example` documents every Razorpay / Resend / LLM
  key plus the new `API_KEY`, `RATE_LIMIT_PER_MINUTE`, `BUSINESS_ID`.

**Done when:** a clean clone deploys green, a test-mode payment on the live URL
closes an invoice end to end, and a reply to a sent reminder creates a promise —
all observed in production logs.

### Explicitly out of scope (deferred, not forgotten)

- Multi-horizon survival-style recovery prediction (the binary model ships).
- Fine-tuned NER for entity extraction — regex + `dateparser` covers it at far
  lower cost.
- Online/continual retraining from real outcomes — sensible once real
  webhook-confirmed payment data accumulates, not before.
- WhatsApp delivery — the channel is declared in the enum and the opt-out
  registry is channel-aware, but nothing is wired.

---

## 6. Known limitations, stated plainly

- **No live email has ever been sent.** Everything up to the network call is
  proven, and the Razorpay half is proven against the real API. Sending needs a
  `RESEND_API_KEY`; note that Resend's free tier only delivers to the account
  owner's address until a domain is verified.
- **The inbound reply half cannot run from localhost at all.** It needs a domain
  with MX records pointed at Resend and a public URL.
- **The ML models are trained on synthetic data.** The metrics measure whether
  the pipeline recovers a signal that was deliberately planted — evidence the
  machinery is correct, not evidence of real-world accuracy.
- **The labels are observational, not causal.** The generator samples each
  outcome independently of what the agent does, so the model predicts who *will*
  pay, not who pays *because* the agent acted. Measuring the latter needs a
  holdout arm this simulation does not have.
- **No load testing.** `BATCH_MAX_INVOICES=200` is a sensible bound, not a
  measured one.

---

## 7. Running it

```bash
python3.11 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
cp .env.example .env          # fill in the values
alembic upgrade head          # schema is never created by the app
python scripts/seed_demo.py --batch-size 120 --seed 42
uvicorn app.main:app --reload
```

Trigger an autonomous run:

```bash
curl -X POST localhost:8000/api/v1/tasks/run-batch -H "Authorization: Bearer $TASK_API_KEY"
```

Check what the agent believes about itself, including whether the kill switch is
on:

```bash
curl localhost:8000/api/v1/tasks/status -H "Authorization: Bearer $TASK_API_KEY"
```

### Endpoint map

| Method | Path | Auth | Purpose |
|---|---|---|---|
| GET | `/api/v1/health` | — | Liveness |
| POST | `/api/v1/invoices/batch` | bearer + rate-limit | Ingest customers and invoices |
| GET | `/api/v1/invoices` | bearer | Work-queue list |
| GET | `/api/v1/invoices/{id}` | bearer | Current state and promises |
| GET | `/api/v1/invoices/{id}/audit` | bearer | Hash-chained decision trail |
| POST | `/api/v1/invoices/{id}/run-cycle` | bearer | One decision cycle, executed |
| POST | `/api/v1/webhooks/razorpay` | signature + rate-limit | Payment confirmation |
| POST | `/api/v1/replies` | signature + rate-limit | Inbound customer reply |
| POST | `/api/v1/replies/classify-preview` | bearer | Side-effect-free classify box |
| GET | `/api/v1/replies/review` | bearer | Human review queue |
| POST | `/api/v1/replies/{id}/reviewed` | bearer | Clear one from the queue |
| POST | `/api/v1/tasks/run-batch` | bearer | Autonomous run |
| GET | `/api/v1/tasks/status` | bearer | Kill switch and queue depths |
| GET | `/api/v1/tasks/runs/latest` | bearer | Latest run summary |
| GET | `/api/v1/tasks/runs` | bearer | Run history |
| GET | `/api/v1/reports/batch` | bearer | In-memory batch report |
| GET | `/api/v1/forecast/cash` | bearer | Probabilistic 7/30-day cash forecast |
| GET | `/api/v1/forecast/cash/card` | bearer | Forecast validation (coverage, bias) |
| GET | `/api/v1/policy` | bearer | Active policy configuration |
| POST | `/api/v1/policy/simulate` | bearer | Counterfactual replay (501: not built) |
| GET | `/api/v1/models/recovery/card` | — | Public model evidence |
| GET | `/api/v1/models/drift/card` | — | Public drift-model evidence |
| GET | `/api/v1/models/timing/card` | — | Public contact-timing model evidence |
| GET | `/api/v1/drift/flags` | bearer | Nightly drift verdicts, newest first |
| GET | `/api/v1/drift/flags/{customer_id}` | bearer | Latest drift verdict, one customer |
| GET | `/api/v1/schedule/next_time` | bearer | Optimal reminder send-time recommendation |
| POST | `/api/score/broken_promise` | bearer | Real-time broken-promise risk score |
| GET | `/api/score/broken_promise/card` | — | Public broken-promise model card |
