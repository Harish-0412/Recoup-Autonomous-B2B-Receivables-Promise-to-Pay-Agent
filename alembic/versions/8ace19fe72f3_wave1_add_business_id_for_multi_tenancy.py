"""wave1_add_business_id_for_multi_tenancy

Revision ID: 8ace19fe72f3
Revises: 0005_merge_drift_contact_timing
Create Date: 2026-09-04 21:04:15.727030

Wave 1 row-level isolation: one Postgres, many businesses.
- business_id TEXT NOT NULL on every tenant table, backfilled to the deploy's
  BUSINESS_ID ('default').
- Composite uniqueness (business_id, key); two tenants can both have INV-1042.
- Indexes for list queries: (business_id, status, due_date),
  (business_id, payment_link_id), plus per-table tenant indexes.
- businesses registry (API_KEY -> business_id map; later JWT org_id).

Idempotent: safe to run on a DB that already has business_id columns from a
partial run (SQLite non-transactional DDL), and on a fresh DB from 0001.
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "8ace19fe72f3"
down_revision: str | None = "0006_batch_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TENANT_TABLES = [
    "customers",
    "invoices",
    "promises",
    "contact_logs",
    "opt_outs",
    "inbound_replies",
    "decision_traces",
    "webhook_events",
    "batch_runs",
    "customer_drift_flags",
]


def _existing_columns(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        return {c["name"] for c in insp.get_columns(table)}
    except Exception:
        return set()


def _existing_indexes(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    try:
        return {i["name"] for i in insp.get_indexes(table)}
    except Exception:
        return set()


def _existing_uniques(table: str) -> set[str]:
    bind = op.get_bind()
    insp = sa.inspect(bind)
    names: set[str] = set()
    try:
        for uc in insp.get_unique_constraints(table):
            if uc.get("name"):
                names.add(uc["name"])
    except Exception:
        pass
    return names


def upgrade() -> None:
    # 1. business_id column on every tenant table (backfill default 'default').
    for table in TENANT_TABLES:
        if "business_id" not in _existing_columns(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                batch_op.add_column(
                    sa.Column(
                        "business_id",
                        sa.Text(),
                        nullable=False,
                        server_default="default",
                    )
                )
    # Backfill any NULLs left by databases that added the column nullable,
    # then enforce NOT NULL where the dialect allows it in batch mode.
    for table in TENANT_TABLES:
        try:
            op.execute(
                sa.text(f"UPDATE {table} SET business_id='default' WHERE business_id IS NULL")
            )
        except Exception:
            pass

    # 2. businesses registry.
    insp_tables = sa.inspect(op.get_bind()).get_table_names()
    if "businesses" not in insp_tables:
        op.create_table(
            "businesses",
            sa.Column("id", sa.Integer(), nullable=False, primary_key=True),
            sa.Column("business_id", sa.Text(), nullable=False),
            sa.Column("name", sa.String(length=255), nullable=False, server_default=""),
            sa.Column("api_key", sa.Text(), nullable=True),
            sa.Column("task_api_key", sa.Text(), nullable=True),
            sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
            sa.UniqueConstraint("business_id", name="uq_businesses_business_id"),
            sa.UniqueConstraint("api_key", name="uq_businesses_api_key"),
            sa.UniqueConstraint("task_api_key", name="uq_businesses_task_api_key"),
        )
        op.create_index("ix_businesses_business_id", "businesses", ["business_id"], unique=True)
        # Seed the default deploy owner so single-tenant installs keep working.
        try:
            op.execute(
                sa.text(
                    "INSERT INTO businesses (business_id, name, created_at) "
                    "VALUES ('default', 'default', CURRENT_TIMESTAMP)"
                )
            )
        except Exception:
            pass

    def _ensure_unique(table: str, name: str, cols: list[str]) -> None:
        if name in _existing_uniques(table):
            return
        with op.batch_alter_table(table, schema=None) as batch_op:
            try:
                batch_op.create_unique_constraint(name, cols)
            except Exception:
                pass

    def _ensure_index(table: str, name: str, cols: list[str]) -> None:
        if name in _existing_indexes(table):
            return
        with op.batch_alter_table(table, schema=None) as batch_op:
            try:
                batch_op.create_index(name, cols)
            except Exception:
                pass

    # 3. Composite uniqueness (drop global single-column uniques where present).
    # SQLite represents single-column uniques as autoindexes, not named
    # constraints, so there is usually nothing to drop -- creating the
    # composite is the actual change. Best-effort drops for PG-style names:
    for table, old_idx in (
        ("customers", "ix_customers_customer_id"),
        ("invoices", "ix_invoices_invoice_id"),
        ("promises", "ix_promises_promise_id"),
        ("webhook_events", "ix_webhook_events_event_id"),
    ):
        if old_idx in _existing_indexes(table):
            # Only drop when it is a UNIQUE index (global uniqueness); keep
            # plain lookup indexes. Inspect flags via get_indexes.
            try:
                insp = sa.inspect(op.get_bind())
                for idx in insp.get_indexes(table):
                    if idx["name"] == old_idx and idx.get("unique"):
                        with op.batch_alter_table(table, schema=None) as batch_op:
                            try:
                                batch_op.drop_index(old_idx)
                            except Exception:
                                pass
            except Exception:
                pass

    _ensure_unique("customers", "uq_customers_business_customer", ["business_id", "customer_id"])
    _ensure_unique("invoices", "uq_invoices_business_invoice", ["business_id", "invoice_id"])
    _ensure_unique("promises", "uq_promises_business_promise", ["business_id", "promise_id"])
    _ensure_unique(
        "webhook_events", "uq_webhook_events_business_event", ["business_id", "event_id"]
    )

    # 4. Tenant list-query indexes.
    _ensure_index(
        "invoices", "ix_invoices_business_status_due_date", ["business_id", "status", "due_date"]
    )
    _ensure_index(
        "invoices", "ix_invoices_business_payment_link_id", ["business_id", "payment_link_id"]
    )
    _ensure_index("promises", "ix_promises_business_status", ["business_id", "status"])
    _ensure_index(
        "contact_logs", "ix_contact_logs_business_invoice_pk", ["business_id", "invoice_pk"]
    )
    _ensure_index("opt_outs", "ix_opt_outs_business_customer_pk", ["business_id", "customer_pk"])
    _ensure_index(
        "inbound_replies",
        "ix_inbound_replies_business_customer_pk",
        ["business_id", "customer_pk"],
    )
    _ensure_index(
        "webhook_events",
        "ix_webhook_events_business_received_at",
        ["business_id", "received_at"],
    )
    _ensure_index("batch_runs", "ix_batch_runs_business_created_at", ["business_id", "created_at"])
    _ensure_index(
        "customer_drift_flags",
        "ix_customer_drift_flags_business_customer_pk",
        ["business_id", "customer_pk"],
    )
    # Model-level indexes declared in app.models.tables beyond the spec list:
    _ensure_index("customers", "ix_customers_business_id", ["business_id"])
    _ensure_index(
        "decision_traces",
        "ix_decision_traces_business_invoice",
        ["business_id", "invoice_id"],
    )
    _ensure_index(
        "inbound_replies",
        "ix_inbound_replies_business_received_at",
        ["business_id", "received_at"],
    )


def downgrade() -> None:
    for name, table in (
        ("ix_customer_drift_flags_business_customer_pk", "customer_drift_flags"),
        ("ix_batch_runs_business_created_at", "batch_runs"),
        ("ix_webhook_events_business_received_at", "webhook_events"),
        ("ix_inbound_replies_business_customer_pk", "inbound_replies"),
        ("ix_inbound_replies_business_received_at", "inbound_replies"),
        ("ix_opt_outs_business_customer_pk", "opt_outs"),
        ("ix_contact_logs_business_invoice_pk", "contact_logs"),
        ("ix_promises_business_status", "promises"),
        ("ix_invoices_business_payment_link_id", "invoices"),
        ("ix_invoices_business_status_due_date", "invoices"),
        ("ix_customers_business_id", "customers"),
        ("ix_decision_traces_business_invoice", "decision_traces"),
    ):
        if name in _existing_indexes(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                try:
                    batch_op.drop_index(name)
                except Exception:
                    pass

    for table, cname in (
        ("customers", "uq_customers_business_customer"),
        ("invoices", "uq_invoices_business_invoice"),
        ("promises", "uq_promises_business_promise"),
        ("webhook_events", "uq_webhook_events_business_event"),
    ):
        if cname in _existing_uniques(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                try:
                    batch_op.drop_constraint(cname, type_="unique")
                except Exception:
                    pass

    # Restore legacy global uniques as plain unique indexes (SQLite-friendly).
    for table, name, col in (
        ("customers", "ix_customers_customer_id", ["customer_id"]),
        ("invoices", "ix_invoices_invoice_id", ["invoice_id"]),
        ("promises", "ix_promises_promise_id", ["promise_id"]),
        ("webhook_events", "ix_webhook_events_event_id", ["event_id"]),
    ):
        if name not in _existing_indexes(table):
            # Only restore on a truly single-tenant DB; skip if duplicates exist.
            with op.batch_alter_table(table, schema=None) as batch_op:
                try:
                    batch_op.create_index(name, col, unique=True)
                except Exception:
                    pass

    if "businesses" in sa.inspect(op.get_bind()).get_table_names():
        try:
            op.drop_index("ix_businesses_business_id", table_name="businesses")
        except Exception:
            pass
        op.drop_table("businesses")

    for table in reversed(TENANT_TABLES):
        if "business_id" in _existing_columns(table):
            with op.batch_alter_table(table, schema=None) as batch_op:
                try:
                    batch_op.drop_column("business_id")
                except Exception:
                    pass
