"""seeker mode

Job and freelance projects become seeker projects; their items keep their kind.

Revision ID: 0002
Revises: 0001
Create Date: 2026-11-15 05:57:24.457934
"""

from collections.abc import Sequence
from typing import Any

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

projects = sa.table(
    "projects",
    sa.column("id", sa.Integer),
    sa.column("mode", sa.String),
    sa.column("brief", sa.JSON),
)
items = sa.table(
    "items",
    sa.column("id", sa.Integer),
    sa.column("project_id", sa.Integer),
    sa.column("fields", sa.JSON),
    sa.column("details", sa.JSON),
)
SCOPES = {"JOB": "jobs", "FREELANCE": "gigs"}
# Old report columns of either mode, renamed to the one seeker set.
COLUMNS = {"salary": "pay", "budget": "pay", "client": "company", "gig": "role"}


def upgrade() -> None:
    bind = op.get_bind()
    old = bind.execute(
        sa.select(projects.c.id, projects.c.mode, projects.c.brief).where(
            projects.c.mode.in_(SCOPES)
        )
    ).all()
    for project_id, mode, brief in old:
        bind.execute(
            projects.update()
            .where(projects.c.id == project_id)
            .values(mode="SEEKER", brief=_brief(brief, SCOPES[str(mode)]))
        )
        for item_id, fields, details in bind.execute(
            sa.select(items.c.id, items.c.fields, items.c.details).where(
                items.c.project_id == project_id
            )
        ).all():
            bind.execute(
                items.update()
                .where(items.c.id == item_id)
                .values(fields=_fields(fields), details=_details(details))
            )
    with op.batch_alter_table("projects", schema=None) as batch_op:
        batch_op.alter_column(
            "mode",
            existing_type=sa.VARCHAR(length=9),
            type_=sa.Enum("BIZ", "SEEKER", name="projectmode"),
            existing_nullable=False,
        )


def downgrade() -> None:
    # Seeker briefs may search both kinds, which no single old mode can hold.
    raise NotImplementedError("seeker projects cannot be split back into job and freelance")


def _brief(brief: object, scope: str) -> dict[str, Any] | None:
    if not isinstance(brief, dict):
        return None
    other = "gigs" if scope == "jobs" else "jobs"
    return {
        "cv_text": brief["cv_text"],
        "preferences": brief["preferences"],
        scope: {"recency_days": brief["recency_days"], "limit": brief["limit"]},
        other: None,
    }


def _fields(fields: object) -> object:
    if not isinstance(fields, dict):
        return fields
    return {COLUMNS.get(name, name): value for name, value in fields.items()}


def _details(details: object) -> object:
    """Freelance items kept the listing under `gig`; seeker items keep it under `listing`."""
    if not isinstance(details, dict):
        return details
    return {("listing" if name == "gig" else name): value for name, value in details.items()}
