"""Record what became of each contact attempt, not just that one was made

Before the executor existed, a ``contact_logs`` row meant "we decided to
contact them" -- nothing was ever sent. Now a row is written for every
*attempt*, and ``status`` says which ones actually landed. Only ``sent`` and
``simulated`` rows are counted as contact by the frequency and volume caps.

Backfill note: every pre-existing row is stamped ``sent``. That is the
least-wrong option available -- those rows were written as though a message had
gone out, so treating them as delivered preserves the cap behaviour the system
already had. It does not make them true, and it is why this migration exists.

Revision ID: 0002_contact_delivery
Revises: 0001_baseline
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0002_contact_delivery"
down_revision: str | None = "0001_baseline"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Values, not names, matching values_callable in app.models.tables._enum.
DELIVERY_STATUS = sa.Enum("sent", "simulated", "failed", name="delivery_status")


def upgrade() -> None:
    bind = op.get_bind()
    DELIVERY_STATUS.create(bind, checkfirst=True)

    # server_default so the NOT NULL column can be added to a table that
    # already has rows; dropped immediately afterwards so the application, not
    # the database, decides the status of every new row.
    op.add_column(
        "contact_logs",
        sa.Column("status", DELIVERY_STATUS, nullable=False, server_default="sent"),
    )
    op.alter_column("contact_logs", "status", server_default=None)

    op.add_column(
        "contact_logs",
        sa.Column("payment_link_id", sa.String(length=64), nullable=True),
    )
    op.add_column(
        "contact_logs",
        sa.Column("provider_error", sa.Text(), nullable=True),
    )

    op.create_index("ix_contact_logs_status", "contact_logs", ["status"], unique=False)
    op.create_index(
        "ix_contact_logs_payment_link_id", "contact_logs", ["payment_link_id"], unique=False
    )


def downgrade() -> None:
    op.drop_index("ix_contact_logs_payment_link_id", table_name="contact_logs")
    op.drop_index("ix_contact_logs_status", table_name="contact_logs")

    op.drop_column("contact_logs", "provider_error")
    op.drop_column("contact_logs", "payment_link_id")
    op.drop_column("contact_logs", "status")

    # The type outlives the column unless it is dropped explicitly, and a
    # leftover type makes a re-upgrade fail on "type already exists".
    DELIVERY_STATUS.drop(op.get_bind(), checkfirst=True)
