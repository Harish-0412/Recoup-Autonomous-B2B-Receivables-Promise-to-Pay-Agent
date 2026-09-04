"""Record which send-time arm each contact used, and when it was recommended.

The bandit learns from genuine replies: a reply attributes reward 1 to the arm
of the last contact on its invoice. That attribution needs the arm stored next
to the contact, in the same unit of work as the send -- a side table written
later would silently drop the link on exactly the rows the bandit needs.

Both columns are nullable: contacts sent before this migration, or while no
trained artifact existed, simply carry no arm, and the bandit skips them.

Revision ID: 0004_contact_timing
Revises: 0003_inbound_replies
Create Date: 2026-09-04
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_contact_timing"
down_revision: str | None = "0003_inbound_replies"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("contact_logs", sa.Column("timing_arm", sa.String(32), nullable=True))
    op.add_column(
        "contact_logs",
        sa.Column("scheduled_for", sa.DateTime(timezone=True), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("contact_logs", "scheduled_for")
    op.drop_column("contact_logs", "timing_arm")
