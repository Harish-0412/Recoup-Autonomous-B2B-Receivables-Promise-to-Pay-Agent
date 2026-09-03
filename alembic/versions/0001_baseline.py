"""Baseline schema: customers, invoices, promises, contacts, opt-outs, traces

Generated from the SQLAlchemy metadata rather than hand-written, so the
baseline cannot disagree with the models it is supposed to create.

Note the enum columns. They are created with the *values* of each Python enum
("human_handoff", not "HUMAN_HANDOFF"), matching values_callable in
app.models.tables._enum. The escalation state machine and the API both speak
those values, so the database speaks them too.

Revision ID: 0001_baseline
Revises:
Create Date: 2026-09-03
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0001_baseline"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "customers",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("customer_id", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("industry", sa.String(length=128), nullable=False),
        sa.Column("email", sa.String(length=320), nullable=True),
        sa.Column("phone", sa.String(length=32), nullable=True),
        sa.Column(
            "preferred_channel",
            sa.Enum("email", "whatsapp", name="contact_channel"),
            nullable=False,
        ),
        sa.Column("tenure_months", sa.Integer(), nullable=False),
        sa.Column("invoice_count", sa.Integer(), nullable=False),
        sa.Column("avg_invoice_amount", sa.Float(), nullable=False),
        sa.Column("on_time_ratio_90d", sa.Float(), nullable=False),
        sa.Column("on_time_ratio_all_time", sa.Float(), nullable=False),
        sa.Column("avg_days_late", sa.Float(), nullable=False),
        sa.Column("prior_broken_promises_count", sa.Integer(), nullable=False),
        sa.Column("prior_disputes_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_customers_customer_id", "customers", ["customer_id"], unique=True)

    op.create_table(
        "invoices",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("customer_pk", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("issue_date", sa.Date(), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.Column("payment_terms_days", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "OPEN",
                "IN_PROGRESS",
                "PROMISED",
                "PAID",
                "DISPUTED",
                "WRITTEN_OFF",
                "HANDED_OFF",
                name="invoice_status",
            ),
            nullable=False,
        ),
        sa.Column(
            "escalation_state",
            sa.Enum(
                "monitoring",
                "reminded",
                "escalated",
                "human_handoff",
                "closed",
                name="escalation_state",
            ),
            nullable=False,
        ),
        sa.Column("ladder_index", sa.Integer(), nullable=False),
        sa.Column("prior_reminders_sent", sa.Integer(), nullable=False),
        sa.Column("last_contact_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("payment_link_id", sa.String(length=64), nullable=True),
        sa.Column("payment_link_url", sa.Text(), nullable=True),
        sa.Column("amount_paid", sa.Float(), nullable=False),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_invoices_customer_pk", "invoices", ["customer_pk"], unique=False)
    op.create_index("ix_invoices_invoice_id", "invoices", ["invoice_id"], unique=True)
    op.create_index("ix_invoices_payment_link_id", "invoices", ["payment_link_id"], unique=False)
    op.create_index("ix_invoices_status", "invoices", ["status"], unique=False)

    op.create_table(
        "promises",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("promise_id", sa.String(length=64), nullable=False),
        sa.Column("invoice_pk", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("promised_amount", sa.Float(), nullable=False),
        sa.Column("promised_date", sa.Date(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("source_reply_id", sa.String(length=64), nullable=True),
        sa.Column("source_confidence", sa.Float(), nullable=False),
        sa.Column(
            "status",
            sa.Enum("PENDING", "KEPT", "BROKEN", "SUPERSEDED", name="promise_status"),
            nullable=False,
        ),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
    )
    op.create_index("ix_promises_invoice_pk", "promises", ["invoice_pk"], unique=False)
    op.create_index("ix_promises_promise_id", "promises", ["promise_id"], unique=True)
    op.create_index("ix_promises_promised_date", "promises", ["promised_date"], unique=False)
    op.create_index("ix_promises_status", "promises", ["status"], unique=False)

    op.create_table(
        "contact_logs",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("invoice_pk", sa.Integer(), sa.ForeignKey("invoices.id"), nullable=False),
        sa.Column("channel", sa.Enum("email", "whatsapp", name="contact_channel"), nullable=False),
        sa.Column("ladder_step", sa.String(length=64), nullable=False),
        sa.Column("subject", sa.String(length=255), nullable=False),
        sa.Column("body_preview", sa.Text(), nullable=False),
        sa.Column("provider_message_id", sa.String(length=128), nullable=True),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index("ix_contact_logs_invoice_pk", "contact_logs", ["invoice_pk"], unique=False)
    op.create_index("ix_contact_logs_sent_at", "contact_logs", ["sent_at"], unique=False)

    op.create_table(
        "opt_outs",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("customer_pk", sa.Integer(), sa.ForeignKey("customers.id"), nullable=False),
        sa.Column("channel", sa.Enum("email", "whatsapp", name="contact_channel"), nullable=True),
        sa.Column("reason", sa.String(length=255), nullable=False),
        sa.Column("source_reply_id", sa.String(length=64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("customer_pk", "channel", name="uq_optout_customer_channel"),
    )
    op.create_index("ix_opt_outs_customer_pk", "opt_outs", ["customer_pk"], unique=False)

    op.create_table(
        "decision_traces",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("invoice_id", sa.String(length=64), nullable=False),
        sa.Column("event", sa.String(length=128), nullable=False),
        sa.Column(
            "outcome",
            sa.Enum(
                "approved", "blocked", "skipped", "executed", "failed", name="decision_outcome"
            ),
            nullable=False,
        ),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("actor", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("prev_hash", sa.String(length=64), nullable=False),
        sa.Column("entry_hash", sa.String(length=64), nullable=False),
        sa.Column("recorded_at", sa.DateTime(timezone=True), nullable=False),
    )
    op.create_index(
        "ix_decision_traces_entry_hash", "decision_traces", ["entry_hash"], unique=False
    )
    op.create_index(
        "ix_decision_traces_invoice_id", "decision_traces", ["invoice_id"], unique=False
    )
    op.create_index(
        "ix_decision_traces_invoice_seq", "decision_traces", ["invoice_id", "seq"], unique=False
    )
    op.create_index("ix_decision_traces_seq", "decision_traces", ["seq"], unique=True)

    op.create_table(
        "webhook_events",
        sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
        sa.Column("event_id", sa.String(length=128), nullable=False),
        sa.Column("event_type", sa.String(length=64), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("signature_verified", sa.Boolean(), nullable=False),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("processing_error", sa.Text(), nullable=True),
    )
    op.create_index("ix_webhook_events_event_id", "webhook_events", ["event_id"], unique=True)
    op.create_index("ix_webhook_events_event_type", "webhook_events", ["event_type"], unique=False)


def downgrade() -> None:
    # Reverse dependency order. The enum types Postgres created alongside the
    # tables are dropped explicitly: dropping a table does not drop its type,
    # and leaving them behind makes a later re-upgrade fail with
    # "type already exists".
    op.drop_table("webhook_events")
    op.drop_table("decision_traces")
    op.drop_table("opt_outs")
    op.drop_table("contact_logs")
    op.drop_table("promises")
    op.drop_table("invoices")
    op.drop_table("customers")

    # Only Postgres has standalone enum types to drop. SQLite and MySQL render
    # these columns inline, so the DROP TYPE statements are both unnecessary
    # and a syntax error there.
    if op.get_bind().dialect.name == "postgresql":
        for enum_name in (
            "contact_channel",
            "invoice_status",
            "escalation_state",
            "promise_status",
            "decision_outcome",
        ):
            op.execute(sa.text(f"DROP TYPE IF EXISTS {enum_name}"))
