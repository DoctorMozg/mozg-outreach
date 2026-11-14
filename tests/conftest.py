from collections.abc import Iterator
from pathlib import Path

import pytest
from sqlalchemy.orm import Session

from mozg_outreach.domain import ProjectMode
from mozg_outreach.storage.db import Database
from mozg_outreach.storage.models import Project, Run


@pytest.fixture
def db(tmp_path: Path) -> Database:
    database = Database(tmp_path / "outreach.db")
    database.migrate()
    return database


@pytest.fixture
def session(db: Database) -> Iterator[Session]:
    with db.session() as s:
        yield s


@pytest.fixture
def project(session: Session) -> Project:
    p = Project(name="DACH DevOps", mode=ProjectMode.BIZ, sender_name="Stan", brief=None)
    session.add(p)
    session.flush()
    return p


@pytest.fixture
def run(session: Session, project: Project) -> Run:
    r = Run(project_id=project.id, pipeline="biz_research")
    session.add(r)
    session.flush()
    return r
