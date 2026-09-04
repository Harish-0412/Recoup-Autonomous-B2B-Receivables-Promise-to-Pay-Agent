# Runbook: deploy, operate, rotate, replay

## First deploy (Render + Neon)

1. Create a Postgres database; copy its `postgresql://` URL.
2. Deploy from this repo (blueprint: `render.yaml`). Set secrets via a
   Render Environment Group, a Doppler config, or AWS Secrets Manager
   env-injection: `DATABASE_URL`, `TASK_API_KEY` (long random string),
   Razorpay test keys, `RAZORPAY_WEBHOOK_SECRET`, `RESEND_API_KEY`,
   `RESEND_FROM_EMAIL`, `RESEND_WEBHOOK_SECRET`, `REPLY_INBOUND_DOMAIN`,
   `REPLY_ADDRESS_SECRET`, `CORS_ORIGINS` (real frontend origin).
   **Do NOT write a `.env` file into the container** -- the image never ships
   one and the app never reads one at boot. A `.env` on the server is a P0
   security finding.
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

## Secret rotation via manager

- Render Path: Environment Groups → recoup-api-secrets → edit key value → Save → the web service redeploys automatically → scheduler secret updated manually if using GitHub Actions → verify via: `curl -sS https://$HOST/api/v1/tasks/status -H "Authorization: Bearer $NEW_TASK_API_KEY" | jq .sending_enabled`
- Doppler Path: `doppler secrets set TASK_API_KEY <new> --project=recoup --config=prod` (or web UI) → redeploy container so env refreshes → scheduler: `doppler secrets set TASK_API_KEY <new> --project=recoup --config=scheduler` → same curl verify
- AWS Secrets Manager Path: `aws secretsmanager put-secret-value --secret-id recoup/prod/TASK_API_KEY --secret-string <new> --region us-east-1` → force-new ECS task deployment (or redeploy Render/EC2) so the injected env refreshes → scheduler key same SM path → curl verify

NOTE: Rollout order (applies to all three paths): set on API service first; then update scheduler / operator machines; confirm new bearer returns 200 while the old value returns 401; treat old value as dead. If RAZORPAY_KEY_SECRET leaked, also rotate in the Razorpay dashboard — a publishable key ID is NOT an API credential.

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

## Backups, RPO, RTO, restore drill

| Mechanism          | RPO      | RTO       | Frequency              |
|--------------------|----------|-----------|------------------------|
| Neon PITR          | ~1 min   | < 5 min   | Continuous             |
| Weekly logical dump| 7 days   | < 30 min  | Weekly (Sun 02:00 UTC) |

### Neon PITR (branch restore to timestamp T)

1. Neon console → Projects → recoup → Branches → "New branch" → select "Restore to a point in time" radio → pick timestamp T → Branch name: restore-staging-YYYYMMDD → Create branch
2. Copy connection string from new branch (DATABASE_URL). From a terminal with alembic + psycopg installed:
   ```
   export DATABASE_URL=postgresql://user:pass@restore-staging-branch.aws.neon.tech/recoup
   alembic stamp head  # schema at T already matches T's alembic_version; stamp prevents upgrade re-running
   ```
3. Smoke: `psql $DATABASE_URL -c "SELECT count(*) FROM invoices;"`

### Weekly logical dump (S3 / Cloudflare R2)

```bash
#!/usr/bin/env bash
set -euo pipefail
TS=$(date -u +%Y%m%d)
DEST="s3://${BACKUP_BUCKET:-recoup-backups}/weekly/${TS}/recoup_tenant.dump"
pg_dump -d "$DATABASE_URL" -n public -F c -f /tmp/recoup_${TS}.dump --no-owner
aws s3 cp /tmp/recoup_${TS}.dump "$DEST" --storage-class STANDARD_IA
rm /tmp/recoup_${TS}.dump
echo "[OK] dumped to $DEST"
```

Retention: enable a bucket lifecycle rule expiring objects under `weekly/` after 90 days. On-demand manual: replace TS and run.

### Restore drill (chain verification on one invoice)

1. Pick a known-paid invoice:
   ```sql
   SELECT invoice_id, amount, amount_paid, paid_at FROM invoices WHERE amount_paid > 0 ORDER BY paid_at DESC LIMIT 5;
   ```

2. Webhook event that credited it:
   ```sql
   SELECT w.event_id, w.event_type, w.signature_verified, w.processed_at FROM webhook_events w WHERE w.payload::text LIKE '%<PAYMENT_LINK_ID>%' ORDER BY w.processed_at DESC LIMIT 5;
   ```

3. Contact + decision trace chain (contacts):
   ```sql
   SELECT c.contact_id, c.channel, c.ladder_step, c.provider_message_id, c.status, c.created_at FROM contacts c WHERE c.invoice_fk = (SELECT id FROM invoices WHERE invoice_id = '<KNOWN_PAID_INVOICE_ID>') ORDER BY c.created_at;
   ```

4. Decision traces:
   ```sql
   SELECT dt.event, dt.outcome, dt.amount, dt.reason, dt.created_at FROM decision_traces dt WHERE dt.invoice_id = '<KNOWN_PAID_INVOICE_ID>' ORDER BY dt.created_at;
   ```

Assert: `(SELECT amount_paid FROM invoices WHERE invoice_id = '<ID>')` == `(SELECT COALESCE(SUM(amount),0) FROM decision_traces WHERE invoice_id = '<ID>' AND event = 'payment:received')` within 0.01. AND: the latest contact row's provider_message_id matches the provider_message_id of the decision_traces row with event='executed' closest to that contact's created_at.

Last restore drill: 2026-09-04 — by Kiro-Agent, invoice id INV-2026-00001, verified hash chain.
