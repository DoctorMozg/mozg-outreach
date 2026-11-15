from pathlib import Path

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select, text

from mozg_outreach.domain import ProjectMode
from mozg_outreach.storage.db import BASELINE, MIGRATIONS, Database, alembic_config
from mozg_outreach.storage.models import Base, Item, Project


def _revision(db: Database) -> str | None:
    with db.engine.connect() as connection:
        return MigrationContext.configure(connection).get_current_revision()


@pytest.fixture
def head() -> str:
    current = ScriptDirectory(str(MIGRATIONS)).get_current_head()
    assert current is not None
    return current


class TestMigrate:
    def test_fresh_database_matches_the_models(self, tmp_path: Path, head: str) -> None:
        db = Database(tmp_path / "fresh.db")
        db.migrate()

        with db.engine.connect() as connection:
            drift = compare_metadata(MigrationContext.configure(connection), Base.metadata)
        assert drift == [], "models changed without a migration; run mozg_outreach.storage.revision"
        assert _revision(db) == head

    def test_pre_migration_database_is_adopted_with_its_data(
        self, tmp_path: Path, head: str
    ) -> None:
        db = Database(tmp_path / "legacy.db")
        # Databases from before migrations have the baseline tables and no alembic_version.
        with db.engine.begin() as connection:
            command.upgrade(alembic_config(connection), BASELINE)
            connection.execute(text("DROP TABLE alembic_version"))
            connection.execute(
                text(
                    "INSERT INTO projects (name, mode, sender_tone, created_at) "
                    "VALUES ('Kept', 'BIZ', 'neutral', '2026-11-01')"
                )
            )

        db.migrate()

        assert _revision(db) == head
        with db.session() as session:
            assert session.scalars(select(Project.name)).all() == ["Kept"]

    def test_running_twice_changes_nothing(self, tmp_path: Path, head: str) -> None:
        db = Database(tmp_path / "twice.db")
        db.migrate()
        db.migrate()

        assert _revision(db) == head

    def test_baseline_is_the_first_revision(self) -> None:
        names = sorted(p.name for p in (MIGRATIONS / "versions").glob("*.py"))
        assert names[0].startswith(f"{BASELINE}_")


class TestSeekerMode:
    def test_job_and_freelance_projects_become_seeker_projects(self, tmp_path: Path) -> None:
        db = Database(tmp_path / "modes.db")
        with db.engine.begin() as connection:
            command.upgrade(alembic_config(connection), BASELINE)
            brief = '{"cv_text": "cv", "preferences": "remote", "recency_days": 14, "limit": 30}'
            connection.execute(
                text(
                    "INSERT INTO projects (id, name, mode, sender_tone, brief, created_at) VALUES "
                    "(1, 'Jobs', 'JOB', 'neutral', :b, '2026-11-01'), "
                    "(2, 'Gigs', 'FREELANCE', 'neutral', :b, '2026-11-01')"
                ),
                {"b": brief},
            )
            connection.execute(
                text(
                    "INSERT INTO items (project_id, kind, dedup_key, title, fields, raw, details, "
                    "selected, created_at, updated_at) "
                    "VALUES (2, 'GIG', 'g', 'Gig', :f, '{}', :d, 0, '2026-11-01', '2026-11-01')"
                ),
                {"f": '{"budget": {"value": "EUR 700/day"}}', "d": '{"gig": {"title": "API"}}'},
            )

        db.migrate()

        with db.session() as session:
            jobs, gigs = session.scalars(select(Project).order_by(Project.id)).all()
            item = session.scalars(select(Item)).one()
            assert (jobs.mode, gigs.mode) == (ProjectMode.SEEKER, ProjectMode.SEEKER)
            assert jobs.brief is not None
            assert gigs.brief is not None
            assert jobs.brief["jobs"] == {"recency_days": 14, "limit": 30}
            assert (jobs.brief["gigs"], gigs.brief["jobs"]) == (None, None)
            assert item.fields == {"pay": {"value": "EUR 700/day"}}
            assert item.details == {"listing": {"title": "API"}}
