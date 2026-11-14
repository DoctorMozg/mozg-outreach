from pathlib import Path

import pytest
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import select

from mozg_outreach.domain import ProjectMode
from mozg_outreach.storage.db import BASELINE, MIGRATIONS, Database
from mozg_outreach.storage.models import Base, Project


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
        Base.metadata.create_all(db.engine)
        with db.session() as session:
            session.add(Project(name="Kept", mode=ProjectMode.JOB, brief=None))

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
