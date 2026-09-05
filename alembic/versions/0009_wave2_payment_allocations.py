"""Wave 2: payment_allocations + integration_credentials tables;
external_ids + erp_source columns on invoices.

Revision ID: 0009_wave2_payment_allocations
Revises: 8ace19fe72f3
Create Date: 2026-09-04

Why this migration exists
--------------------------
Wave 2 — Money Truth introduces three structural changes:

1. ``payment_allocations`` — the canonical ledger for every rupee received.
   ``invoices.amount_paid`` will always be computed as
   ``SUM(payment_allocations.amount)`` for the invoice. The unique constraint
   on (business_id, source, provider_ref) makes double-counting structurally
   impossible: if Razorpay fires both ``payment.captured`` and
   ``payment_link.paid`` for the same transaction, the second INSERT hits the
   constraint and is rejected as a duplicate.

2. ``integration_credentials`` — one row per (tenant, ERP provider) holding
   the OAuth2 refresh token that the hourly sync job uses. Tokens are stored
   verbatim in v1 (Wave 3 will add encryption).

3. ``invoices.external_ids`` (JSON) + ``invoices.erp_source`` (TEXT) — let ERP
   syncs record their own opaque IDs (zoho_invoice_id, qbo_invoice_id, etc.)
   without a column per provider.

What this migration does NOT change
------------------------------------
``invoices.amount_paid`` is left in place (default 0.0). Existing rows are NOT
backfilled from allocations — historical Razorpay payments stay in
``amount_paid`` as-is, and new payments will be written through the allocation
ledger. A future data migration can create seed allocations from existing
``amount_paid`` values if needed.
"""

from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_wave2_payment_allocations"
down_revision: str = "8ace19fe72f3"
branch_labels = None
depends_on = None


def _create_enum_safely(enum_obj: sa.Enum, bind: sa.engine.Connection) -> None:
    """Create a Postgres enum type, tolerant of it already being there.

    ``checkfirst=True`` alone was not enough in practice: a crash-restarted
    deploy (the whole ``alembic upgrade head`` run is one transaction --
    see ``alembic/env.py`` -- so a mid-chain failure should roll everything
    back, but an abruptly killed process does not always get the chance to)
    can leave a type committed with no matching ``alembic_version`` row, so
    the next attempt's checkfirst query loses the race.

    A bare Python ``try/except`` around ``enum_obj.create()`` is *not*
    sufficient either: once Postgres raises inside a transaction, that
    transaction is aborted and every later statement in the same
    ``alembic upgrade head`` run fails with "current transaction is aborted"
    -- catching the Python exception doesn't undo that. A ``DO`` block with
    its own ``EXCEPTION`` clause gets PL/pgSQL's implicit sub-transaction, so
    a caught ``duplicate_object`` rolls back only that statement and leaves
    the outer migration transaction healthy. This is what actually fixes the
    race described above, belt-and-braces alongside ``8ace19fe72f3``.
    """

    if bind.dialect.name != "postgresql":
        # SQLite (the clean-checkout demo path) has no native enum type --
        # SQLAlchemy represents it as a CHECK constraint on the column
        # instead, so there is no separate type to create here.
        return

    values = ", ".join(f"'{v}'" for v in enum_obj.enums)
    bind.execute(
        sa.text(
            f"""
            DO $$ BEGIN
                CREATE TYPE {enum_obj.name} AS ENUM ({values});
            EXCEPTION
                WHEN duplicate_object THEN null;
            END $$;
            """
        )
    )


def upgrade() -> None:
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    existing_tables = set(inspector.get_table_names())

    # ------------------------------------------------------------------
    # 1. New enum types (Postgres only; SQLite stores TEXT)
    # ------------------------------------------------------------------
    _create_enum_safely(
        sa.Enum(
            "razorpay_link",
            "razorpay_payment",
            "bank_utr",
            "erp_credit_note",
            name="allocation_source",
        ),
        bind,
    )
    _create_enum_safely(
        sa.Enum(
            "zoho_books",
            "quickbooks",
            "razorpay_invoices",
            "tally",
            name="integration_provider",
        ),
        bind,
    )

    # ------------------------------------------------------------------
    # 2. payment_allocations table
    # ------------------------------------------------------------------
    if "payment_allocations" not in existing_tables:
        op.create_table(
            "payment_allocations",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
            sa.Column("invoice_pk", sa.Integer(), nullable=True),
            sa.Column(
                "source",
                # NOTE: must be the postgresql-dialect ENUM, not the generic
                # sa.Enum -- the generic type silently drops create_type on
                # the ground (it has no such concept; only the dialect-
                # specific class does), so create_type=False here was
                # inert and CREATE TABLE re-issued CREATE TYPE regardless
                # of _create_enum_safely already having created it above.
                # That, not any crash-restart race, was the actual cause of
                # every "allocation_source already exists" deploy failure.
                postgresql.ENUM(
                    "razorpay_link",
                    "razorpay_payment",
                    "bank_utr",
                    "erp_credit_note",
                    name="allocation_source",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("provider_ref", sa.String(length=128), nullable=False),
            sa.Column("amount", sa.Float(), nullable=False),
            sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
            sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column(
                "recorded_by", sa.String(length=32), nullable=False, server_default="webhook"
            ),
            sa.Column("payer_account", sa.String(length=128), nullable=True),
            sa.Column("notes", sa.Text(), nullable=False, server_default=""),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.ForeignKeyConstraint(
                ["invoice_pk"], ["invoices.id"], name="fk_payment_allocations_invoice"
            ),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "business_id",
                "source",
                "provider_ref",
                name="uq_payment_allocations_business_source_ref",
            ),
        )
        op.create_index(
            "ix_payment_allocations_business_invoice_pk",
            "payment_allocations",
            ["business_id", "invoice_pk"],
        )
        op.create_index(
            "ix_payment_allocations_business_source",
            "payment_allocations",
            ["business_id", "source"],
        )
        op.create_index(
            "ix_payment_allocations_created_at",
            "payment_allocations",
            ["created_at"],
        )

    # ------------------------------------------------------------------
    # 3. integration_credentials table
    # ------------------------------------------------------------------
    if "integration_credentials" not in existing_tables:
        op.create_table(
            "integration_credentials",
            sa.Column("id", sa.Integer(), nullable=False),
            sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
            sa.Column(
                "provider",
                # See the note on the payment_allocations.source column
                # above -- generic sa.Enum ignores create_type entirely.
                postgresql.ENUM(
                    "zoho_books",
                    "quickbooks",
                    "razorpay_invoices",
                    "tally",
                    name="integration_provider",
                    create_type=False,
                ),
                nullable=False,
            ),
            sa.Column("access_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("refresh_token", sa.Text(), nullable=False, server_default=""),
            sa.Column("token_expires_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("scope", sa.Text(), nullable=False, server_default=""),
            sa.Column("extra", sa.JSON(), nullable=False, server_default="{}"),
            sa.Column("last_sync_at", sa.DateTime(timezone=True), nullable=True),
            sa.Column("last_sync_invoices", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("last_sync_errors", sa.Integer(), nullable=False, server_default="0"),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
            sa.PrimaryKeyConstraint("id"),
            sa.UniqueConstraint(
                "business_id",
                "provider",
                name="uq_integration_credentials_business_provider",
            ),
        )
        op.create_index(
            "ix_integration_credentials_business_id",
            "integration_credentials",
            ["business_id"],
        )

    # ------------------------------------------------------------------
    # 4. New columns on invoices
    # ------------------------------------------------------------------
    invoice_columns = {c["name"] for c in inspector.get_columns("invoices")}
    if "external_ids" not in invoice_columns:
        op.add_column(
            "invoices",
            sa.Column("external_ids", sa.JSON(), nullable=False, server_default="{}"),
        )
    if "erp_source" not in invoice_columns:
        op.add_column(
            "invoices",
            sa.Column("erp_source", sa.Text(), nullable=False, server_default="batch_ingest"),
        )


def downgrade() -> None:
    # Remove columns from invoices
    op.drop_column("invoices", "erp_source")
    op.drop_column("invoices", "external_ids")

    # Drop new tables
    op.drop_index("ix_integration_credentials_business_id", table_name="integration_credentials")
    op.drop_table("integration_credentials")

    op.drop_index("ix_payment_allocations_created_at", table_name="payment_allocations")
    op.drop_index("ix_payment_allocations_business_source", table_name="payment_allocations")
    op.drop_index("ix_payment_allocations_business_invoice_pk", table_name="payment_allocations")
    op.drop_table("payment_allocations")

    # Drop enum types (Postgres only; SQLite doesn't need this)
    bind = op.get_bind()
    if bind.dialect.name == "postgresql":
        sa.Enum(name="integration_provider").drop(bind, checkfirst=True)
        sa.Enum(name="allocation_source").drop(bind, checkfirst=True)
