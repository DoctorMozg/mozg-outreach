"""archive items

Archived leads leave the main report and later searches skip them.

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17 06:16:18
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(sa.Column("archived_at", sa.DateTime(), nullable=True))


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_column("archived_at")
