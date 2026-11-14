"""Alembic environment; `Database` passes its open connection in `config.attributes`."""

from alembic import context
from sqlalchemy import Connection

from mozg_outreach.storage.models import Base

connection: Connection = context.config.attributes["connection"]
# SQLite can't ALTER most constraints in place; batch mode rebuilds the table instead.
context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
with context.begin_transaction():
    context.run_migrations()
