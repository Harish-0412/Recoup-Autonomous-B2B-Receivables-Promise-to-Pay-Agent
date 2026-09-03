"""Store every inbound customer reply, and the ones a human still has to read

The review queue is a table rather than a log line because "routed to a human"
has to have somewhere to route *to*. Replies the classifier was unsure about
are stored with ``disposition='needs_review'`` and no promise attached, and
``GET /api/v1/replies/review`` is that queue.

Unmatched replies are stored too, with a null ``invoice_pk``. A reply that
cannot be tied to an invoice is exactly the one worth a person's attention, so
discarding it would be the one outcome worse than queueing it.

Revision ID: 0003_inbound_replies
Revises: 0002_contact_delivery
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "0003_inbound_replies"
down_revision: str | None = "0002_contact_delivery"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

#: Values, not names, matching values_callable in app.models.tables._enum.
#:
#: ``create_type=False`` is load-bearing. SQLAlchemy's Postgres dialect emits a
#: CREATE TYPE for any enum column it sees in a CREATE TABLE, so with the
#: default the type is created twice -- once explicitly below, once implicitly
#: by ``create_table`` -- and the second attempt fails with "type already
#: exists", rolling the whole migration back. Creating it explicitly and
#: telling the table not to is what makes upgrade/downgrade/upgrade work.
REPLY_DISPOSITION = postgresql.ENUM(
    "auto_handled",
    "needs_review",
    "reviewed",
    name="reply_disposition",
    create_type=False,
)


def upgrade() -> None:
    REPLY_DISPOSITION.create(op.get_bind(), checkfirst=True)

    op.create_table(
        "inbound_replies",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("reply_id", sa.String(length=128), nullable=False),
        # Nullable on purpose: an unroutable reply is kept, not dropped.
        sa.Column("invoice_pk", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=True),
        sa.Column("customer_pk", sa.Integer(), sa.ForeignKey("customers.id"), nullable=True),
        sa.Column("from_email", sa.String(length=320), nullable=False),
        sa.Column("to_email", sa.String(length=320), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("intent", sa.String(length=64), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.Column("classifier_version", sa.String(length=64), nullable=False),
        sa.Column("fallback_used", sa.Boolean(), nullable=False),
        sa.Column("disposition", REPLY_DISPOSITION, nullable=False),
        sa.Column("disposition_reason", sa.Text(), nullable=False),
        sa.Column("promise_pk", sa.Integer(), sa.ForeignKey("promises.id"), nullable=True),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reviewed_at", sa.DateTime(timezone=True), nullable=True),
    )

    # Unique, because this is the idempotency key: Resend retries on non-2xx,
    # and a retried delivery must not record the same promise twice.
    op.create_index("ix_inbound_replies_reply_id", "inbound_replies", ["reply_id"], unique=True)
    op.create_index("ix_inbound_replies_invoice_pk", "inbound_replies", ["invoice_pk"])
    op.create_index("ix_inbound_replies_customer_pk", "inbound_replies", ["customer_pk"])
    op.create_index("ix_inbound_replies_disposition", "inbound_replies", ["disposition"])
    op.create_index("ix_inbound_replies_received_at", "inbound_replies", ["received_at"])


def downgrade() -> None:
    op.drop_index("ix_inbound_replies_received_at", table_name="inbound_replies")
    op.drop_index("ix_inbound_replies_disposition", table_name="inbound_replies")
    op.drop_index("ix_inbound_replies_customer_pk", table_name="inbound_replies")
    op.drop_index("ix_inbound_replies_invoice_pk", table_name="inbound_replies")
    op.drop_index("ix_inbound_replies_reply_id", table_name="inbound_replies")
    op.drop_table("inbound_replies")

    # The type outlives the table unless dropped explicitly, and a leftover
    # type makes a re-upgrade fail on "type already exists".
    REPLY_DISPOSITION.drop(op.get_bind(), checkfirst=True)
