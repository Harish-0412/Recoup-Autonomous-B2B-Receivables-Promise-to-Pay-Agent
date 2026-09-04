"""OpenTelemetry setup plus request-id middleware.

Two knobs, both opt-in by env, so a plain checkout traces to console JSON
without hitting the network:

* ``OTEL_EXPORTER_OTLP_ENDPOINT`` — when set, spans ship to Grafana Cloud
  Tempo / any OTLP-gateway HTTP receiver. When unset, we still emit the
  same trace/span ids on logs and to console JSON for local debugging.
* ``RATE_LIMIT_REDIS_URL`` is unrelated to tracing; listed here to keep the
  list of env-side switches discoverable.

Request ids: the client may send ``X-Request-Id``; if not we mint a 32-hex
uuid4. It is echoed back on the response header and bound to structlog via
contextvars so every log line for the same request ties to the same trace.

Health URLs are excluded from the FastAPI auto-instrumentor so Render's
30-second ping does not fill the trace store.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from contextlib import contextmanager, suppress
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from fastapi import FastAPI, Request, Response
    from starlette.middleware.base import BaseHTTPMiddleware

from app.core.config import get_settings
from app.core.logging import get_logger

logger = get_logger(__name__)

#: Paths excluded from FastAPI auto-instrumentation. ``/health`` is kept cheap
#: per the Wave-3 spec; ``/ready`` is also a liveness probe and similarly cheap.
_HEALTH_EXCLUDES = "/health,/api/v1/health,/api/v1/ready,/health/db"

#: Global tracer for custom span creation
_tracer = None

_setup_done = False


def set_span_attribute_dict(span: Any | None, d: dict[str, Any]) -> None:
    """Copy every primitive value in ``d`` onto ``span``.

    Safe: non-primitives are converted to ``repr()`` rather than raising from
    the OTel SDK, and ``span=None`` (tracing disabled) is a no-op. Callers get
    uniform semantics regardless of value type.
    """

    if span is None:
        return
    for key, value in d.items():
        if isinstance(value, str | int | float | bool):
            with suppress(Exception):
                span.set_attribute(key, value)
        elif value is None:
            continue  # OTel ignores None attributes; skip rather than warn
        else:
            with suppress(Exception):
                span.set_attribute(key, repr(value)[:200])


def setup_otel(app: FastAPI) -> None:
    """Idempotently wire OpenTelemetry into ``app``.

    Safe to call more than once (e.g. from tests): the second call is a no-op.
    """

    global _setup_done, _tracer
    if _setup_done:
        return
    _setup_done = True

    settings = get_settings()

    # 1. Resource + TracerProvider -----------------------------------------
    try:
        from opentelemetry import trace
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import (
            BatchSpanProcessor,
            ConsoleSpanExporter,
        )
    except ImportError:
        logger.warning("opentelemetry-sdk not available; skipping tracing setup")
        return

    resource = Resource.create(
        attributes={
            "service.name": settings.OTEL_SERVICE_NAME,
            "service.version": "0.1.0",
            "deployment.environment": settings.APP_ENV,
        }
    )
    provider = TracerProvider(resource=resource)

    # 2. Exporter: OTLP HTTP when configured, else Console JSON -------------
    otlp_endpoint = settings.OTEL_EXPORTER_OTLP_ENDPOINT
    if otlp_endpoint:
        try:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

            headers_env = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "")
            exporter_kwargs: dict[str, Any] = {"endpoint": otlp_endpoint}
            if headers_env:
                parsed: dict[str, str] = {}
                for pair in headers_env.split(","):
                    if "=" in pair:
                        k, v = pair.split("=", 1)
                        parsed[k.strip()] = v.strip()
                if parsed:
                    exporter_kwargs["headers"] = parsed
            exporter = OTLPSpanExporter(**exporter_kwargs)
            logger.info("OTel tracing sending to OTLP", endpoint=otlp_endpoint)
        except Exception as exc:  # noqa: BLE001
            logger.warning(
                "OTLP exporter failed to initialise, falling back to console", error=str(exc)
            )
            exporter = ConsoleSpanExporter()
    else:
        exporter = ConsoleSpanExporter()
        logger.info("OTel tracing writing to console (OTEL_EXPORTER_OTLP_ENDPOINT unset)")

    provider.add_span_processor(BatchSpanProcessor(exporter))
    trace.set_tracer_provider(provider)

    # Create global tracer for custom instrumentation
    _tracer = trace.get_tracer(__name__)

    # 3. FastAPI auto-instrumentation (excluding health URLs) ---------------
    try:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(
            app,
            excluded_urls=_HEALTH_EXCLUDES,
            tracer_provider=provider,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("FastAPIInstrumentor unavailable", error=str(exc))

    # 4. SQLAlchemy instrumentation (sync + async engine) -------------------
    try:
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        from app.db.session import engine

        SQLAlchemyInstrumentor().instrument(
            engine=engine.sync_engine if hasattr(engine, "sync_engine") else engine,
            tracer_provider=provider,
            enable_commenter=True,
            commenter_options={"opentelemetry_values": True},
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning("SQLAlchemyInstrumentor unavailable", error=str(exc))

    # 5. httpx client instrumentation (for Razorpay/Resend outbound calls) --
    try:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    except Exception as exc:  # noqa: BLE001
        logger.warning("HTTPXClientInstrumentor unavailable", error=str(exc))

    # 6. Stdlib logging + structlog get trace_id/span_id --------------------
    try:
        from opentelemetry.instrumentation.logging import LoggingInstrumentor

        LoggingInstrumentor().instrument(tracer_provider=provider, set_logging_format=False)
    except Exception as exc:  # noqa: BLE001
        logger.warning("LoggingInstrumentor unavailable", error=str(exc))

    # 7. Redis instrumentation for rate limiting ---------------------------
    try:
        from opentelemetry.instrumentation.redis import RedisInstrumentor

        RedisInstrumentor().instrument(tracer_provider=provider)
    except Exception as exc:  # noqa: BLE001
        logger.warning("RedisInstrumentor unavailable", error=str(exc))


def _module_tracer() -> Any | None:
    """The tracer backing :func:`create_span`, resolved lazily.

    ``setup_otel`` stores its tracer on ``_tracer``; before it runs (tests,
    import-time spans) we fall back to the provider-installed tracer so span
    creation still works against whatever global ``TracerProvider`` is active.
    """

    global _tracer
    if _tracer is None:
        try:
            from opentelemetry import trace

            _tracer = trace.get_tracer("recoup")
        except Exception:  # noqa: BLE001 - OTel not installed -> no spans
            return None
    return _tracer


def get_tracer() -> Any | None:
    """Get the tracer used for custom span creation."""
    return _module_tracer()


@contextmanager
def create_span(name: str, **attributes: Any) -> Iterator[Any | None]:
    """Context manager that runs ``name`` as the *current* span.

    Using ``start_as_current_span`` (rather than ``start_span``) is what makes
    nested ``with create_span(...)`` blocks form a real parent/child tree in
    the trace store: each block sets itself as the active span for its body,
    so a ``score`` span opened inside a ``cycle.invoice`` span is recorded as
    its child. Exceptions propagate after being recorded on the span.

    Returns ``None`` (no-op context) when OpenTelemetry is not available, so
    callers need no tracing-specific branches.
    """

    tracer = _module_tracer()
    if tracer is None:
        yield None
        return

    with tracer.start_as_current_span(name) as span:
        set_span_attribute_dict(span, attributes)
        try:
            yield span
        except Exception:
            try:
                from opentelemetry import trace as _otel_trace

                span.record_exception()
                span.set_status(_otel_trace.Status(_otel_trace.StatusCode.ERROR))
            except Exception:  # noqa: BLE001 - never let tracing hide the error
                pass
            raise


def _set_span_result(span: Any | None, *, ok: bool, error: str | None = None, **extra: Any) -> None:
    """Record a gateway outcome on the span created around a provider call."""

    attrs: dict[str, Any] = {"ok": ok}
    if error:
        attrs["error"] = error[:200]
    attrs.update(extra)
    set_span_attribute_dict(span, attrs)


# ---------------------------------------------------------------------------
# X-Request-Id middleware (one per app, added by main.py after setup_otel)
# ---------------------------------------------------------------------------


def request_id_middleware_factory(app: FastAPI) -> type[BaseHTTPMiddleware]:
    """Return a middleware class that, for every request:

    * reads/generates ``X-Request-Id`` (32-hex uuid4)
    * echoes it back on the response header
    * binds it to structlog contextvars so log lines carry ``request_id``
    """

    import structlog
    from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint

    class _RequestIdMiddleware(BaseHTTPMiddleware):
        async def dispatch(self, request: Request, call_next: RequestResponseEndpoint) -> Response:
            existing = request.headers.get("X-Request-Id", "")
            rid = existing if existing and 4 <= len(existing) <= 128 else uuid.uuid4().hex
            # bind_contextvars returns {key: contextvars.Token} so the context
            # can be rolled back exactly, even when requests interleave.
            tokens = structlog.contextvars.bind_contextvars(request_id=rid)
            try:
                response = await call_next(request)
            finally:
                structlog.contextvars.reset_contextvars(**tokens)
            response.headers["X-Request-Id"] = rid
            return response

    return _RequestIdMiddleware
