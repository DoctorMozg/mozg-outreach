"""Autogenerate a migration: `uv run python -m mozg_outreach.storage.revision "add x"`."""

import argparse
import tempfile
from pathlib import Path

from alembic import command

from mozg_outreach.storage.db import Database, alembic_config


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("message", help="Short description; becomes the file's slug.")
    parser.add_argument("--rev-id", help="Revision id; defaults to the next 4-digit number.")
    args = parser.parse_args()
    with tempfile.TemporaryDirectory() as scratch:
        # Diff the models against a database already at head, so only the new change is emitted.
        db = Database(Path(scratch) / "head.db")
        db.migrate()
        with db.engine.begin() as connection:
            config = alembic_config(connection)
            command.revision(
                config, message=args.message, autogenerate=True, rev_id=args.rev_id or _next_id()
            )


def _next_id() -> str:
    versions = Path(__file__).with_name("migrations") / "versions"
    numbers = [int(p.name[:4]) for p in versions.glob("[0-9][0-9][0-9][0-9]_*.py")]
    return f"{max(numbers, default=0) + 1:04d}"


if __name__ == "__main__":
    main()
