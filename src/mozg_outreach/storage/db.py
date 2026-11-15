import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine, event, inspect
from sqlalchemy.engine.interfaces import DBAPIConnection
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import ConnectionPoolEntry

logger = logging.getLogger(__name__)

MIGRATIONS = Path(__file__).with_name("migrations")
# Databases made by `create_all` before migrations existed match this revision.
BASELINE = "0001"


class Database:
    """Local SQLite store. Calls are sync: single-user writes take milliseconds."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.engine: Engine = create_engine(f"sqlite:///{path}")
        event.listen(self.engine, "connect", _sqlite_pragmas)
        self._sessions = sessionmaker(self.engine, expire_on_commit=False)

    def migrate(self) -> None:
        """Bring the schema to the newest revision, adopting a pre-migration database first."""
        with self.engine.connect() as connection:
            # Batch migrations rebuild a table by dropping it, which with foreign keys on would
            # cascade-delete its children. SQLite only changes this pragma outside a transaction.
            connection.exec_driver_sql("PRAGMA foreign_keys=OFF")
            connection.commit()
            try:
                with connection.begin():
                    self._upgrade(connection)
            finally:
                connection.exec_driver_sql("PRAGMA foreign_keys=ON")
                connection.commit()

    @staticmethod
    def _upgrade(connection: Connection) -> None:
        config = alembic_config(connection)
        tables = inspect(connection).get_table_names()
        if tables and "alembic_version" not in tables:
            logger.info("stamping a pre-migration database at revision %s", BASELINE)
            command.stamp(config, BASELINE)
        command.upgrade(config, "head")
        broken = connection.exec_driver_sql("PRAGMA foreign_key_check").all()
        if broken:
            raise RuntimeError(f"migration left rows with dangling foreign keys: {broken[:5]}")

    @contextmanager
    def session(self) -> Iterator[Session]:
        with self._sessions.begin() as session:
            yield session


def alembic_config(connection: Connection) -> Config:
    config = Config()
    config.set_main_option("script_location", str(MIGRATIONS))
    config.attributes["connection"] = connection
    return config


def _sqlite_pragmas(dbapi_connection: DBAPIConnection, _record: ConnectionPoolEntry) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys=ON")
    cursor.execute("PRAGMA journal_mode=WAL")
    cursor.close()
