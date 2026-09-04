# Tenancy: Wave 1 row-level isolation (one Postgres, many businesses)

## Decision

Recoup v1 was deliberately single-tenant (one business per deploy, no
`business_id`, HMAC signing invoice only). Wave 1 retires that: every tenant
table carries `business_id TEXT NOT NULL`, every repository read/write filters
by it, Reply-To signs `<business>.<invoice>`, and Razorpay link notes carry
`business_id` so `payment_link.paid` cannot close another tenant's invoice.

## Schema

- `business_id` on `customers`, `invoices`, `promises`, `contact_logs`,
  `opt_outs`, `inbound_replies`, `decision_traces`, `webhook_events`,
  `batch_runs`, `customer_drift_flags`. Backfill default is the deploy's
  `Settings.BUSINESS_ID` (`'default'`).
- Composite uniqueness: `invoices (business_id, invoice_id)`,
  `customers (business_id, customer_id)`,
  `webhook_events (business_id, event_id)`,
  `promises (business_id, promise_id)`. No global unique on `invoice_id` --
  two tenants can both have INV-1042.
- Indexes on every list path: `(business_id, status, due_date)`,
  `(business_id, payment_link_id)`, plus per-table tenant indexes.
- `businesses` registry maps operator/cron keys to a business
  (`API_KEY -> business_id`; later JWT `org_id`). No `/tenants` routes in the
  agent loop.
- Optional Postgres RLS (`SET app.business_id`) remains future defense in
  depth. Repository scoping is the enforcement; RLS alone is not relied upon.

## Request scoping

- Operator routes: `require_tenant()` after `require_api_key`; every
  repository call takes `TenantContext.business_id`.
- Cron routes: `require_task_tenant()` after `require_task_key`. One key maps
  to exactly one business -- never "all tenants".
- Webhooks (public, signature-verified): tenant comes from Razorpay
  `notes.business_id` (minted by the executor). Pre-Wave-1 links without notes
  take a single audited legacy fallback that scopes mutations to the link's
  owning tenant.
- Inbound replies (public, signature-verified): tenant comes from the tagged
  address. A valid address from A never resolves into B; forgeries and
  unroutable mail land in a per-tenant review quarantine and are never
  auto-acted upon.

## Verification

- `tests/test_tenancy_isolation.py`: two businesses share one DB with
  colliding INV-1042; run-cycle scope, forged reply-to rejection, per-tenant
  payment-link lookup, per-tenant webhook idempotency, and the
  business_id-required repository wrapper.
- `scripts/seed_two_businesses.py`: manual two-tenant seed (`acme`, `globex`).
- `alembic downgrade -1 && alembic upgrade head` round-trips the Wave 1
  revision with data preserved.

## Still later

Per-tenant OPA policy, per-books-org ERP mapping, and JWT `org_id` claims.
Same APIs; no new public routes in Wave 1.
