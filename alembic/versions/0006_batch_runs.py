"""Create batch_runs -- the table every later migration assumed already existed

Revision ID: 0006_batch_runs
Revises: 0005_merge_drift_contact_timing
Create Date: 2026-09-05

``BatchRunRecord`` (``app/models/tables.py``) has existed since the autonomous
batch runner shipped, but no migration ever created its table. Every dev and
CI environment had it anyway -- created out of band, before "Alembic owns the
schema" was enforced -- so nothing caught the gap until a genuinely fresh
Postgres ran the chain and ``8ace19fe72f3`` (wave1 tenancy) tried to
``ALTER TABLE batch_runs ADD COLUMN business_id`` against a table that was
never there: ``psycopg.errors.UndefinedTable``.

Declares ``business_id`` directly rather than adding it after the fact, since
this is a fresh table on any database that reaches this revision. Guarded by
an existence check so it is a no-op on the databases that already have the
table from before this migration existed.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_batch_runs"
down_revision: str | None = "0005_merge_drift_contact_timing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "batch_runs" in inspector.get_table_names():
        return

    op.create_table(
        "batch_runs",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("run_id", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.String(length=64), nullable=False),
        sa.Column("finished_at", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("ran", sa.Boolean(), nullable=False, server_default="true"),
        sa.Column("skipped_reason", sa.Text(), nullable=False, server_default=""),
        sa.Column("promises_checked", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("promises_broken", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("promises_kept", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("invoices_considered", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("scored", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("acted", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("blocked_by_policy", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("left_alone", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("delivery_failed", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("handed_off", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("sending_halted", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("errors", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column("invoice_decisions", sa.JSON(), nullable=False, server_default="[]"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )
    op.create_index("ix_batch_runs_run_id", "batch_runs", ["run_id"], unique=True)
    op.create_index(
        "ix_batch_runs_business_created_at", "batch_runs", ["business_id", "created_at"]
    )


def downgrade() -> None:
    inspector = sa.inspect(op.get_bind())
    if "batch_runs" not in inspector.get_table_names():
        return
    op.drop_index("ix_batch_runs_business_created_at", table_name="batch_runs")
    op.drop_index("ix_batch_runs_run_id", table_name="batch_runs")
    op.drop_table("batch_runs")
