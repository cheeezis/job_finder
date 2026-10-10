"""Exercise separate runtime principals in PostgreSQL, including indirect delete paths."""

import importlib.util
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote, urlencode, urlunsplit
from uuid import uuid4

import psycopg
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from job_finder.models import Job, JobSource
from job_finder.persistence import postgres_store as store, runtime_permissions as permissions
from job_finder.persistence.agent_usage import spent_today_and_this_month
from job_finder.persistence.database import database_url, transaction
from job_finder.persistence.migrations.runtime_boundaries import dataset_condition
from job_finder.workflow.manual_import import import_manual_url
from job_finder.workflow.review_actions import start_application, update_review_decision, update_review_note
from job_finder.workflow.review_data import attach_fact_sheets, load_review_jobs


def load_runtime_script():
    scripts = Path(__file__).resolve().parents[1] / "scripts"
    with patch.object(sys, "path", [str(scripts), *sys.path]):
        spec = importlib.util.spec_from_file_location("create_runtime_roles_test", scripts / "create_runtime_roles.py")
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
    return script


def provisioning_uri(info):
    """Keep optional libpq credentials and TLS options in the provisioner's URI input."""
    login = quote(info["user"], safe="")
    if "password" in info:
        login += ":" + quote(info["password"], safe="")
    host = info["host"]
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = ":" + info["port"] if "port" in info else ""
    options = {key: value for key, value in info.items() if key not in {"user", "password", "host", "port", "dbname"}}
    return urlunsplit(
        ("postgresql", f"{login}@{host}{port}", "/" + quote(info["dbname"], safe=""), urlencode(options), "")
    )


class ProvisioningUriTests(unittest.TestCase):
    def test_optional_password_and_tls_options_survive_conversion_and_role_replacement(self):
        script = load_runtime_script()
        for password in (None, "", "synthetic@password:with/slash"):
            for host in ("127.0.0.1", "::1"):
                with self.subTest(password=password, host=host):
                    info = {
                        "user": "synthetic_admin",
                        "host": host,
                        "port": "5432",
                        "dbname": "synthetic_test",
                        "sslmode": "verify-full",
                        "sslrootcert": "C:/synthetic/ca.pem",
                    }
                    if password is not None:
                        info["password"] = password
                    uri = provisioning_uri(info)
                    # libpq normalizes an empty URI password to an omitted password.
                    self.assertEqual(conninfo_to_dict(uri), {key: value for key, value in info.items() if value != ""})
                    runtime = conninfo_to_dict(script.runtime_url(uri, "synthetic_runtime", "new@test"))
                    self.assertEqual(runtime, {**info, "user": "synthetic_runtime", "password": "new@test"})


class RuntimePermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.admin_url = database_url()
        cls.database = conninfo_to_dict(cls.admin_url)["dbname"]
        if os.environ.get("JOBFINDER_TEST_MODE") != "1" or not cls.database.endswith("_test"):
            raise RuntimeError("Rechtetests benötigen eine eigene _test-Datenbank.")
        suffix = uuid4().hex
        cls.roles = {key: f"f09_{key}_{suffix}" for key in permissions.RUNTIME_ROLES}
        cls.worker_group, cls.review_group = f"f09_worker_access_{suffix}", f"f09_review_access_{suffix}"
        cls.all_roles = (*cls.roles.values(), cls.worker_group, cls.review_group)
        cls.urls = {key: make_conninfo(cls.admin_url, user=role, password=suffix) for key, role in cls.roles.items()}
        cls.addClassCleanup(cls.cleanup)
        with psycopg.connect(cls.admin_url) as connection:
            cls.admin_role = connection.execute("SELECT current_user").fetchone()[0]
            cls.set_policy(connection, cls.review_group)
            for role in cls.roles.values():
                permissions.ensure_login_role(connection, role, suffix)
            cls.grant(connection)

    @classmethod
    def set_policy(cls, connection, group):
        condition = dataset_condition(group)
        connection.execute(
            sql.SQL("ALTER POLICY runtime_datasets ON datasets USING ({}) WITH CHECK ({})").format(condition, condition)
        )

    @classmethod
    def grant(cls, connection):
        permissions.apply_runtime_grants(
            connection,
            cls.database,
            cls.admin_role,
            roles=cls.roles,
            worker_group=cls.worker_group,
            review_group=cls.review_group,
        )

    @classmethod
    def cleanup(cls):
        with psycopg.connect(cls.admin_url) as connection:
            cls.set_policy(connection, permissions.REVIEW_GROUP)
            for role in cls.all_roles:
                if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def execute_as(self, component, statement, parameters=None):
        with psycopg.connect(self.urls[component]) as connection:
            result = connection.execute(statement, parameters)
            rows = result.fetchall() if result.description else result.rowcount
            connection.rollback()
            return rows

    def test_each_identity_has_exact_table_capabilities(self):
        for component in self.roles:
            writable = permissions.REVIEW_WRITE_TABLES if component == "review" else permissions.WORKER_TABLES
            readable = (*writable, *permissions.REVIEW_READ_TABLES) if component == "review" else writable
            for table in (*permissions.WORKER_TABLES, "schema_version", "alembic_version"):
                statements = (
                    (f"SELECT * FROM {table} WHERE false", table in readable),
                    (f"INSERT INTO {table} SELECT * FROM {table} WHERE false", table in writable),
                    (f"DELETE FROM {table} WHERE false", table in writable),
                )
                for statement, allowed in statements:
                    with self.subTest(component=component, statement=statement):
                        if allowed:
                            self.execute_as(component, statement)
                        else:
                            with self.assertRaises(errors.InsufficientPrivilege):
                                self.execute_as(component, statement)

    def test_review_can_save_state_history_documents_and_manual_imports(self):
        with patch.dict(os.environ, {"JOBFINDER_DATABASE_URL": self.urls["review"]}), transaction() as connection:
            before = store.read_memory(connection, "f09-test")
            after = {
                "test:role": {
                    "workflow_status": "applied",
                    "note": "kept",
                    "workflow_history": [{"status": "applied", "occurred_on": "2026-01-01"}],
                    "application_documents": [{"stored_name": "test.pdf"}],
                }
            }
            store.write_memory(connection, "f09-test", before, after)
            self.assertEqual(store.read_memory(connection, "f09-test"), after)
            store.write_dataset("internal/jobs.json", [{"id": "test:role", "title": "Example"}])
            store.write_dataset("output/recommendations.json", {"recommendations": [{"id": "test:role"}]})
            store.write_dataset(
                "internal/manual_jobs_cache.json", {"jobs": {"https://example.test": {"title": "Example"}}}
            )
            self.assertEqual(store.read_dataset("internal/jobs.json")[0]["id"], "test:role")
            self.assertIn("https://example.test", store.read_dataset("internal/manual_jobs_cache.json")["jobs"])
            connection.rollback()

    def test_both_workers_publish_cache_outbox_and_costs_without_network_calls(self):
        for component in ("worker", "hybrid"):
            with (
                self.subTest(component=component),
                patch.dict(os.environ, {"JOBFINDER_DATABASE_URL": self.urls[component]}),
                transaction() as connection,
            ):
                store.write_dataset(
                    "internal/notifications.json", {"pending": {"test:role": {"job_id": "test:role"}}, "sent": {}}
                )
                store.write_dataset("internal/source_cache.json", {"jobs": {"test:role": {"title": "Example"}}})
                self.assertIn("test:role", store.read_dataset("internal/notifications.json")["pending"])
                self.assertIn("test:role", store.read_dataset("internal/source_cache.json")["jobs"])
                connection.execute(
                    "INSERT INTO agent_usage(job_id,model,input_tokens,cached_input_tokens,output_tokens,reasoning_tokens,cost_eur) "
                    "VALUES ('test:role','mock',1,0,1,0,0.001)"
                )
                connection.rollback()

    def test_actual_review_flow_runs_with_review_role(self):
        url = "https://example.test/jobs/f09"
        job = Job(
            id="manual:f09-role",
            title="Junior Python Entwickler",
            company="Example GmbH",
            locations=["Fulda"],
            sources=[JobSource("manual", url)],
            description_raw="Python Entwicklung " * 20,
            description_clean="Python Entwicklung " * 20,
        )
        with (
            patch.dict(os.environ, {"JOBFINDER_DATABASE_URL": self.urls["review"]}),
            transaction() as connection,
            patch("job_finder.workflow.manual_import.manual.validate_public_url", return_value=url),
            patch("job_finder.workflow.manual_import.manual.fetch_text_with_final_url", return_value=(url, "mock")),
            patch("job_finder.workflow.manual_import.manual.job_from_page", return_value=job),
        ):
            # Other adapter tests deliberately leave incomplete default snapshots.
            # Replace them only inside this transaction; rollback restores their rows.
            connection.execute("DELETE FROM job_state WHERE scope='default'")
            store.write_dataset("internal/jobs.json", [])
            store.write_dataset("output/recommendations.json", {"recommendations": []})
            store.write_dataset("internal/manual_jobs_cache.json", {"jobs": {}})
            result = import_manual_url(url)
            self.assertEqual(result["job_id"], job.id)
            update_review_decision(job.id, "interesting")
            update_review_note(job.id, "Example note")
            card = next(card for card in attach_fact_sheets(load_review_jobs()) if card["id"] == job.id)
            self.assertEqual((card["workflow_status"], card["review_note"]), ("interesting", "Example note"))
            self.assertEqual(start_application(job.id)["workflow_status"], "applied")
            self.assertEqual(len(spent_today_and_this_month()), 2)
            connection.rollback()

    def test_review_cannot_bypass_child_denials_through_dataset_headers(self):
        protected = "f09-protected/notifications.json"
        with psycopg.connect(self.admin_url) as admin:
            admin.execute("INSERT INTO datasets(name,metadata) VALUES (%s,'{}')", (protected,))
            admin.execute(
                "INSERT INTO notifications(dataset,notification_key,delivery_state,present,extra) VALUES (%s,'probe','pending','{}','{}')",
                (protected,),
            )
        try:
            self.assertEqual(self.execute_as("review", "DELETE FROM datasets WHERE name=%s", (protected,)), 0)
            self.assertEqual(
                self.execute_as("review", "UPDATE datasets SET name='internal/jobs.json' WHERE name=%s", (protected,)),
                0,
            )
            with self.assertRaises(errors.InsufficientPrivilege):
                self.execute_as(
                    "review",
                    "INSERT INTO datasets(name,metadata) VALUES (%s,'{}') ON CONFLICT(name) DO UPDATE SET metadata='{}'",
                    (protected,),
                )
            for membership in (self.roles["review"], self.review_group):
                with psycopg.connect(self.urls["review"]) as connection:
                    connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(membership)))
                    self.assertEqual(connection.execute("DELETE FROM datasets WHERE name=%s", (protected,)).rowcount, 0)
            with psycopg.connect(self.admin_url) as admin:
                self.assertEqual(
                    admin.execute("SELECT count(*) FROM notifications WHERE dataset=%s", (protected,)).fetchone()[0], 1
                )
        finally:
            with psycopg.connect(self.admin_url) as admin:
                admin.execute("DELETE FROM datasets WHERE name=%s", (protected,))

    def test_runtime_roles_cannot_change_schema_roles_or_set_worker_role(self):
        for component in self.roles:
            for statement in (
                "CREATE TABLE forbidden_runtime(id int)",
                "ALTER TABLE job_state ADD COLUMN forbidden_runtime int",
                "TRUNCATE datasets CASCADE",
                "ALTER TABLE datasets DISABLE ROW LEVEL SECURITY",
                "DROP POLICY runtime_datasets ON datasets",
                "CREATE ROLE forbidden_runtime",
            ):
                with (
                    self.subTest(component=component, statement=statement),
                    self.assertRaises(errors.InsufficientPrivilege),
                ):
                    self.execute_as(component, statement)
        with self.assertRaises(errors.InsufficientPrivilege):
            self.execute_as("review", sql.SQL("SET ROLE {}").format(sql.Identifier(self.worker_group)))

    def test_rerunning_grants_preserves_passwords_and_future_tables_stay_private(self):
        with psycopg.connect(self.admin_url) as admin:
            for role in self.roles.values():
                permissions.ensure_login_role(admin, role, "this-must-not-replace-the-password")
            self.grant(admin)
            admin.execute("CREATE TABLE f09_future(id int)")
        try:
            for component in self.roles:
                with self.subTest(component=component), self.assertRaises(errors.InsufficientPrivilege):
                    self.execute_as(component, "SELECT * FROM f09_future")
        finally:
            with psycopg.connect(self.admin_url) as admin:
                admin.execute("DROP TABLE f09_future")

    def test_grants_refuse_missing_policy_and_inherited_public_access(self):
        with psycopg.connect(self.admin_url) as admin:
            admin.execute("DROP POLICY runtime_datasets ON datasets")
            with self.assertRaisesRegex(RuntimeError, "Datensatzgrenze"), admin.transaction():
                self.grant(admin)
            admin.rollback()
        with psycopg.connect(self.admin_url) as admin:
            admin.execute("GRANT SELECT ON notifications TO PUBLIC")
            with self.assertRaisesRegex(RuntimeError, "Matrix"), admin.transaction():
                self.grant(admin)
            admin.rollback()

    def test_role_preparation_is_readonly_by_default_and_does_not_activate(self):
        script = load_runtime_script()
        target = {
            "connect_kwargs": conninfo_to_dict(self.admin_url),
            "database": self.database,
            "admin_role": self.admin_role,
            "admin_url": "postgresql://unused@unused.test/unused",
        }
        with (
            tempfile.TemporaryDirectory() as directory,
            patch.multiple(
                script, RUNTIME_ROLES=self.roles, WORKER_GROUP=self.worker_group, REVIEW_GROUP=self.review_group
            ),
        ):
            path = Path(directory) / ".env.runtime-local"
            with patch.object(script, "write_credentials", side_effect=AssertionError("inspection wrote credentials")):
                self.assertEqual(script.prepare(target, path)["mode"], "inspect")
            self.assertFalse(path.exists())
            with (
                self.assertRaisesRegex(RuntimeError, "Passwort"),
                patch.object(
                    script, "write_credentials", side_effect=AssertionError("missing credentials wrote a file")
                ),
            ):
                script.prepare(target, path, apply=True)
            # Existing credentials are supplied internally and never printed.
            target["admin_url"] = self.admin_url
            script.write_credentials(path, {script.credential_key(key): url for key, url in self.urls.items()})
            result = script.prepare(target, path, apply=True)
            self.assertFalse(result["activated"])
            values = script.dotenv_values(path)
            self.assertEqual((values[script.READY], values[script.PHASE]), ("1", "legacy"))
            script.prepare(target, path, apply=True)
            values[script.PHASE] = "split"
            script.write_credentials(path, values)
            with self.assertRaisesRegex(RuntimeError, "Rückschaltung"):
                script.prepare(target, path, apply=True)
            sample = (
                "postgresql://admin:old@db.example.test:5432/jobfinder?sslmode=verify-full&sslrootcert=C%3A%2Fca.pem"
            )
            converted = conninfo_to_dict(script.runtime_url(sample, "runtime", "test@password"))
            self.assertEqual(
                (converted["user"], converted["password"], converted["sslmode"], converted["sslrootcert"]),
                ("runtime", "test@password", "verify-full", "C:/ca.pem"),
            )

    def test_provisioner_creates_new_credentials_and_refuses_file_failure_before_ddl(self):
        script = load_runtime_script()
        roles = {key: f"f09_new_{key}_{uuid4().hex}" for key in self.roles}
        info = conninfo_to_dict(self.admin_url)
        admin_url = provisioning_uri(info)
        target = {
            "connect_kwargs": info,
            "database": self.database,
            "admin_role": self.admin_role,
            "admin_url": admin_url,
        }
        try:
            with (
                tempfile.TemporaryDirectory() as directory,
                patch.multiple(
                    script, RUNTIME_ROLES=roles, WORKER_GROUP=self.worker_group, REVIEW_GROUP=self.review_group
                ),
            ):
                path = Path(directory) / ".env.runtime-local"
                with (
                    patch.object(script, "write_credentials", side_effect=OSError("read-only file")),
                    self.assertRaises(OSError),
                ):
                    script.prepare(target, path, apply=True)
                with psycopg.connect(self.admin_url) as admin:
                    self.assertEqual(
                        admin.execute(
                            "SELECT count(*) FROM pg_roles WHERE rolname=ANY(%s)", (list(roles.values()),)
                        ).fetchone()[0],
                        0,
                    )
                script.prepare(target, path, apply=True)
                values = script.dotenv_values(path)
                self.assertEqual((values[script.READY], values[script.PHASE]), ("1", "legacy"))
                original = path.read_text(encoding="utf-8")
                script.prepare(target, path, apply=True)
                self.assertEqual(path.read_text(encoding="utf-8"), original)
        finally:
            with psycopg.connect(self.admin_url) as admin:
                for role in roles.values():
                    if admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                        admin.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                        admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def test_existing_unsafe_roles_are_refused_without_repair(self):
        for statement in (
            sql.SQL("ALTER ROLE {} BYPASSRLS").format(sql.Identifier(self.roles["review"])),
            sql.SQL("GRANT {} TO {} WITH ADMIN OPTION").format(
                sql.Identifier(self.review_group), sql.Identifier(self.roles["review"])
            ),
            sql.SQL("ALTER TABLE notifications OWNER TO {}").format(sql.Identifier(self.roles["review"])),
        ):
            with self.subTest(statement=statement.as_string(None)), psycopg.connect(self.admin_url) as admin:
                admin.execute(statement)
                with self.assertRaises(RuntimeError), admin.transaction():
                    self.grant(admin)
                admin.rollback()
