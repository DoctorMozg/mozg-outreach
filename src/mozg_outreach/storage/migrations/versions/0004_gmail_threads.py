"""gmail threads

Email letters put into the user's Gmail Drafts, with their sent, replied and bounced times.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16 13:54:48.724210
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "mail_threads",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=False),
        sa.Column("draft_id", sa.Integer(), nullable=False),
        sa.Column("address", sa.String(length=320), nullable=False),
        sa.Column("gmail_thread_id", sa.BigInteger(), nullable=False),
        sa.Column("gmail_message_id", sa.BigInteger(), nullable=False),
        sa.Column("pushed_at", sa.DateTime(), nullable=False),
        sa.Column("sent_at", sa.DateTime(), nullable=True),
        sa.Column("replied_at", sa.DateTime(), nullable=True),
        sa.Column("bounced_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["draft_id"], ["drafts.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "recipient"),
    )
    with op.batch_alter_table("mail_threads", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_mail_threads_item_id"), ["item_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("mail_threads", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_mail_threads_item_id"))

    op.drop_table("mail_threads")
