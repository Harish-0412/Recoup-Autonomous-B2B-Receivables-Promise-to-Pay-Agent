# Tenancy: deliberately single-tenant in v1

## Decision

Recoup v1 serves **one business per deploy**. There is no `business_id`
column, no tenant claim in auth, and no per-tenant scoping in repository
queries. This is a scope choice, not an oversight: the closed loop (score ->
gate -> execute -> promise -> webhook) is the thing being proven, and
tenancy machinery would obscure it without changing any decision the agent
makes.

## What this costs later

Retrofitting tenancy touches every table that holds customer data
(`customers`, `invoices`, `promises`, `contact_logs`, `opt_outs`,
`inbound_replies`, `decision_traces`, `webhook_events`, `batch_runs`) plus
every repository query and every HMAC/signature scope. That is why the
decision is recorded here rather than left implicit: the longer it stays
undecided, the more expensive the migration.

## Groundwork already in place

- `BUSINESS_ID` (default `"default"`) identifies the owner of a deploy.
  It is returned by `GET /api/v1/tasks/status` and recorded wherever a run
  summary is persisted, so a future split migration has a stable owner key.
- Auth is a single operator bearer (`TASK_API_KEY`, optional `API_KEY`
  alias). Multi-tenancy will need per-tenant credentials; the current shape
  -- one secret, constant-time compare, deny-when-unset -- is the seam that
  gets replaced, not extended.
- `Reply-To` routing signs `<invoice>` only. A tenanted form must sign
  `<business>.<invoice>` so a valid address from one tenant cannot record a
  promise in another.

## When to revisit

When a second real business onboards, not before. The migration then is:
add `business_id` (defaulting to the deploy's `BUSINESS_ID`), backfill,
scope every repository query, re-scope reply routing and webhook matching,
and issue per-tenant keys. Until that day, running one deploy per business
is the supported topology.
