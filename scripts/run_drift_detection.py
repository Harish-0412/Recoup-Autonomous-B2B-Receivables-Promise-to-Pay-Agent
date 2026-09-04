"""Nightly drift detection over the open customer book.

    python scripts/run_drift_detection.py [--limit 500] [--window-days 90]

Scores every customer holding an actionable invoice through the trained
Isolation Forest (same nine features as training, built from three
trailing 30-day windows) and persists one verdict row per customer --
flagged or not, so "was this customer checked" stays answerable.

A flag changes no invoice state, freezes nothing, and sends nothing to
the customer. Operator notification follows the DRY_RUN convention:
always rendered and logged; emailed only when DRIFT_ALERT_EMAIL names a
mailbox *and* DRY_RUN is off. Schedule with cron (nightly), exactly like
the batch trigger -- the app itself holds no timers.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from datetime import date
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from app.core.config import get_settings  # noqa: E402
from app.core.logging import get_logger, setup_logging  # noqa: E402
from app.db.session import async_session_maker  # noqa: E402
from app.models import CustomerDriftFlag  # noqa: E402
from app.services import repository  # noqa: E402
from app.services.drift import InvoiceFacts, customer_series  # noqa: E402
from app.services.resend_client import get_resend_client  # noqa: E402
from src.ml.drift.features import FEATURE_COLUMNS, from_period_series, to_row  # noqa: E402
from src.ml.drift.service import DriftScorer  # noqa: E402
from src.ml.versioning import utc_now  # noqa: E402

logger = get_logger(__name__)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--window-days", type=int, default=None)
    parser.add_argument("--as-of", type=date.fromisoformat, default=None)
    return parser


def render_alert(
    flagged: list[tuple[str, str, float, list[tuple[str, float]]]],
    *,
    model_version: str,
    window_days: int,
) -> tuple[str, str]:
    """Subject + text body for the operator alert. Pure, tested directly."""

    subject = f"[Recoup] {len(flagged)} customer(s) flagged for payment drift"
    lines = [
        f"Drift detection ran over a trailing {window_days}-day window",
        f"(model {model_version}). Flags are review suggestions only --",
        "nothing was frozen, escalated, or sent.",
        "",
    ]
    for customer_id, name, score, drivers in flagged:
        lines.append(f"- {customer_id} ({name}): score {score:+.3f}")
        for feature, deviation in drivers[:3]:
            lines.append(f"    {feature}: deviation {deviation:+.1f}")
    if not flagged:
        lines.append("No customers drifted this run.")
    lines.append("")
    lines.append("Review queue: GET /api/v1/drift/flags?only_flagged=true")
    return subject, "\n".join(lines)


async def run(*, limit: int, window_days: int, as_of: date) -> dict[str, int]:
    settings = get_settings()
    scorer = DriftScorer()
    summary = {
        "scored": 0,
        "flagged": 0,
        "fallback": 0,
        "alert_emailed": 0,
    }
    if not scorer.available:
        logger.warning("No drift artifact available; recording nothing")
        summary["fallback"] = 1
        return summary

    flagged: list[tuple[str, str, float, list[tuple[str, float]]]] = []
    async with async_session_maker() as session:
        customers = await repository.customers_with_open_invoices(session, limit=limit)
        for customer in customers:
            invoices = await repository.invoices_for_customer(session, customer.id)
            facts = [
                InvoiceFacts(
                    amount=invoice.amount,
                    amount_paid=invoice.amount_paid,
                    issue_date=invoice.issue_date,
                    due_date=invoice.due_date,
                    paid_at=invoice.paid_at,
                )
                for invoice in invoices
            ]
            lifetime_scale = max(customer.avg_invoice_amount, 0.0) * max(customer.invoice_count, 1)
            series = customer_series(
                facts,
                lifetime_scale=lifetime_scale,
                as_of=as_of,
                window_days=window_days,
            )
            feature_values = to_row(from_period_series(series))
            result = scorer.score_series(customer.customer_id, series)
            assert result.anomaly_score is not None and result.threshold is not None
            await repository.save_drift_flag(
                session,
                CustomerDriftFlag(
                    customer_pk=customer.id,
                    anomaly_score=result.anomaly_score,
                    threshold=result.threshold,
                    flagged=result.flagged,
                    model_version=result.model_version,
                    window_days=window_days,
                    details={
                        "features": {
                            column: float(value)
                            for column, value in zip(FEATURE_COLUMNS, feature_values, strict=True)
                        },
                        "top_drivers": [
                            {
                                "feature": driver.feature,
                                "value": driver.value,
                                "deviation": driver.deviation,
                            }
                            for driver in result.top_drivers
                        ],
                    },
                ),
            )
            summary["scored"] += 1
            if result.flagged:
                summary["flagged"] += 1
                flagged.append(
                    (
                        customer.customer_id,
                        customer.name,
                        result.anomaly_score,
                        [(d.feature, d.deviation) for d in result.top_drivers],
                    )
                )
        await session.commit()

    subject, body = render_alert(
        flagged, model_version=scorer.model_version, window_days=window_days
    )
    logger.info(
        "Drift detection complete",
        scored=summary["scored"],
        flagged=summary["flagged"],
        model=scorer.model_version,
    )
    print(f"{subject}\n{body}")
    if settings.DRIFT_ALERT_EMAIL and not settings.DRY_RUN:
        get_resend_client().send_email(
            to=settings.DRIFT_ALERT_EMAIL,
            subject=subject,
            html=f"<pre>{body}</pre>",
            text=body,
        )
        summary["alert_emailed"] = 1
        logger.info("Drift alert emailed", to=settings.DRIFT_ALERT_EMAIL)
    else:
        logger.info(
            "Drift alert logged, not emailed",
            reason=(
                "DRIFT_ALERT_EMAIL unset" if not settings.DRIFT_ALERT_EMAIL else "DRY_RUN is on"
            ),
        )
    return summary


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    setup_logging()
    settings = get_settings()
    summary = asyncio.run(
        run(
            limit=args.limit,
            window_days=args.window_days or settings.DRIFT_WINDOW_DAYS,
            as_of=args.as_of or utc_now().date(),
        )
    )
    print(f"scored={summary['scored']} flagged={summary['flagged']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
