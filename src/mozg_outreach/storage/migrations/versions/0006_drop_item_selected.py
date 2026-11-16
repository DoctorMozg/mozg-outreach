"""drop item selected

Any lead can be enriched or drafted, so the saved selection flag goes; the list's checkboxes
now live only on the page.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16 19:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.drop_column("selected")


def downgrade() -> None:
    with op.batch_alter_table("items") as batch_op:
        batch_op.add_column(
            sa.Column("selected", sa.Boolean(), nullable=False, server_default=sa.false())
        )
