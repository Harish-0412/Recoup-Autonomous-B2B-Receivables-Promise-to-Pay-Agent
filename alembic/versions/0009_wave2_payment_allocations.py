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

from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_wave2_payment_allocations"
down_revision: str = "8ace19fe72f3"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. New enum types (Postgres only; SQLite stores TEXT)
    # ------------------------------------------------------------------
    # Create the allocation_source enum
    allocation_source_enum = sa.Enum(
        "razorpay_link",
        "razorpay_payment",
        "bank_utr",
        "erp_credit_note",
        name="allocation_source",
    )
    allocation_source_enum.create(op.get_bind(), checkfirst=True)

    # Create the integration_provider enum
    integration_provider_enum = sa.Enum(
        "zoho_books",
        "quickbooks",
        "razorpay_invoices",
        "tally",
        name="integration_provider",
    )
    integration_provider_enum.create(op.get_bind(), checkfirst=True)

    # ------------------------------------------------------------------
    # 2. payment_allocations table
    # ------------------------------------------------------------------
    op.create_table(
        "payment_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column("invoice_pk", sa.Integer(), nullable=True),
        sa.Column(
            "source",
            sa.Enum(
                "razorpay_link",
                "razorpay_payment",
                "bank_utr",
                "erp_credit_note",
                name="allocation_source",
            ),
            nullable=False,
        ),
        sa.Column("provider_ref", sa.String(length=128), nullable=False),
        sa.Column("amount", sa.Float(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False, server_default="INR"),
        sa.Column("received_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("recorded_by", sa.String(length=32), nullable=False, server_default="webhook"),
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
    op.create_table(
        "integration_credentials",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("business_id", sa.Text(), nullable=False, server_default="default"),
        sa.Column(
            "provider",
            sa.Enum(
                "zoho_books",
                "quickbooks",
                "razorpay_invoices",
                "tally",
                name="integration_provider",
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
    op.add_column(
        "invoices",
        sa.Column("external_ids", sa.JSON(), nullable=False, server_default="{}"),
    )
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
