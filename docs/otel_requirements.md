# OpenTelemetry Requirements - Wave 3 Implementation

## Overview

The Wave 3 OpenTelemetry implementation provides comprehensive distributed tracing for the Recoup autonomous receivables system. This document specifies the exact instrumentation requirements and verification procedures.

## Instrumentation Coverage

### 1. FastAPI HTTP Requests
- **Auto-instrumented**: All HTTP endpoints except health checks (`/health`, `/api/v1/health`, `/api/v1/ready`, `/health/db`)
- **Spans include**: HTTP method, route, status code, duration
- **Custom attributes**: Request ID, business ID, user agent
- **Health endpoints excluded**: To prevent trace store noise from liveness probes

### 2. SQLAlchemy Database Operations
- **Auto-instrumented**: All async SQLAlchemy operations
- **Spans include**: SQL queries, connection info, duration
- **SQL commenting**: Enabled with OpenTelemetry values for correlation
- **Covers**: Invoice queries, customer lookups, decision trace writes, webhook processing

### 3. HTTP Client Calls (httpx)
- **Auto-instrumented**: All outbound HTTP calls
- **Covers**: Razorpay API calls, Resend email API, LLM inference calls, ERP integrations
- **Spans include**: URL, method, status code, duration, retry attempts

### 4. Redis Operations
- **Auto-instrumented**: Rate limiting cache operations
- **Spans include**: Command type, key patterns, response times
- **Covers**: Shared rate limiter, distributed locks, session storage

### 5. Custom Business Logic Spans
- **Batch execution cycles**: Root span `run_batch_cycle` with child spans for each invoice
- **Invoice processing**: Individual `cycle.invoice` spans with `score`, `gate` and `execute_contact` child phases
- **Contact execution**: `execute_contact` spans for email/payment link generation, with `razorpay.payment_link.*` and `resend.emails.send` provider spans under them
- **ML workflow**: Custom spans for scoring, drift detection, reply classification

### 6. Structured Logging Integration
- **Trace correlation**: `trace_id` and `span_id` automatically added to all log lines
- **Request correlation**: `request_id` from X-Request-Id header bound to context
- **JSON format**: Production logs include trace IDs for Grafana/Jaeger correlation

## Span Hierarchy Example

A complete batch run creates this trace structure:

```
run_batch_cycle (root span)
├── cycle.invoice (invoice 1)
│   ├── score          # ML/rules scoring -> proposed action
│   ├── gate           # policy gate verdict (approved or blocked)
│   └── execute_contact
│       ├── razorpay.payment_link.fetch (dry-run equivalent if reusing)
│       ├── razorpay.payment_link.create
│       └── resend.emails.send
├── cycle.invoice (invoice 2)
│   ├── score
│   │   ├── ml_recovery_model (custom)
│   │   └── select_invoices_query (SQL)
│   └── gate
│       └── execute_contact
│           └── resend.emails.send
└── cycle.invoice (invoice N)
    ├── score
    │   └── drift_model_inference (custom)
    └── gate
```

Provider calls are wrapped at the executor seam, so both live and dry-run
gateways emit the same `razorpay.payment_link.*` / `resend.emails.send`
span names (the dry run's minted link and logged email are the "dry-run
equivalents" of the HTTP calls).

## Configuration

### Environment Variables

#### Required for OTLP Export
```bash
OTEL_EXPORTER_OTLP_ENDPOINT=https://tempo-gateway.grafana.net/otlp
OTEL_EXPORTER_OTLP_HEADERS=Authorization=Basic <base64-token>
OTEL_SERVICE_NAME=recoup
```

#### Optional Configuration
```bash
OTEL_RESOURCE_ATTRIBUTES=deployment.environment=production,service.version=0.1.0
```

### Local Development
- **Console export**: When `OTEL_EXPORTER_OTLP_ENDPOINT` is unset, traces go to stdout as JSON
- **Health check exclusion**: Prevents local health check spam in traces
- **Auto-detection**: No configuration required for basic tracing

### Production Setup
- **Grafana Cloud Tempo**: OTLP HTTP endpoint with authorization header
- **Honeycomb**: OTLP endpoint with API key in headers
- **Jaeger**: OTLP collector endpoint
- **Custom OTLP**: Any OpenTelemetry-compatible collector

## Verification Procedures

### 1. Startup Verification
```bash
# Check logs for successful instrumentation
grep "OTel tracing" app.log

# Expected output:
# "OTel tracing sending to OTLP" (production)
# "OTel tracing writing to console" (development)
```

### 2. Trace Generation Test
```bash
# Trigger a batch run
curl -X POST https://your-app.com/api/v1/tasks/run-batch \
  -H "Authorization: Bearer $TASK_API_KEY"

# Expected: root span "run_batch_cycle" with child spans in trace store
```

### 3. HTTP Request Tracing
```bash
# Make authenticated request with custom header
curl https://your-app.com/api/v1/invoices \
  -H "Authorization: Bearer $API_KEY" \
  -H "X-Request-Id: test-12345"

# Expected: HTTP span with request_id attribute
```

### 4. Database Query Tracing
```bash
# Trigger database-heavy operation
curl https://your-app.com/api/v1/reports/cash-forecast \
  -H "Authorization: Bearer $API_KEY"

# Expected: SQL spans with query details and execution time
```

### 5. External API Tracing
```bash
# Trigger outbound HTTP calls
curl -X POST https://your-app.com/api/v1/invoices/INV-123/execute \
  -H "Authorization: Bearer $API_KEY"

# Expected: httpx client spans for Razorpay/Resend calls
```

## Performance Requirements

### Trace Export Performance
- **Batch export**: Spans buffered and sent in batches to minimize performance impact
- **Async processing**: Export doesn't block request processing
- **Timeout**: 30-second timeout for OTLP export attempts
- **Fallback**: Switches to console export on repeated failures

### Overhead Limits
- **CPU overhead**: <5% additional CPU usage for tracing
- **Memory overhead**: <50MB additional memory for span buffering
- **Network overhead**: <1% of application bandwidth for trace export

### Sampling (Future)
- **Current**: 100% sampling (all traces exported)
- **Production**: Consider head-based sampling at high volume
- **Tail sampling**: Consider for cost optimization

## Monitoring and Alerting

### Trace Ingestion Health
- **Metric**: Trace export success rate >95%
- **Alert**: Failed OTLP exports for >5 minutes
- **Escalation**: Fallback to console logging, investigate collector

### Application Performance
- **Metric**: p95 latency increase <10% with tracing enabled
- **Alert**: Significant performance regression after tracing deployment
- **Mitigation**: Disable tracing temporarily, investigate overhead

### Trace Completeness
- **Metric**: Expected spans present in batch run traces
- **Alert**: Missing spans in run_batch_cycle traces
- **Validation**: Automated checks for span hierarchy completeness

## Troubleshooting

### Common Issues

#### 1. No traces appearing in collector
```bash
# Check OTLP endpoint configuration
echo $OTEL_EXPORTER_OTLP_ENDPOINT
echo $OTEL_EXPORTER_OTLP_HEADERS

# Check app logs for export errors
grep "OTLP exporter" app.log
```

#### 2. Missing spans in traces
```bash
# Verify instrumentation loaded
grep "Instrumentor" app.log

# Check for instrumentation errors
grep "unavailable" app.log
```

#### 3. Performance degradation
```bash
# Check span export queue size
# Monitor memory usage growth
# Consider reducing sampling rate
```

#### 4. Trace correlation issues
```bash
# Verify trace_id in logs
grep "trace_id" app.log | head -5

# Check span parent-child relationships
# Verify request_id propagation
```

### Debug Mode
```bash
# Enable detailed OpenTelemetry logging
OTEL_LOG_LEVEL=debug python app/main.py

# Console export for local debugging
unset OTEL_EXPORTER_OTLP_ENDPOINT
python app/main.py
```

## Security Considerations

### Sensitive Data
- **Excluded**: Payment details, customer PII not included in span attributes
- **Limited**: Only business-relevant IDs and status codes in traces
- **Sanitized**: Error messages truncated to prevent sensitive data leaks

### Authentication
- **OTLP headers**: Secure API keys for trace export
- **TLS**: All trace export uses HTTPS/TLS
- **Network**: Traces never leave secured network boundaries

### Compliance
- **Data retention**: Traces retained per collector policy (typically 30-90 days)
- **PII handling**: No customer names, emails, or payment details in traces
- **Audit**: Trace export configuration changes logged

## Future Enhancements

### Metrics Integration
- **OpenTelemetry Metrics**: Add business metrics alongside traces
- **Custom metrics**: Invoice processing rates, payment success rates
- **Correlation**: Link metrics to traces for full observability

### Enhanced Sampling
- **Adaptive sampling**: Higher sampling for errors, lower for successes
- **Business context**: Sample based on invoice value, customer tier
- **Cost optimization**: Reduce trace volume while maintaining visibility

### Custom Instrumentation
- **ML model inference**: Detailed spans for model scoring and inference
- **Policy engine**: Trace policy evaluation and gate decisions
- **ERP synchronization**: Trace data flows from external ERP systems