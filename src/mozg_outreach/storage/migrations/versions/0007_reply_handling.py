"""reply handling

An interaction can carry a date (an out-of-office's return, a "not now" date). Mail threads
note auto-replies and the sent text, and suppressed addresses get their own table.

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17 12:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    with op.batch_alter_table("interactions") as batch_op:
        batch_op.add_column(sa.Column("until", sa.Date(), nullable=True))
    with op.batch_alter_table("mail_threads") as batch_op:
        batch_op.add_column(sa.Column("auto_replied_at", sa.DateTime(), nullable=True))
        batch_op.add_column(sa.Column("sent_body", sa.Text(), nullable=True))
    op.create_table(
        "suppressions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("domain", sa.String(length=255), nullable=False),
        sa.Column("reason", sa.String(length=40), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("address"),
    )
    with op.batch_alter_table("suppressions") as batch_op:
        batch_op.create_index(batch_op.f("ix_suppressions_domain"), ["domain"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("suppressions") as batch_op:
        batch_op.drop_index(batch_op.f("ix_suppressions_domain"))
    op.drop_table("suppressions")
    with op.batch_alter_table("mail_threads") as batch_op:
        batch_op.drop_column("sent_body")
        batch_op.drop_column("auto_replied_at")
    with op.batch_alter_table("interactions") as batch_op:
        batch_op.drop_column("until")
