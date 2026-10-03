"""Migration acceptance on disposable databases, independent historical DDL and real role grants."""

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from alembic import command
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from job_finder.persistence import schema_migrations as migrations

LEGACY_SCHEMA = (Path(__file__).with_name("fixtures") / "schema_v2.sql").read_text(encoding="utf-8")


class SchemaMigrationTests(unittest.TestCase):
    def setUp(self):
        if os.getenv("JOBFINDER_TEST_MODE") != "1":
            self.skipTest("Migrationstests nur über scripts/test_postgres.py gegen isolierte Test-DBs.")
        self.server_url = os.environ["JOBFINDER_DATABASE_URL"]
        if not conninfo_to_dict(self.server_url)["dbname"].endswith("_test"):
            raise RuntimeError("Migrationstests benötigen eine separate _test-Datenbank.")
        self.name = f"jobfinder_migration_{uuid4().hex}_test"
        with psycopg.connect(self.server_url, autocommit=True) as connection:
            connection.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(self.name)))
        self.addCleanup(self.drop_database)
        self.url = make_conninfo(self.server_url, dbname=self.name)
        environment = patch.dict(
            os.environ, {"JOBFINDER_ADMIN_DATABASE_URL": self.url, "JOBFINDER_DATABASE_URL": self.url}
        )
        environment.start()
        self.addCleanup(environment.stop)

    def drop_database(self):
        with psycopg.connect(self.server_url, autocommit=True) as connection:
            connection.execute(sql.SQL("DROP DATABASE {}").format(sql.Identifier(self.name)))

    def execute(self, statement, params=None):
        with psycopg.connect(self.url) as connection:
            return (
                connection.execute(statement, params).fetchall()
                if statement.startswith("SELECT")
                else connection.execute(statement, params)
            )

    def legacy(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(LEGACY_SCHEMA)
            connection.execute("INSERT INTO schema_version VALUES (2)")

    def seed(self):
        """Include decision, history, document reference, publications, notifications and cost ledger."""
        statements = (
            "INSERT INTO job_state (scope,job_id,workflow_status,present,extra) VALUES ('default','test:1','interview','{}','{\"note\":\"kept\"}')",
            "INSERT INTO workflow_history (scope,job_id,position,status,present,extra) VALUES ('default','test:1',0,'applied','{}','{}')",
            "INSERT INTO application_documents (scope,job_id,position,stored_name,present,extra) VALUES ('default','test:1',0,'example.pdf','{}','{}')",
            "INSERT INTO datasets (name,metadata) VALUES ('example.json','{}')",
            "INSERT INTO jobs (dataset,job_id,position,present,extra) VALUES ('example.json','test:1',0,'{}','{}')",
            "INSERT INTO recommendations (dataset,job_id,position,present,extra) VALUES ('example.json','test:1',0,'{}','{}')",
            "INSERT INTO notifications (dataset,notification_key,delivery_state,present,extra) VALUES ('example.json','test:1','pending','{}','{}')",
            "INSERT INTO source_cache (dataset,cache_key,position,payload) VALUES ('example.json','example',0,'{}')",
            "INSERT INTO manual_sources (dataset,url,position,payload) VALUES ('example.json','https://example.test',0,'{}')",
            "INSERT INTO agent_usage (job_id,model,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,cost_eur) VALUES ('test:1','mock',10,0,5,0,0.00123)",
            "INSERT INTO agent_fact_sheets (scope,job_id,model,complete,cost_eur) VALUES ('default','test:1','mock',false,0.00123)",
        )
        with psycopg.connect(self.url) as connection:
            for statement in statements:
                connection.execute(statement)

    def rows(self):
        result = {}
        with psycopg.connect(self.url) as connection:
            tables = connection.execute(
                "SELECT tablename FROM pg_tables WHERE schemaname='public' AND tablename <> 'alembic_version' ORDER BY tablename"
            ).fetchall()
            for (table,) in tables:
                result[table] = connection.execute(
                    sql.SQL("SELECT row_to_json(t)::text FROM {} t ORDER BY row_to_json(t)::text").format(
                        sql.Identifier(table)
                    )
                ).fetchall()
        return result

    def signature(self):
        """Use PostgreSQL's own catalog representation, independently of the validator."""
        with psycopg.connect(self.url) as connection:
            columns = connection.execute(
                "SELECT c.relname,a.attname,format_type(a.atttypid,a.atttypmod),a.attnotnull,pg_get_expr(d.adbin,d.adrelid) "
                "FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace JOIN pg_attribute a ON a.attrelid=c.oid "
                "LEFT JOIN pg_attrdef d ON d.adrelid=c.oid AND d.adnum=a.attnum "
                "WHERE n.nspname='public' AND c.relkind='r' AND a.attnum>0 AND NOT a.attisdropped ORDER BY c.relname,a.attnum"
            ).fetchall()
            constraints = connection.execute(
                "SELECT c.relname,con.conname,pg_get_constraintdef(con.oid) FROM pg_constraint con "
                "JOIN pg_class c ON c.oid=con.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public' ORDER BY c.relname,con.conname"
            ).fetchall()
            indexes = connection.execute(
                "SELECT tablename,indexname,indexdef FROM pg_indexes WHERE schemaname='public' ORDER BY tablename,indexname"
            ).fetchall()
        return columns, constraints, indexes

    def test_status_is_readonly_and_does_not_create_a_version_table(self):
        original = migrations._status
        readonly = []

        def inspect(connection):
            readonly.append(connection.exec_driver_sql("SHOW transaction_read_only").scalar())
            return original(connection)

        with patch.object(migrations, "_status", side_effect=inspect):
            self.assertEqual(migrations.schema_status()["state"], "empty")
        self.assertEqual(readonly, ["on"])
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])

    def test_empty_database_uses_alembic_and_reruns_preserve_rows(self):
        self.assertEqual(migrations.migrate()["action"], "created")
        self.seed()
        before = self.rows()
        self.assertEqual(migrations.migrate()["action"], "unchanged")
        self.assertEqual(self.rows(), before)
        self.assertEqual(
            migrations.schema_status(), {"state": "current", "revision": "0001_baseline", "head": "0001_baseline"}
        )

    def test_existing_database_is_validated_then_stamped_without_changing_data(self):
        self.legacy()
        self.seed()
        before = self.rows()
        self.assertEqual(migrations.schema_status()["state"], "legacy")
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])
        self.assertEqual(migrations.migrate()["action"], "adopted")
        self.assertEqual(self.rows(), before)

    def test_new_and_adopted_database_have_identical_structures(self):
        migrations.migrate()
        fresh = self.signature()
        self.execute("DROP SCHEMA public CASCADE")
        self.execute("CREATE SCHEMA public")
        self.legacy()
        migrations.migrate()
        self.assertEqual(self.signature(), fresh)

    def test_known_import_journal_is_validated_and_preserved_without_recreating_it(self):
        self.legacy()
        self.execute(
            "CREATE TABLE migration_runs (source_fingerprint text PRIMARY KEY, completed_at timestamptz NOT NULL DEFAULT now(), summary jsonb NOT NULL)"
        )
        self.execute("INSERT INTO migration_runs (source_fingerprint,summary) VALUES ('example','{\"verified\":true}')")
        before = self.rows()
        self.assertEqual(migrations.migrate()["action"], "adopted")
        self.assertEqual(self.rows(), before)
        self.execute("ALTER TABLE migration_runs ALTER COLUMN summary TYPE text USING summary::text")
        with self.assertRaisesRegex(RuntimeError, "Struktur"):
            migrations.migrate()

    def test_schema_drift_refuses_adoption_and_never_repairs_it(self):
        changes = (
            "ALTER TABLE agent_usage DROP COLUMN web_searches",
            "ALTER TABLE job_state ALTER COLUMN salary_expectation_eur TYPE integer",
            "ALTER TABLE job_state ALTER COLUMN extra DROP NOT NULL",
            "ALTER TABLE agent_usage ALTER COLUMN web_searches SET DEFAULT 1",
            "ALTER TABLE job_state ADD COLUMN unexpected text",
            "CREATE TABLE unexpected (id integer)",
            "DROP INDEX job_state_status",
            "ALTER TABLE notifications DROP CONSTRAINT notifications_delivery_state_check",
            "ALTER TABLE notifications DROP CONSTRAINT notifications_delivery_state_check; ALTER TABLE notifications ADD CHECK (delivery_state IN ('sent','pending','other'))",
            "ALTER TABLE jobs DROP CONSTRAINT jobs_pkey",
            "ALTER TABLE workflow_history DROP CONSTRAINT workflow_history_scope_job_id_fkey",
            "ALTER TABLE workflow_history DROP CONSTRAINT workflow_history_scope_job_id_fkey; ALTER TABLE workflow_history ADD FOREIGN KEY (scope,job_id) REFERENCES job_state",
            "CREATE UNIQUE INDEX unexpected ON job_state(title)",
            "CREATE VIEW unexpected AS SELECT job_id FROM job_state",
            "ALTER TABLE job_state ENABLE ROW LEVEL SECURITY",
            "ALTER TABLE job_state ADD CHECK (workflow_status <> 'bad')",
            "ALTER TABLE notifications DROP CONSTRAINT notifications_delivery_state_check; ALTER TABLE notifications ADD CHECK (delivery_state IN ('sent','pen ding'))",
            "DROP INDEX job_state_status; CREATE INDEX job_state_status ON job_state(scope,workflow_status) WHERE workflow_status='new'",
            "DROP INDEX job_state_status; CREATE INDEX job_state_status ON job_state(scope,workflow_status DESC)",
            "DROP INDEX job_state_status; CREATE INDEX job_state_status ON job_state(scope,workflow_status) INCLUDE (title)",
            "DROP INDEX job_state_status; CREATE INDEX job_state_status ON job_state(scope text_pattern_ops,workflow_status)",
            "ALTER TABLE workflow_history DROP CONSTRAINT workflow_history_scope_job_id_fkey; ALTER TABLE workflow_history ADD CONSTRAINT workflow_history_scope_job_id_fkey FOREIGN KEY (scope,job_id) REFERENCES job_state ON DELETE CASCADE NOT VALID",
        )
        for change in changes:
            with self.subTest(change=change):
                self.legacy()
                self.execute(change)
                before = self.signature()
                with self.assertRaisesRegex(RuntimeError, "Struktur"):
                    migrations.migrate()
                self.assertEqual(self.signature(), before)
                self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])
                self.execute("DROP SCHEMA public CASCADE")
                self.execute("CREATE SCHEMA public")

    def test_partial_database_is_not_filled_in(self):
        self.execute("CREATE TABLE job_state (scope text, job_id text)")
        with self.assertRaisesRegex(RuntimeError, "Struktur"):
            migrations.migrate()
        self.assertEqual(self.execute("SELECT to_regclass('public.datasets')"), [(None,)])

    def test_unknown_legacy_version_is_not_stamped(self):
        self.legacy()
        self.execute("UPDATE schema_version SET version=999")
        with self.assertRaisesRegex(RuntimeError, "Schemaversion"):
            migrations.migrate()
        self.assertEqual(self.execute("SELECT version FROM schema_version"), [(999,)])
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])

    def test_unknown_alembic_revision_is_not_changed(self):
        migrations.migrate()
        self.execute("UPDATE alembic_version SET version_num='unknown'")
        with self.assertRaisesRegex(RuntimeError, "Migrationsstand"):
            migrations.migrate()
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [("unknown",)])

    def test_malformed_alembic_marker_is_not_adopted(self):
        self.legacy()
        self.execute("CREATE TABLE alembic_version (version_num text)")
        with self.assertRaisesRegex(RuntimeError, "Migrationsmetadaten"):
            migrations.migrate()
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [])

    def future_revision(self, *, fail=False):
        """Build a later revision only in a temporary migration directory."""
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        target = Path(directory.name)
        (target / "versions").mkdir()
        source = Path(migrations.__file__).with_name("migrations")
        for relative in ("env.py", "versions/0001_baseline.py"):
            (target / relative).write_text((source / relative).read_text(encoding="utf-8"), encoding="utf-8")
        (target / "versions/0002_example.py").write_text(
            'from alembic import op\nimport sqlalchemy as sa\nrevision="0002_example"\ndown_revision="0001_baseline"\n'
            'def upgrade():\n    op.add_column("job_state", sa.Column("example_marker", sa.Text, nullable=True))\n'
            + ('    raise RuntimeError("injected revision failure")\n' if fail else ""),
            encoding="utf-8",
        )
        original = migrations.migration_config

        def config(connection=None):
            result = original(connection)
            result.set_main_option("script_location", str(target).replace("%", "%%"))
            return result

        override = patch.object(migrations, "migration_config", side_effect=config)
        override.start()
        self.addCleanup(override.stop)

    def test_a_later_revision_upgrades_the_existing_database_without_losing_rows(self):
        migrations.migrate()
        self.seed()
        before = self.execute("SELECT workflow_status,extra FROM job_state")
        self.future_revision()
        self.assertEqual(migrations.schema_status()["state"], "outdated")
        result = migrations.migrate()
        self.assertEqual((result["action"], result["revision"]), ("upgraded", "0002_example"))
        self.assertEqual(self.execute("SELECT workflow_status,extra FROM job_state"), before)
        self.assertEqual(self.execute("SELECT example_marker FROM job_state"), [(None,)])

    def test_a_failed_later_revision_rolls_back_schema_and_revision(self):
        migrations.migrate()
        self.seed()
        before = self.signature(), self.rows()
        self.future_revision(fail=True)
        with self.assertRaisesRegex(RuntimeError, "injected revision failure"):
            migrations.migrate()
        self.assertEqual((self.signature(), self.rows()), before)
        self.assertEqual(self.execute("SELECT version_num FROM alembic_version"), [("0001_baseline",)])

    def test_failure_after_stamping_rolls_back_the_marker_and_preserves_data(self):
        self.legacy()
        self.seed()
        before = self.rows()
        with (
            patch.object(migrations.command, "upgrade", side_effect=RuntimeError("injected failure")),
            self.assertRaisesRegex(RuntimeError, "injected"),
        ):
            migrations.migrate()
        self.assertEqual(self.rows(), before)
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])

    def test_failure_after_creating_schema_rolls_back_ddl_and_version(self):
        original = command.upgrade

        def fail_after_ddl(config, target):
            original(config, target)
            raise RuntimeError("injected failure")

        with (
            patch.object(migrations.command, "upgrade", side_effect=fail_after_ddl),
            self.assertRaisesRegex(RuntimeError, "injected"),
        ):
            migrations.migrate()
        self.assertEqual(migrations.schema_status()["state"], "empty")
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])

    def test_direct_unvalidated_stamp_is_refused(self):
        self.legacy()
        with self.assertRaisesRegex(RuntimeError, "ungeprüftes"):
            command.stamp(migrations.migration_config(), "head")
        self.assertEqual(self.execute("SELECT to_regclass('public.alembic_version')"), [(None,)])

    def test_destructive_baseline_downgrade_is_refused(self):
        migrations.migrate()
        self.seed()
        before = self.rows()
        with self.assertRaisesRegex(RuntimeError, "nicht zurückgebaut"), migrations.admin_connection() as connection:
            command.downgrade(migrations.migration_config(connection), "base")
        self.assertEqual(self.rows(), before)
        self.assertEqual(migrations.schema_status()["state"], "current")

    def test_migration_markers_are_private_even_when_app_grants_precede_initialization(self):
        role = f"migration_app_{uuid4().hex}"
        with psycopg.connect(self.url) as connection:
            connection.execute(
                sql.SQL("CREATE ROLE {} NOSUPERUSER NOCREATEDB NOCREATEROLE").format(sql.Identifier(role))
            )
            connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(role)))
            connection.execute(
                sql.SQL(
                    "ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE,DELETE ON TABLES TO {}"
                ).format(sql.Identifier(role))
            )

        def cleanup_role():
            with psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

        self.addCleanup(cleanup_role)
        migrations.migrate()
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
            connection.execute("SELECT * FROM job_state")
            connection.execute(
                "INSERT INTO job_state (scope,job_id,present,extra) VALUES ('default','test:1','{}','{}')"
            )
            connection.execute("UPDATE job_state SET workflow_status='new'")
            connection.execute("DELETE FROM job_state")
        for statement in (
            "SELECT * FROM alembic_version",
            "SELECT * FROM schema_version",
            "CREATE TABLE forbidden (id int)",
            "ALTER TABLE job_state ADD COLUMN forbidden int",
        ):
            with (
                self.subTest(statement=statement),
                self.assertRaises(errors.InsufficientPrivilege),
                psycopg.connect(self.url) as connection,
            ):
                connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
                connection.execute(statement)


if __name__ == "__main__":
    unittest.main()
