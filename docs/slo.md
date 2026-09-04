# Service Level Objectives (SLOs)

## 1) LIVENESS

**Name:** Health endpoint availability and latency

**SLI formula (exact):** `count(GET /api/v1/health that return 2xx AND latency < 500ms) / count(GET /api/v1/health requests)`

**Target %:** 99%

**Rolling window:** 5 minutes

**Error budget %:** 1% (1 in 100 requests may fail or exceed latency threshold)

**Alert thresholds:**
- 1h burn rate ≥ 14.4 → **PAGE** on-call immediately
- 1h burn rate 2–14.4 + 6h burn rate ≥ 1 → **OPEN ticket**, work within shift

**Concrete queries:**

*PromQL histogram_quantile (latency):*
```promql
histogram_quantile(
  0.99,
  sum by (le) (
    rate(
      http_server_duration_seconds_bucket{
        http_method="GET",
        http_route="/api/v1/health"
      }[5m]
    )
  )
) < 0.5
```

*PromQL ratio (success within threshold):*
```promql
(
  sum(
    rate(
      http_server_duration_seconds_bucket{
        http_method="GET",
        http_route="/api/v1/health",
        le="0.5",
        http_status_code=~"2.."
      }[5m]
    )
  )
  /
  sum(
    rate(
      http_server_duration_seconds_count{
        http_method="GET",
        http_route="/api/v1/health"
      }[5m]
    )
  )
) >= 0.99
```

---

## 2) WEBHOOK INGESTION

**Name:** Webhook and reply endpoint ingestion success

**SLI formula (exact):** `count(POST /api/v1/webhooks/* AND POST /api/v1/replies that return 2xx AND latency < 5s) / count(POST /api/v1/webhooks/* AND POST /api/v1/replies requests)`

**Target %:** 99%

**Rolling window:** 1 hour

**Error budget %:** 1% (1 in 100 requests may fail or exceed latency threshold)

**Alert thresholds:**
- 1h burn rate ≥ 14.4 → **PAGE** on-call immediately
- 1h burn rate 2–14.4 + 6h burn rate ≥ 1 → **OPEN ticket**, work within shift

**Concrete queries:**

*SQL ratio query against `webhook_events` table:*
```sql
SELECT
  COUNT(*) FILTER (
    WHERE status = 'received'
      AND http_status_code BETWEEN 200 AND 299
      AND received_at >= NOW() - INTERVAL '1 hour'
  )::float / NULLIF(
    COUNT(*) FILTER (WHERE received_at >= NOW() - INTERVAL '1 hour'),
    0
  ) AS success_ratio
FROM webhook_events;
```

*OTel `http.server.duration` PromQL query:*
```promql
(
  sum(
    rate(
      http_server_duration_seconds_bucket{
        http_method="POST",
        http_route=~"/api/v1/webhooks/.*|/api/v1/replies",
        le="5",
        http_status_code=~"2.."
      }[1h]
    )
  )
  /
  sum(
    rate(
      http_server_duration_seconds_count{
        http_method="POST",
        http_route=~"/api/v1/webhooks/.*|/api/v1/replies"
      }[1h]
    )
  )
) >= 0.99
```

---

## 3) BATCH RUN LATENCY

**Name:** Batch cycle p95 latency

**SLI formula (exact):** `histogram_quantile(0.95, span_duration_histogram_bucket{span_name="run_batch_cycle"}) < 30s` for batches ≤ `BATCH_MAX_INVOICES` (default 1920, calibrated in `docs/load_test.md`: cliff at N ≈ 2400, cap at 80%)

**Target %:** 99% of batch cycles complete within p95 < 30s

**Rolling window:** 24 hours

**Error budget %:** 1% (1 in 100 batch cycles may exceed 30s p95)

**Alert thresholds:**
- 1h burn rate ≥ 14.4 → **PAGE** on-call immediately
- 1h burn rate 2–14.4 + 6h burn rate ≥ 1 → **OPEN ticket**, work within shift

**Concrete query:**

*OTel `span_duration` histogram query on span name `run_batch_cycle`:*
```promql
histogram_quantile(
  0.95,
  sum by (le) (
    rate(
      span_duration_histogram_bucket{
        span_name="run_batch_cycle"
      }[24h]
    )
  )
) < 30
```

---

## 4) EXACTLY-ONCE WEBHOOKS

**Name:** No duplicate contacts per invoice per UTC day

**SLI formula (exact):** `SLI = 1 - (rows with same (invoice_id, channel, ladder_step, DATE created_at UTC) having COUNT(*) > 1) / total_contact_rows_per_day` — target: **0 duplicates** (i.e., no groups with COUNT > 1)

**Target %:** 100% (zero duplicate contacts)

**Rolling window:** UTC day (calendar day, 24h boundary in UTC)

**Error budget %:** 0% (any duplicate is a breach)

**Alert thresholds:**
- 1h burn rate ≥ 14.4 → **PAGE** on-call immediately (any detected duplicate triggers this)
- 1h burn rate 2–14.4 + 6h burn rate ≥ 1 → **OPEN ticket**, work within shift

**Concrete Postgres SQL query:**
```sql
SELECT invoice_id,
       channel,
       ladder_step,
       DATE(created_at AT TIME ZONE 'UTC') AS d,
       COUNT(*) c
FROM contacts
GROUP BY 1,2,3,4
HAVING COUNT(*) > 1
ORDER BY d DESC, c DESC;
```

---

## BURN-RATE TRIAGE TABLE

| Condition | Action |
|---|---|
| 1h burn ≥ 14.4 | **PAGE** on-call immediately |
| 1h burn 2–14.4 + 6h burn ≥ 1 | **OPEN ticket**, work within shift |
| Burn < 1 sustained | **MONITOR** only |

---

**Note:** SLO dashboards, burn-rate panels, and alert routing live in ops Grafana Cloud / Honeycomb. MSME customer dashboard (/dashboard) intentionally does not expose them.
