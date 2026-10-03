"""Use only the validated admin transaction supplied by the database maintenance command."""

from alembic import context

connection = context.config.attributes.get("connection")
if connection is None or context.is_offline_mode():
    raise RuntimeError("Migrationen mit python -m job_finder.db migrate ausführen; kein ungeprüftes stamp/upgrade.")

context.configure(connection=connection, version_table_schema="public")
with context.begin_transaction():
    context.run_migrations()
