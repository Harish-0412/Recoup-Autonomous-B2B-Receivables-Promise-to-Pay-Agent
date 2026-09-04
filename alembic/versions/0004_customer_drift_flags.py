"""Nightly drift verdicts, one row per customer per run

A drift flag is a suggestion that a human look at a customer -- it never
changes invoice state, so the table carries no state machine, only the
verdict: the score, the frozen threshold it was cut against, the model
version that produced it, and the feature values behind it for the
reviewer who opens the flag. ``flagged=False`` rows are kept too, so
"was this customer checked" stays answerable.

Revision ID: 0004_customer_drift_flags
Revises: 0003_inbound_replies
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_customer_drift_flags"
down_revision: str | None = "0003_inbound_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customer_drift_flags",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("customer_pk", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("anomaly_score", sa.Float(), nullable=False),
        sa.Column("threshold", sa.Float(), nullable=False),
        sa.Column("flagged", sa.Boolean(), nullable=False, server_default="false"),
        sa.Column("model_version", sa.String(length=64), nullable=False, server_default=""),
        sa.Column("window_days", sa.Integer(), nullable=False, server_default="90"),
        sa.Column("details", sa.JSON(), nullable=False, server_default="{}"),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.func.now(),
        ),
    )

    op.create_index("ix_customer_drift_flags_customer_pk", "customer_drift_flags", ["customer_pk"])
    op.create_index("ix_customer_drift_flags_flagged", "customer_drift_flags", ["flagged"])
    op.create_index("ix_customer_drift_flags_created_at", "customer_drift_flags", ["created_at"])


def downgrade() -> None:
    op.drop_index("ix_customer_drift_flags_created_at", table_name="customer_drift_flags")
    op.drop_index("ix_customer_drift_flags_flagged", table_name="customer_drift_flags")
    op.drop_index("ix_customer_drift_flags_customer_pk", table_name="customer_drift_flags")
    op.drop_table("customer_drift_flags")
