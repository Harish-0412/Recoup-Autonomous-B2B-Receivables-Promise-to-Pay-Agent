# Wave 3 Operations Infrastructure Technical Design

## Overview

This design implements the complete Wave 3 operations infrastructure for Recoup, building on the existing FastAPI application with comprehensive observability, secrets management, and backup verification. The design ensures production-ready operations with fail-safe defaults, minimal performance impact, and clear operational procedures.

## Architecture Components

### 1. Enhanced OpenTelemetry Integration

#### Current State Analysis
- **Existing**: Basic OTel setup in `app/core/observability.py` with FastAPI auto-instrumentation
- **Existing**: Custom span creation for `run_batch_cycle` in `app/services/batch_runner.py`
- **Existing**: OTLP export configuration with console fallback
- **Existing**: Request ID middleware for correlation

#### Enhancements Required

**1.1 Business Logic Span Hierarchy**

Implement structured tracing for the complete batch operation hierarchy:

```python
# Enhanced span creation for business workflows
async def run_batch_cycle():
    with create_span("batch.cycle", 
                    business_id=business_id,
                    batch_size=limit,
                    run_id=summary.run_id) as batch_span:
        
        # Scoring phase
        with create_span("batch.scoring_phase",
                        invoices_count=len(invoices)) as scoring_span:
            for invoice in invoices:
                with create_span("invoice.score",
                               invoice_id=invoice.invoice_id,
                               days_overdue=days_overdue) as score_span:
                    # Existing scoring logic
                    pass

        # Decision phase 
        with create_span("batch.decision_phase") as decision_span:
            # Decision making logic with individual spans
            
        # Execution phase
        with create_span("batch.execution_phase") as exec_span:
            # Contact execution with provider-specific spans
```

**1.2 Enhanced Error Context**

```python
def enhanced_span_error_handling(span, exception: Exception):
    """Enhanced error recording with business context."""
    span.record_exception(exception)
    span.set_status(Status(StatusCode.ERROR, str(exception)))
    
    # Add business context for operational debugging
    if hasattr(exception, 'invoice_id'):
        span.set_attribute("error.invoice_id", exception.invoice_id)
    if hasattr(exception, 'customer_id'):
        span.set_attribute("error.customer_id", exception.customer_id)
        
    # Classify error types for alerting
    error_type = classify_error(exception)
    span.set_attribute("error.classification", error_type)
```

**1.3 Performance Metrics Integration**

```python
# Custom metrics for business KPIs
def record_batch_metrics(summary: RunSummary):
    """Record business metrics alongside traces."""
    metrics = get_meter(__name__)
    
    # Batch performance
    batch_duration = metrics.create_histogram(
        "recoup.batch.duration_seconds",
        description="Batch cycle execution time"
    )
    
    # Business outcomes
    invoices_processed = metrics.create_counter(
        "recoup.invoices.processed_total", 
        description="Invoices processed by outcome"
    )
    
    contacts_sent = metrics.create_counter(
        "recoup.contacts.sent_total",
        description="Contacts sent by channel and step"
    )
```

### 2. Production Secrets Management System

#### Current State Analysis
- **Existing**: Multi-provider secret loading (Render, Doppler, AWS SM)
- **Existing**: Runtime injection via `inject_secrets_into_env()`
- **Existing**: API endpoints for operational management (`/api/v1/secrets/refresh`, `/api/v1/secrets/status`)
- **Existing**: Graceful fallback to environment variables

#### Enhancements Required

**2.1 Secret Caching and Refresh Strategy**

```python
class EnhancedSecretManager:
    """Production-ready secret manager with caching and monitoring."""
    
    def __init__(self):
        self.cache_ttl = timedelta(hours=1)  # Cache validity
        self.refresh_lock = asyncio.Lock()
        self.last_refresh = None
        self.refresh_callbacks: list[Callable] = []
        
    async def start_background_refresh(self):
        """Start background refresh task for cache management."""
        asyncio.create_task(self._refresh_loop())
        
    async def _refresh_loop(self):
        """Background task to refresh secrets periodically."""
        while True:
            try:
                await asyncio.sleep(300)  # Check every 5 minutes
                
                if (self.last_refresh is None or 
                    datetime.now() - self.last_refresh > self.cache_ttl):
                    
                    await self.refresh_secrets()
                    
                    # Notify registered callbacks
                    for callback in self.refresh_callbacks:
                        try:
                            await callback()
                        except Exception as exc:
                            logger.warning("Secret refresh callback failed", error=str(exc))
                            
            except Exception as exc:
                logger.error("Secret refresh loop failed", error=str(exc))
```

**2.2 Integration with Settings System**

```python
# Enhanced config integration
class SecretAwareSettings(BaseSettings):
    """Settings class that can reload from secret manager."""
    
    _secret_manager: SecretManager = None
    
    @classmethod
    async def reload_from_secrets(cls):
        """Reload settings after secret refresh."""
        if cls._secret_manager:
            await cls._secret_manager.refresh_secrets()
            # Clear the lru_cache to force reload
            get_settings.cache_clear()
            return get_settings()
        return None
```

**2.3 Operational API Enhancements**

```python
@router.post("/secrets/rotate")
async def rotate_secret(
    secret_name: str,
    new_value: str,
    _: None = Depends(verify_api_key)
) -> dict:
    """Securely rotate a single secret value.
    
    Updates the secret in the provider and refreshes local cache.
    """
    
@router.get("/secrets/health") 
async def secrets_health() -> dict:
    """Health check for secrets management system."""
    # Check provider connectivity, cache freshness, etc.
```

### 3. Backup Restore Verification System

#### Current State Analysis
- **Existing**: Full restore drill script in `scripts/restore_drill.py`
- **Existing**: Hash chain verification logic
- **Existing**: Runbook update automation
- **Existing**: Support for both PostgreSQL and SQLite

#### Production Integration Required

**3.1 Automated Drill Scheduling**

```python
# New module: app/services/backup_verification.py
class BackupVerificationService:
    """Production backup verification service."""
    
    async def schedule_restore_drill(self) -> str:
        """Schedule restore drill execution."""
        drill_id = f"drill_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        
        # Execute in background task
        asyncio.create_task(self._execute_drill(drill_id))
        return drill_id
        
    async def _execute_drill(self, drill_id: str):
        """Execute restore drill with full observability."""
        with create_span("backup.restore_drill", drill_id=drill_id) as span:
            try:
                result = await execute_restore_drill_async(
                    db_url=get_settings().DATABASE_URL,
                    operator_name="automated-system"
                )
                
                # Record results in observability
                span.set_attribute("drill.success", result.success)
                span.set_attribute("drill.invoice_id", result.invoice_id)
                span.set_attribute("drill.error_count", len(result.errors))
                
                # Store results for API access
                await self._store_drill_result(drill_id, result)
                
            except Exception as exc:
                span.record_exception(exc)
                logger.error("Automated restore drill failed", drill_id=drill_id, error=str(exc))
```

**3.2 API Integration**

```python
@router.post("/backup/restore-drill")
async def trigger_restore_drill(_: None = Depends(verify_api_key)) -> dict:
    """Trigger restore drill execution."""
    
@router.get("/backup/drill-status/{drill_id}")
async def get_drill_status(drill_id: str) -> dict:
    """Get restore drill execution status."""
    
@router.get("/backup/latest-drill")  
async def get_latest_drill_result() -> dict:
    """Get results of most recent restore drill."""
```

### 4. Enhanced Application Lifespan Management

#### Integration Points

```python
# Enhanced lifespan in app/main.py
@asynccontextmanager
async def enhanced_lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Enhanced startup with full operations infrastructure."""
    
    # Phase 1: Secrets loading (existing)
    await inject_secrets_into_env()
    
    # Phase 2: Settings refresh after secrets
    settings = get_settings()
    setup_logging()
    logger.info("Starting application", 
                env=settings.APP_ENV, 
                debug=settings.DEBUG,
                otel_endpoint=bool(settings.OTEL_EXPORTER_OTLP_ENDPOINT))
    
    # Phase 3: Database connectivity check (existing)
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    logger.info("Database reachable", pool_mode=settings.DB_POOL_MODE)
    
    # Phase 4: Background services startup
    secret_manager = await get_secret_manager()
    if secret_manager.active_provider:
        await secret_manager.start_background_refresh()
        logger.info("Secret refresh background task started")
    
    # Phase 5: Verification services
    backup_service = BackupVerificationService()
    app.state.backup_service = backup_service
    
    # Phase 6: Existing classifier setup
    install_cascade_classifier()
    logger.info("Reply classifier cascade installed")

    yield
    
    # Shutdown: cleanup background tasks
    await close_db()
    logger.info("Application shutdown complete")
```

### 5. Enhanced Health and Readiness Checks

#### Comprehensive Operational Health

```python
# Enhanced health checks in app/api/health.py
@router.get("/health/observability")
async def health_observability() -> dict:
    """Check OpenTelemetry infrastructure health."""
    try:
        # Test span creation
        with create_span("health.test_span") as span:
            span.set_attribute("test", True)
            
        return {
            "status": "healthy",
            "tracer": "available",
            "otlp_endpoint": bool(get_settings().OTEL_EXPORTER_OTLP_ENDPOINT),
            "exporters": ["otlp" if get_settings().OTEL_EXPORTER_OTLP_ENDPOINT else "console"]
        }
    except Exception as exc:
        return {
            "status": "degraded", 
            "error": str(exc),
            "tracer": "unavailable"
        }

@router.get("/health/secrets")
async def health_secrets() -> dict:
    """Check secrets management system health."""
    try:
        manager = await get_secret_manager()
        
        # Test secret retrieval
        test_secret = manager.get_secret("DATABASE_URL")
        
        return {
            "status": "healthy",
            "provider": manager.active_provider.name if manager.active_provider else "env_fallback",
            "cache_fresh": manager.last_refresh and (datetime.now() - manager.last_refresh) < timedelta(hours=2),
            "cache_size": len(manager.cache)
        }
    except Exception as exc:
        return {
            "status": "degraded",
            "error": str(exc)
        }

@router.get("/ready/enhanced")
async def enhanced_ready_check(db: AsyncSession = Depends(get_db)) -> JSONResponse:
    """Comprehensive readiness check including operations infrastructure."""
    
    checks = {}
    failing = []
    
    # Existing checks: database, redis
    # ... existing logic ...
    
    # New checks: observability, secrets
    try:
        obs_health = await health_observability()
        checks["observability"] = obs_health["status"]
        if obs_health["status"] != "healthy":
            failing.append("observability")
    except Exception as exc:
        checks["observability"] = "fail"
        checks["observability_error"] = str(exc)[:200] 
        failing.append("observability")
        
    try:
        secrets_health = await health_secrets()
        checks["secrets"] = secrets_health["status"] 
        if secrets_health["status"] != "healthy":
            failing.append("secrets")
    except Exception as exc:
        checks["secrets"] = "fail"
        checks["secrets_error"] = str(exc)[:200]
        failing.append("secrets")
    
    overall = "ready" if not failing else "not_ready"
    code = status.HTTP_200_OK if not failing else status.HTTP_503_SERVICE_UNAVAILABLE
    
    return JSONResponse(
        status_code=code,
        content={
            "status": overall,
            "checks": checks,
            "failing": failing,
            "service": get_settings().APP_NAME,
            "version": "0.1.0"
        }
    )
```

## Implementation Plan

### Phase 1: Enhanced OpenTelemetry (Week 1)
1. **Business Logic Spans**: Enhance existing `run_batch_cycle` tracing hierarchy
2. **Custom Metrics**: Add business KPI metrics alongside traces  
3. **Error Context**: Improve error recording with business context
4. **Performance Validation**: Verify minimal impact on batch processing

### Phase 2: Production Secrets Management (Week 2)  
1. **Background Refresh**: Implement cache management and refresh loops
2. **Settings Integration**: Connect secret refresh to pydantic settings reload
3. **Operational APIs**: Enhance existing endpoints with rotation and health
4. **Production Testing**: Validate rotation procedures across providers

### Phase 3: Backup Verification Integration (Week 3)
1. **Service Layer**: Create `BackupVerificationService` for automated drills
2. **API Integration**: Add backup management endpoints
3. **Scheduling**: Connect to existing batch scheduler infrastructure  
4. **Monitoring**: Integrate drill results with observability system

### Phase 4: Enhanced Health Checks (Week 4)
1. **Component Health**: Add observability and secrets health endpoints
2. **Readiness Enhancement**: Extend `/ready` with operations checks
3. **SLO Integration**: Connect health status to existing SLO monitoring
4. **Documentation**: Update runbook with new operational procedures

## Configuration Changes

### Environment Variables

```bash
# New OpenTelemetry configuration
OTEL_METRIC_EXPORT_INTERVAL=30000  # 30 seconds
OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT=128
OTEL_SPAN_EVENT_COUNT_LIMIT=128

# Secrets management  
SECRET_CACHE_TTL_HOURS=1
SECRET_REFRESH_INTERVAL_SECONDS=300

# Backup verification
BACKUP_DRILL_SCHEDULE_CRON="0 2 * * 0"  # Weekly Sunday 2 AM UTC
BACKUP_DRILL_RETENTION_DAYS=30
```

### Settings Additions

```python
# Additional settings in app/core/config.py
class Settings(BaseSettings):
    # ... existing settings ...
    
    # OpenTelemetry enhancements
    OTEL_SPAN_ATTRIBUTE_COUNT_LIMIT: int = Field(default=128)
    OTEL_SPAN_EVENT_COUNT_LIMIT: int = Field(default=128) 
    OTEL_METRIC_EXPORT_INTERVAL: int = Field(default=30000)
    
    # Secrets management
    SECRET_CACHE_TTL_HOURS: int = Field(default=1, ge=1, le=24)
    SECRET_REFRESH_INTERVAL_SECONDS: int = Field(default=300, ge=60)
    
    # Backup verification  
    BACKUP_DRILL_SCHEDULE_CRON: str = Field(default="0 2 * * 0")
    BACKUP_DRILL_RETENTION_DAYS: int = Field(default=30, ge=1)
    BACKUP_DRILL_AUTO_ENABLED: bool = Field(default=True)
```

## Monitoring and Alerting

### OpenTelemetry Metrics

```promql
# Batch cycle latency (existing SLO)
histogram_quantile(0.95, sum by (le) (
  rate(span_duration_histogram_bucket{span_name="batch.cycle"}[24h])
)) < 30

# Secret refresh success rate
rate(recoup_secrets_refresh_total{status="success"}[5m]) /
rate(recoup_secrets_refresh_total[5m]) > 0.95

# Backup drill success
recoup_backup_drill_success{result="passed"} == 1

# Business outcome metrics
rate(recoup_invoices_processed_total{outcome="contacted"}[1h])
rate(recoup_contacts_sent_total{channel="email"}[1h])
```

### Health Check Integration

```yaml
# Render health check configuration  
healthCheckPath: /api/v1/ready/enhanced
```

## Security Considerations

### Secrets Security
- **No Secret Logging**: Ensure secret values never appear in logs or traces
- **Provider Authentication**: Use IAM roles/service accounts where possible
- **Rotation Validation**: Verify old secrets are invalidated after rotation
- **Audit Trail**: Log secret access patterns for security monitoring

### Observability Security  
- **Span Sanitization**: Remove PII from trace attributes
- **Export Authentication**: Secure OTLP endpoint with proper auth headers
- **Retention Limits**: Configure appropriate trace retention policies

## Performance Impact Analysis

### Tracing Overhead
- **CPU Impact**: <2% additional CPU usage for enhanced span creation
- **Memory Impact**: Minimal - spans are batched and exported asynchronously
- **Network Impact**: OTLP exports batched every 30 seconds, ~10KB/batch typical

### Secrets Management Overhead
- **Startup Impact**: 200-500ms additional startup time for secret loading
- **Runtime Impact**: Background refresh every 5 minutes, <100ms per refresh
- **Memory Impact**: ~1KB per cached secret, typical deployment ~50 secrets

### Backup Drill Impact
- **Scheduled Execution**: Weekly, 2-5 minute execution time
- **Database Impact**: Read-only queries, minimal load on production DB
- **No Business Disruption**: Verification runs against restore point, not live DB

## Operational Procedures

### Secret Rotation Procedure
1. **Update Provider**: Use provider-specific CLI/API to update secret
2. **Trigger Refresh**: `POST /api/v1/secrets/refresh` or restart service
3. **Verify Update**: `GET /api/v1/secrets/status` confirms new value loaded
4. **Update Dependents**: Rotate scheduler/external service credentials  
5. **Validate**: Test functionality with new credentials

### Backup Drill Procedure
1. **Manual Trigger**: `POST /api/v1/backup/restore-drill`
2. **Monitor Progress**: `GET /api/v1/backup/drill-status/{drill_id}`
3. **Review Results**: Check observability dashboard for span details
4. **Runbook Update**: Automatic update on successful completion
5. **Failure Response**: Alert on-call, investigate chain integrity issues

### Observability Troubleshooting
1. **Trace Missing**: Check `/health/observability` endpoint
2. **High Latency**: Review batch cycle span hierarchy for bottlenecks
3. **Export Failures**: Verify OTLP endpoint connectivity and auth
4. **Span Limits**: Increase attribute/event limits if truncation occurs

## Success Metrics

### Operational Excellence
- **MTTR Reduction**: 50% faster incident resolution with enhanced tracing
- **Secret Rotation**: Zero-downtime rotations with background refresh
- **Backup Confidence**: Weekly automated drill success rate >99%
- **Health Visibility**: All infrastructure components monitored

### Performance Maintenance
- **Batch SLO**: Maintain p95 <30s despite enhanced instrumentation
- **Health Latency**: All health checks respond <500ms  
- **Secret Performance**: Refresh operations complete <100ms
- **Drill Efficiency**: Backup verification completes <5 minutes

This design provides comprehensive operations infrastructure while maintaining the existing application's performance and reliability characteristics. The phased implementation allows for validation at each step and ensures production readiness throughout the deployment process.