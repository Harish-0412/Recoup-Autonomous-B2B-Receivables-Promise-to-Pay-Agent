# Backend Status

Where the Recoup backend actually stands, what was decided to build, what is in
progress, and what is left. Written against the code on `main`, not against
intentions — every "done" below was verified by running it, and the way it was
verified is stated so you can re-run it.

Last updated: 2026-09-03 · Phases 0–4 of the eight-phase backend plan complete.

---

## 1. At a glance

| # | Phase | Status | Evidence |
|---|---|---|---|
| 00 | Make a clean clone installable | ✅ **Done** | Fresh venv → `pip install -e ".[dev]"` → 552 tests, zero manual installs |
| 01 | One source of schema truth | ✅ **Done** | Empty DB → `upgrade head` → `downgrade base` → `upgrade head`, all 3 migrations |
| 02 | Wire the executor | ✅ **Done** | Real Razorpay link `plink_TXVSdg7K0skWhG` created and fetched back |
| 03 | Close the promise loop | ✅ **Done** | "we will clear this by Friday" → promise row with parsed date |
| 04 | Make it autonomous | ✅ **Done** | 5 concurrent triggers → exactly 1 run, 0 duplicate contacts |
| 05 | Guard the perimeter | 🟡 **Partial** | Task endpoints authenticated; the rest of the API is still open |
| 06 | Test the layer that talks to the world | 🔴 **Not started** | 552 tests, but the HTTP layer is still thinly covered |
| 07 | Ship it | 🔴 **Not started** | No Dockerfile, no release command, no host config |

**Health:** 552 tests passing · `ruff check` clean · `ruff format` clean ·
`mypy app/` clean (44 source files).

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

### Phase 5 — Guard the perimeter 🟡

**Partially done, and the done half was pulled forward out of necessity.** The
task endpoints send real email, so an unauthenticated trigger is an
unauthenticated way to mail an entire customer book — they could not wait.

Already landed:

- Bearer-token auth on `/api/v1/tasks/*`, constant-time comparison, and an
  **unset key denies rather than allows**.
- `DEBUG` defaults to `False` — it used to default `True`, so an unconfigured
  deploy published `/docs` and widened CORS. Deliberately *not* derived from
  `APP_ENV`, because `APP_ENV` itself defaults to `"development"` and deriving it
  restores the bug exactly.
- `CORS_ORIGINS` replaces the `"*"` branch, which browsers reject anyway when
  paired with `allow_credentials`.
- Production refuses to boot while any secret still holds its placeholder.

Still open:

- **Every other route is unauthenticated** — including `POST /invoices/batch`
  and `POST /invoices/{id}/run-cycle`.
- **No rate limiting** on ingest or the reply webhook.
- **Multi-tenancy is undecided.** Either add `business_id` to the tables now and
  scope every repository query, or document single-tenant as a deliberate scope
  choice. Retrofitting tenancy across seven tables later is far more expensive
  than adding it now — this is the one open decision that gets costlier by the
  day.

---

## 5. What remains

### Phase 6 — Test the layer that talks to the world 🔴 · ~1 day

552 tests, and the HTTP layer is still the thinnest part. Every bug in section 3
lived there.

- Real Postgres in CI (already a service container) rather than SQLite — the
  schema uses Postgres enums and the migrations must be exercised.
- Each test in a transaction, rolled back, so tests share one schema and stay
  independent.
- Cover the paths that carry money or promises: webhook replay is idempotent,
  bad signatures rejected, `run-cycle` persists trace and contact atomically, a
  failed send does not advance the ladder, protected routes 401.
- Fake the providers at the client boundary so no test touches the network.
- Coverage gates on `app/api` and `app/services`.

**Done when:** API and service coverage clears 80%, and the executor's failure
paths are tested as thoroughly as its happy path.

### Phase 7 — Ship it 🔴 · ~1 day

Nothing above is provable to an outsider until it runs somewhere with a public
URL — which both webhooks require.

- Multi-stage Dockerfile on `python:3.11-slim`, non-root, no build toolchain in
  the final layer.
- Release command runs `alembic upgrade head`; web process runs Uvicorn.
- Health checks at the existing `/api/v1/health`.
- Render or Railway + Neon Postgres — both give the HTTPS URL the webhooks need.
- Register live webhook URLs with Razorpay and Resend; set CORS to the real
  frontend origin.
- Request-ID middleware feeding the existing structlog setup; Sentry for
  unhandled errors.
- `docs/runbook.md`: environment variables, first deploy, rotating a leaked key,
  replaying a failed webhook.

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
| POST | `/api/v1/invoices/batch` | ⚠️ none | Ingest customers and invoices |
| GET | `/api/v1/invoices/{id}` | ⚠️ none | Current state and promises |
| GET | `/api/v1/invoices/{id}/audit` | ⚠️ none | Hash-chained decision trail |
| POST | `/api/v1/invoices/{id}/run-cycle` | ⚠️ none | One decision cycle, executed |
| POST | `/api/v1/webhooks/razorpay` | signature | Payment confirmation |
| POST | `/api/v1/replies` | signature | Inbound customer reply |
| GET | `/api/v1/replies/review` | ⚠️ none | Human review queue |
| POST | `/api/v1/replies/{id}/reviewed` | ⚠️ none | Clear one from the queue |
| POST | `/api/v1/tasks/run-batch` | bearer | Autonomous run |
| GET | `/api/v1/tasks/status` | bearer | Kill switch and queue depths |
| GET | `/api/v1/reports/batch` | ⚠️ none | In-memory batch report |
| GET | `/api/v1/policy` | ⚠️ none | Active policy configuration |

⚠️ marks routes that phase 5 still has to close.
