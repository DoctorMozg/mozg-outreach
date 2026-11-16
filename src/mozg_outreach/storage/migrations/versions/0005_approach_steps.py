"""approach steps

Drafts and Gmail threads carry the approach step they belong to; a sent letter keeps its
Message-ID and subject so a follow-up can reply to it.

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16 18:00:00
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

# Names SQLite's unnamed unique constraints on reflection, so batch mode can drop them.
UNNAMED = {"uq": "uq_%(table_name)s_%(column_0_name)s"}


def upgrade() -> None:
    with op.batch_alter_table("drafts", naming_convention=UNNAMED) as batch_op:
        batch_op.add_column(sa.Column("step", sa.Integer(), nullable=True))
        batch_op.drop_constraint("uq_drafts_item_id", type_="unique")
        batch_op.create_unique_constraint(
            "uq_drafts", ["item_id", "channel", "recipient", "step", "version"]
        )
    with op.batch_alter_table("mail_threads", naming_convention=UNNAMED) as batch_op:
        batch_op.add_column(sa.Column("step", sa.Integer(), nullable=True))
        batch_op.add_column(sa.Column("sent_message_id", sa.String(length=998), nullable=True))
        batch_op.add_column(sa.Column("sent_subject", sa.String(length=998), nullable=True))
        batch_op.drop_column("gmail_message_id")
        batch_op.drop_constraint("uq_mail_threads_item_id", type_="unique")
        batch_op.create_unique_constraint("uq_mail_threads", ["item_id", "recipient", "step"])


def downgrade() -> None:
    with op.batch_alter_table("mail_threads") as batch_op:
        batch_op.drop_constraint("uq_mail_threads", type_="unique")
        batch_op.create_unique_constraint("uq_mail_threads_item_id", ["item_id", "recipient"])
        batch_op.add_column(
            sa.Column("gmail_message_id", sa.BigInteger(), nullable=False, server_default="0")
        )
        batch_op.drop_column("sent_subject")
        batch_op.drop_column("sent_message_id")
        batch_op.drop_column("step")
    with op.batch_alter_table("drafts") as batch_op:
        batch_op.drop_constraint("uq_drafts", type_="unique")
        batch_op.create_unique_constraint(
            "uq_drafts_item_id", ["item_id", "channel", "recipient", "version"]
        )
        batch_op.drop_column("step")
