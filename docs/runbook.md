# Runbook: deploy, operate, rotate, replay

## First deploy (Render + Neon)

1. Create a Postgres database; copy its `postgresql://` URL.
2. Deploy from this repo (blueprint: `render.yaml`). Set secrets:
   `DATABASE_URL`, `TASK_API_KEY` (long random string), Razorpay test keys,
   `RAZORPAY_WEBHOOK_SECRET`, `RESEND_API_KEY`, `RESEND_FROM_EMAIL`,
   `RESEND_WEBHOOK_SECRET`, `REPLY_INBOUND_DOMAIN`, `REPLY_ADDRESS_SECRET`,
   `CORS_ORIGINS` (real frontend origin).
3. Release runs `alembic upgrade head` before Uvicorn boots (see
   `Dockerfile` CMD). Confirm `/api/v1/health` is healthy.
4. `APP_ENV=production` refuses to boot on placeholder secrets -- that 503
   at release time is the guard working, not a bug.
5. Register webhook URLs with Razorpay (`/api/v1/webhooks/razorpay`) and
   Resend (`/api/v1/replies`); point reply-domain MX at Resend.
6. Enable ONE scheduler: the GitHub `scheduler` workflow (set
   `RECOUP_API_URL` + `TASK_API_KEY` secrets) or the Render cron in
   `render.yaml` -- not both.
7. Smoke test: `TASK_API_KEY=... python scripts/trigger_batch.py
   --url https://<host>`; then `GET /api/v1/tasks/status` to confirm the
   kill switch state.

## Rotating a leaked key

`TASK_API_KEY` (or `API_KEY`) appears in logs, a screenshot, or -- as once
happened here -- hardcoded in source:

1. Generate a new random value; set it on the API service first.
2. Update the scheduler secret (`TASK_API_KEY`) and any operator machines.
3. Confirm: `curl /api/v1/tasks/status -H "Authorization: Bearer $NEW"`
   returns 200 while the old value returns 401.
4. Treat the old value as dead; it cannot be "unseen". If a Razorpay key
   leaked, rotate it in the Razorpay dashboard too -- a publishable key ID
   is never an API credential and must never be accepted as one.

## Replaying a failed webhook

- Razorpay deliveries are stored in `webhook_events` keyed by event id;
  repeats are no-ops (`duplicate`). A handler crash stores the row with
  `processing_error` and still answers 200, so Razorpay stops retrying --
  find it with `select * from webhook_events where processing_error is not
  null`, fix the cause, then ask Razorpay to redeliver that event id.
- Resend inbound replies dedupe on the provider message id the same way;
  a retried delivery answers `duplicate` with the original disposition.

## Kill switch

`SENDING_ENABLED=false` halts all outbound with no redeploy and advances
nothing -- flipping it back on resumes where the agent left off. Confirm
the live value at `GET /api/v1/tasks/status` (field `sending_enabled`).
`DRY_RUN=true` is the separate development mode: full render + log, nothing
delivered, contacts recorded as SIMULATED.

## Rate limits

`RATE_LIMIT_PER_MINUTE` (default 60, per IP, per process) caps ingest and
both webhooks. A 429 carries `Retry-After`; raise the value or put a shared
limiter (proxy/Redis) in front for multi-replica exactness.
