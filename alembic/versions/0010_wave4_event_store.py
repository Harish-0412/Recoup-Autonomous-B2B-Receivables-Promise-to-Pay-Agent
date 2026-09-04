"""Wave 4 durable event-store stream.

Revision ID: 0010_wave4_event_store
Revises: 0009_wave2_payment_allocations
Create Date: 2026-09-04

The hot path still dual-writes ``decision_traces`` during the migration window,
but ``recoup_event_records`` becomes the aggregate stream that audit and replay
read. The payload is intentionally private to the backend; no route exposes the
raw table.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_wave4_event_store"
down_revision: str = "0009_wave2_payment_allocations"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "recoup_event_records",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("aggregate_id", sa.String(length=36), nullable=False),
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("trace_seq", sa.Integer(), nullable=True),
        sa.Column("trace_hash", sa.String(length=64), nullable=True),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "approved",
                "blocked",
                "executed",
                "skipped",
                "failed",
                name="decision_outcome",
            ),
            nullable=True,
        ),
        sa.Column("reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("actor", sa.String(length=64), nullable=False, server_default="agent"),
        sa.Column("payload", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("aggregate_state", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "business_id",
            "invoice_id",
            "version",
            name="uq_recoup_events_business_invoice_version",
        ),
        sa.UniqueConstraint("trace_hash", name="uq_recoup_events_trace_hash"),
    )
    op.create_index(
        "ix_recoup_events_aggregate_id",
        "recoup_event_records",
        ["aggregate_id"],
    )
    op.create_index(
        "ix_recoup_events_business_invoice_version",
        "recoup_event_records",
        ["business_id", "invoice_id", "version"],
    )
    op.create_index(
        "ix_recoup_events_business_recorded_at",
        "recoup_event_records",
        ["business_id", "recorded_at"],
    )
    op.create_index("ix_recoup_event_records_invoice_id", "recoup_event_records", ["invoice_id"])


def downgrade() -> None:
    op.drop_index("ix_recoup_event_records_invoice_id", table_name="recoup_event_records")
    op.drop_index("ix_recoup_events_business_recorded_at", table_name="recoup_event_records")
    op.drop_index("ix_recoup_events_business_invoice_version", table_name="recoup_event_records")
    op.drop_index("ix_recoup_events_aggregate_id", table_name="recoup_event_records")
    op.drop_table("recoup_event_records")
