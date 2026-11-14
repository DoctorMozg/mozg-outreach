"""baseline

Revision ID: 0001
Revises:
Create Date: 2026-11-14 21:03:35.302300
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "entities",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.Enum("PERSON", "COMPANY", name="entitykind"), nullable=False),
        sa.Column("name", sa.String(length=300), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_entities_kind"), ["kind"], unique=False)

    op.create_table(
        "projects",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.Column("mode", sa.Enum("BIZ", "JOB", "FREELANCE", name="projectmode"), nullable=False),
        sa.Column("sender_name", sa.String(length=100), nullable=True),
        sa.Column("sender_tone", sa.String(length=10), nullable=False),
        sa.Column("brief", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "voice_profiles",
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("profile", sa.JSON(), nullable=False),
        sa.Column("built_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("channel"),
    )
    op.create_table(
        "voice_samples",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=20), nullable=False),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("held_out", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("voice_samples", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_voice_samples_channel"), ["channel"], unique=False)

    op.create_table(
        "entity_identifiers",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "DOMAIN", "EMAIL", "LINKEDIN", "TELEGRAM", "NAME_AT_COMPANY", name="identifierkind"
            ),
            nullable=False,
        ),
        sa.Column("value", sa.String(length=300), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("kind", "value"),
    )
    with op.batch_alter_table("entity_identifiers", schema=None) as batch_op:
        batch_op.create_index(
            batch_op.f("ix_entity_identifiers_entity_id"), ["entity_id"], unique=False
        )

    op.create_table(
        "field_spec_versions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("spec", sa.JSON(), nullable=False),
        sa.Column("accepted_at", sa.DateTime(), nullable=True),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "version"),
    )
    op.create_table(
        "merge_suggestions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_a_id", sa.Integer(), nullable=False),
        sa.Column("entity_b_id", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(length=300), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entity_a_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["entity_b_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_a_id", "entity_b_id"),
    )
    op.create_table(
        "relations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("from_id", sa.Integer(), nullable=False),
        sa.Column("to_id", sa.Integer(), nullable=False),
        sa.Column("type", sa.String(length=40), nullable=False),
        sa.Column("detail", sa.String(length=300), nullable=True),
        sa.Column("source_url", sa.String(length=1000), nullable=True),
        sa.Column("quote", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["from_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["to_id"], ["entities.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("from_id", "to_id", "type"),
    )
    with op.batch_alter_table("relations", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_relations_from_id"), ["from_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_relations_to_id"), ["to_id"], unique=False)

    op.create_table(
        "runs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("pipeline", sa.String(length=60), nullable=False),
        sa.Column(
            "status",
            sa.Enum(
                "PENDING",
                "RUNNING",
                "AWAITING_GATE",
                "COMPLETE",
                "FAILED",
                "CANCELLED",
                name="runstatus",
            ),
            nullable=False,
        ),
        sa.Column("phase", sa.String(length=60), nullable=True),
        sa.Column("pending_gate", sa.JSON(), nullable=True),
        sa.Column("state", sa.JSON(), nullable=False),
        sa.Column("field_spec_version", sa.Integer(), nullable=True),
        sa.Column("cost_usd", sa.Double(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("started_at", sa.DateTime(), nullable=False),
        sa.Column("finished_at", sa.DateTime(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "source_configs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("config", sa.JSON(), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column("cursor", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "items",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=False),
        sa.Column("last_run_id", sa.Integer(), nullable=True),
        sa.Column("kind", sa.Enum("COMPANY", "JOB", "GIG", name="itemkind"), nullable=False),
        sa.Column("dedup_key", sa.String(length=300), nullable=False),
        sa.Column("title", sa.String(length=300), nullable=False),
        sa.Column("fields", sa.JSON(), nullable=False),
        sa.Column("raw", sa.JSON(), nullable=False),
        sa.Column("score_total", sa.Double(), nullable=True),
        sa.Column("score", sa.JSON(), nullable=True),
        sa.Column("details", sa.JSON(), nullable=False),
        sa.Column("selected", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.Column("updated_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["last_run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("project_id", "dedup_key"),
    )
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_items_score_total"), ["score_total"], unique=False)

    op.create_table(
        "run_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("run_id", sa.Integer(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("payload", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("run_id", "seq"),
    )
    op.create_table(
        "sightings",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_kind", sa.Enum("WEB", "TELEGRAM", "FEED", name="sourcekind"), nullable=False
        ),
        sa.Column("source_url", sa.String(length=1000), nullable=False),
        sa.Column("quote", sa.Text(), nullable=False),
        sa.Column("project_id", sa.Integer(), nullable=True),
        sa.Column("run_id", sa.Integer(), nullable=True),
        sa.Column("seen_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["project_id"], ["projects.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["run_id"], ["runs.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("entity_id", "source_url", "quote"),
    )
    with op.batch_alter_table("sightings", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_sightings_entity_id"), ["entity_id"], unique=False)

    op.create_table(
        "drafts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("channel", sa.String(length=40), nullable=False),
        sa.Column("recipient", sa.String(length=200), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("subject", sa.String(length=200), nullable=True),
        sa.Column("body", sa.Text(), nullable=False),
        sa.Column("review", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "channel", "recipient", "version"),
    )
    op.create_table(
        "interactions",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("note", sa.Text(), nullable=False),
        sa.Column("happened_at", sa.DateTime(), nullable=False),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    with op.batch_alter_table("interactions", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_interactions_item_id"), ["item_id"], unique=False)

    op.create_table(
        "item_links",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("item_id", sa.Integer(), nullable=False),
        sa.Column("entity_id", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(["entity_id"], ["entities.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["item_id"], ["items.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("item_id", "entity_id", "role"),
    )
    with op.batch_alter_table("item_links", schema=None) as batch_op:
        batch_op.create_index(batch_op.f("ix_item_links_entity_id"), ["entity_id"], unique=False)
        batch_op.create_index(batch_op.f("ix_item_links_item_id"), ["item_id"], unique=False)


def downgrade() -> None:
    with op.batch_alter_table("item_links", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_item_links_item_id"))
        batch_op.drop_index(batch_op.f("ix_item_links_entity_id"))

    op.drop_table("item_links")
    with op.batch_alter_table("interactions", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_interactions_item_id"))

    op.drop_table("interactions")
    op.drop_table("drafts")
    with op.batch_alter_table("sightings", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_sightings_entity_id"))

    op.drop_table("sightings")
    op.drop_table("run_events")
    with op.batch_alter_table("items", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_items_score_total"))

    op.drop_table("items")
    op.drop_table("source_configs")
    op.drop_table("runs")
    with op.batch_alter_table("relations", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_relations_to_id"))
        batch_op.drop_index(batch_op.f("ix_relations_from_id"))

    op.drop_table("relations")
    op.drop_table("merge_suggestions")
    op.drop_table("field_spec_versions")
    with op.batch_alter_table("entity_identifiers", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_entity_identifiers_entity_id"))

    op.drop_table("entity_identifiers")
    with op.batch_alter_table("voice_samples", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_voice_samples_channel"))

    op.drop_table("voice_samples")
    op.drop_table("voice_profiles")
    op.drop_table("projects")
    with op.batch_alter_table("entities", schema=None) as batch_op:
        batch_op.drop_index(batch_op.f("ix_entities_kind"))

    op.drop_table("entities")
