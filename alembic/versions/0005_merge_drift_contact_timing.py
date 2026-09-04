"""Merge the drift-flags and contact-timing branches

Two features landed concurrently on top of 0003, producing two heads. This
revision merges them so ``head`` is single again -- CI's
upgrade/downgrade/upgrade round-trip assumes one chain. No schema change
lives here; both branches apply in dependency order.

Revision ID: 0005_merge_drift_contact_timing
Revises: 0004_customer_drift_flags, 0004_contact_timing
Create Date: 2026-09-04
"""

from collections.abc import Sequence

revision: str = "0005_merge_drift_contact_timing"
down_revision: tuple[str, ...] = (
    "0004_customer_drift_flags",
    "0004_contact_timing",
)
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    pass


def downgrade() -> None:
    pass
