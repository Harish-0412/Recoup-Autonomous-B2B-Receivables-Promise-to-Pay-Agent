import logging
import sys
from typing import Any, cast

import structlog

from app.core.config import get_settings


def _add_opentelemetry_processor(_, __, event_dict: dict) -> dict:
    """Add OpenTelemetry trace_id and span_id to log records."""
    try:
        from opentelemetry import trace

        current_span = trace.get_current_span()
        if current_span and current_span.is_recording():
            ctx = current_span.get_span_context()
            if ctx and ctx.is_valid:
                event_dict["trace_id"] = format(ctx.trace_id, "032x")
                event_dict["span_id"] = format(ctx.span_id, "016x")
    except ImportError:
        # OpenTelemetry not available, skip
        pass
    except Exception:  # noqa: BLE001
        # Any error in trace extraction shouldn't break logging
        pass

    return event_dict


def setup_logging() -> None:
    settings = get_settings()

    # Configure stdlib logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    )

    # Configure structlog
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            _add_opentelemetry_processor,  # Add trace/span IDs
            structlog.processors.StackInfoRenderer(),
            structlog.dev.set_exc_info,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            structlog.processors.JSONRenderer()
            if settings.is_production
            else structlog.dev.ConsoleRenderer(colors=True),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)
        ),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    return cast(structlog.BoundLogger, structlog.get_logger(name))


def bind_logger(logger: structlog.BoundLogger, **context: Any) -> structlog.BoundLogger:
    """``logger.bind()``, typed as what it actually returns.

    structlog types ``bind`` as returning ``BoundLoggerBase``, which has no
    ``info``/``warning`` -- so every call site would otherwise have to cast, or
    the whole module would have to give up on type checking. One cast here,
    matching ``get_logger`` above.
    """

    return cast(structlog.BoundLogger, logger.bind(**context))
