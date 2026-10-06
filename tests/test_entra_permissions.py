"""Emulate only Azure's identity API; exercise grants, RLS and rollback in real local PostgreSQL."""

import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
from uuid import uuid4

import psycopg
from psycopg import errors, sql
from psycopg.conninfo import conninfo_to_dict

from job_finder.persistence import entra_permissions as entra, runtime_permissions as permissions
from job_finder.persistence.database import database_url
from job_finder.persistence.migrations.runtime_boundaries import dataset_condition


class EntraConfigurationTests(unittest.TestCase):
    def test_principal_ids_must_be_complete_distinct_and_valid(self):
        good = {key: str(uuid4()) for key in ("tenant_id", *entra.ENTRA_ROLES)}
        self.assertEqual(entra.validate_principals(good), good)
        cases = [
            None,
            {},
            {**good, "worker": "client-name"},
            {**good, "hybrid": good["worker"]},
            {**good, "tenant_id": str(uuid4())[:8]},
            {**good, "review": "00000000-0000-0000-0000-000000000000"},
        ]
        for values in cases:
            with self.subTest(values=values), self.assertRaises(RuntimeError):
                entra.validate_principals(values)

    def test_pgaadauth_calls_are_parameterized_and_non_admin(self):
        connection = MagicMock()
        entra.create_principal(connection, "role", "oid")
        connection.execute.assert_called_once_with(
            "SELECT * FROM pg_catalog.pgaadauth_create_principal_with_oid(%s,%s,%s,%s,%s)",
            ("role", "oid", "service", False, False),
        )
        cursor = connection.execute.return_value
        cursor.description = [SimpleNamespace(name=key) for key in ("rolname", "objectId", "isAdmin")]
        cursor.fetchall.return_value = [("role", "oid", 0)]
        self.assertEqual(entra.list_principals(connection), [{"rolname": "role", "objectid": "oid", "isadmin": 0}])
        connection.execute.assert_called_with("SELECT * FROM pg_catalog.pgaadauth_list_principals(false)")

    def test_setup_connections_must_target_same_server_with_separate_databases(self):
        path = Path(__file__).resolve().parents[1] / "scripts/create_entra_roles.py"
        spec = importlib.util.spec_from_file_location("create_entra_roles_test", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        admin = "postgresql://admin:old@db.example.test/jobfinder?sslmode=verify-full"
        identity = "postgresql://owner@db.example.test/postgres?sslmode=verify-full"
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(script.targets(admin, identity)[1]["dbname"], "postgres")
            for url in (
                identity.replace("/postgres", "/jobfinder"),
                identity.replace("db.example", "other.example"),
                identity.replace("owner@", "owner:static-password@"),
                identity.replace("verify-full", "require"),
                identity.replace("db.example.test", "db.example.test:5444"),
            ):
                with self.subTest(url=url), self.assertRaises(RuntimeError):
                    script.targets(admin, url)

    def test_cli_keeps_admin_methods_separate_and_redacts_failures(self):
        path = Path(__file__).resolve().parents[1] / "scripts/create_entra_roles.py"
        spec = importlib.util.spec_from_file_location("create_entra_roles_cli_test", path)
        script = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(script)
        principals = {key: str(uuid4()) for key in ("tenant_id", *entra.ENTRA_ROLES)}
        admin = "postgresql://admin:private-password@db.test/jobfinder?sslmode=verify-full&connect_timeout=30"
        identity = "postgresql://owner@db.test/postgres?sslmode=verify-full&connect_timeout=30"
        with tempfile.TemporaryDirectory() as directory:
            file = Path(directory) / "principals.json"
            file.write_text(json.dumps(principals), encoding="utf-8")
            for mode in ("inspect", "apply", "token_error", "connection_error"):
                output = io.StringIO()
                provider = MagicMock()
                provider.get_token.return_value.token = "private-token"
                if mode == "token_error":
                    provider.get_token.side_effect = RuntimeError("private-token")
                with (
                    self.subTest(mode=mode),
                    patch.dict(os.environ, {}, clear=True),
                    patch.object(
                        sys,
                        "argv",
                        [str(path), "--principals-file", str(file), *(["--apply"] if mode == "apply" else [])],
                    ),
                    patch.object(
                        script,
                        "configured_value",
                        side_effect=lambda _path, key: admin if key == "JOBFINDER_ADMIN_DATABASE_URL" else identity,
                    ),
                    patch.object(script, "AzureCliCredential", return_value=provider) as credential,
                    patch.object(script.psycopg, "connect") as connect,
                    patch.object(script, "prepare", return_value={"activated": False}) as prepare,
                    redirect_stdout(output),
                ):
                    if mode == "connection_error":
                        connect.side_effect = psycopg.OperationalError("private-password private-token")
                    if mode.endswith("error"):
                        with self.assertRaises(SystemExit) as error:
                            script.main()
                        self.assertEqual(error.exception.code, 1)
                        prepare.assert_not_called()
                    else:
                        script.main()
                        self.assertEqual(connect.call_count, 2)
                        app, principal = (call.kwargs for call in connect.call_args_list)
                        self.assertEqual((app["dbname"], app["password"]), ("jobfinder", "private-password"))
                        self.assertEqual((principal["dbname"], principal["password"]), ("postgres", "private-token"))
                        self.assertEqual(app["connect_timeout"], 10)
                        self.assertEqual(principal["connect_timeout"], 10)
                        prepare.assert_called_once()
                        self.assertEqual(prepare.call_args.kwargs["apply"], mode == "apply")
                    credential.assert_called_once_with(tenant_id=principals["tenant_id"])
                    provider.close.assert_called_once()
                    self.assertNotIn("private-password", output.getvalue())
                    self.assertNotIn("private-token", output.getvalue())


class EntraPermissionsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.url = database_url()
        cls.database = conninfo_to_dict(cls.url)["dbname"]
        if os.environ.get("JOBFINDER_TEST_MODE") != "1" or not cls.database.endswith("_test"):
            raise RuntimeError("Entra-Rechtetests benötigen eine eigene _test-Datenbank.")
        suffix = uuid4().hex
        cls.legacy = {key: f"f10_old_{key}_{suffix}" for key in entra.ENTRA_ROLES}
        cls.worker_group, cls.review_group = f"f10_worker_access_{suffix}", f"f10_review_access_{suffix}"
        cls.mapping_table = f"f10_mapping_{suffix}"
        cls.addClassCleanup(cls.cleanup_class)
        with psycopg.connect(cls.url) as connection:
            cls.set_policy(connection, cls.review_group)
            for role in cls.legacy.values():
                permissions.ensure_login_role(connection, role, "local-test-password")
            permissions.apply_runtime_grants(
                connection,
                cls.database,
                connection.execute("SELECT current_user").fetchone()[0],
                roles=cls.legacy,
                worker_group=cls.worker_group,
                review_group=cls.review_group,
            )
            connection.execute(
                sql.SQL(
                    "CREATE TABLE {} (rolname text,objectid text,tenantid text,principaltype text,isadmin int,ismfa int)"
                ).format(sql.Identifier(cls.mapping_table))
            )

    @classmethod
    def set_policy(cls, connection, group):
        condition = dataset_condition(group)
        connection.execute(
            sql.SQL("ALTER POLICY runtime_datasets ON datasets USING ({}) WITH CHECK ({})").format(condition, condition)
        )

    @classmethod
    def cleanup_class(cls):
        with psycopg.connect(cls.url) as connection:
            cls.set_policy(connection, permissions.REVIEW_GROUP)
            connection.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(cls.mapping_table)))
            for role in (*cls.legacy.values(), cls.worker_group, cls.review_group):
                connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def setUp(self):
        suffix = uuid4().hex
        self.roles = {key: f"f10_new_{key}_{suffix}" for key in entra.ENTRA_ROLES}
        self.principals = {key: str(uuid4()) for key in ("tenant_id", *entra.ENTRA_ROLES)}
        self.addCleanup(self.cleanup_roles)
        self.enterContext(patch.object(entra, "list_principals", self.fake_list))
        self.enterContext(patch.object(entra, "create_principal", self.fake_create))

    def fake_list(self, connection):
        cursor = connection.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(self.mapping_table)))
        return [
            dict(zip((column.name for column in cursor.description), row, strict=True)) for row in cursor.fetchall()
        ]

    def fake_create(self, connection, role, object_id):
        connection.execute(sql.SQL("CREATE ROLE {} LOGIN INHERIT").format(sql.Identifier(role)))
        connection.execute(
            sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,'service',0,0)").format(sql.Identifier(self.mapping_table)),
            (role, object_id, self.principals["tenant_id"]),
        )

    def cleanup_roles(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("DELETE FROM {}").format(sql.Identifier(self.mapping_table)))
            self.set_policy(connection, self.review_group)
            connection.execute("REVOKE ALL ON source_cache FROM PUBLIC")
            for role in self.roles.values():
                if connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone():
                    connection.execute(sql.SQL("DROP OWNED BY {}").format(sql.Identifier(role)))
                    connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))

    def prepare(self, *, apply=False):
        with psycopg.connect(self.url, autocommit=True) as app, psycopg.connect(self.url, autocommit=True) as identity:
            return entra.prepare(
                identity,
                app,
                self.principals,
                self.database,
                apply=apply,
                roles=self.roles,
                legacy_roles=self.legacy,
                worker_group=self.worker_group,
                review_group=self.review_group,
            )

    def test_inspection_does_not_create_roles_then_apply_is_idempotent(self):
        before = self.prepare()
        self.assertFalse(any(before["roles_present"].values()))
        self.assertFalse(before["activated"])
        result = self.prepare(apply=True)
        self.assertTrue(all(result["grants_verified"].values()))
        self.assertFalse(result["activated"])
        self.assertEqual(result, self.prepare(apply=True))
        self.assertEqual(self.prepare()["grants_verified"], result["grants_verified"])
        with psycopg.connect(self.url) as connection:
            permissions.verify_runtime_grants(connection, self.database, roles=self.legacy)
            passwords = connection.execute(
                "SELECT rolpassword FROM pg_authid WHERE rolname=ANY(%s)", (list(self.legacy.values()),)
            ).fetchall()
            self.assertTrue(all(row[0] for row in passwords))
            self.assertEqual(
                connection.execute(
                    "SELECT rolpassword FROM pg_authid WHERE rolname=ANY(%s)", (list(self.roles.values()),)
                ).fetchall(),
                [(None,)] * 3,
            )

    def test_new_review_inherits_same_dataset_boundary_and_table_denials(self):
        self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            connection.execute("INSERT INTO datasets(name,metadata) VALUES ('f10-protected/source','{}')")
            connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.roles["review"])))
            self.assertEqual(
                connection.execute("SELECT name FROM datasets WHERE name='f10-protected/source'").fetchall(), []
            )
            self.assertEqual(connection.execute("DELETE FROM datasets WHERE name='f10-protected/source'").rowcount, 0)
            connection.execute(
                "INSERT INTO datasets(name,metadata) VALUES ('internal/jobs.json','{}') ON CONFLICT DO NOTHING"
            )
            self.assertEqual(
                connection.execute("SELECT name FROM datasets WHERE name='internal/jobs.json'").fetchone()[0],
                "internal/jobs.json",
            )
            connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.review_group)))
            self.assertEqual(connection.execute("DELETE FROM datasets WHERE name='f10-protected/source'").rowcount, 0)
            connection.rollback()
        for statement in (
            "SELECT * FROM notifications",
            "DELETE FROM agent_usage",
            "TRUNCATE jobs",
            "CREATE TABLE forbidden(id int)",
            "UPDATE alembic_version SET version_num=version_num",
        ):
            with self.subTest(statement=statement), psycopg.connect(self.url) as connection:
                connection.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self.roles["review"])))
                with self.assertRaises(errors.InsufficientPrivilege):
                    connection.execute(statement)
                connection.rollback()

    def test_wrong_mapping_or_admin_label_is_refused_without_changes(self):
        self.prepare(apply=True)
        cases = (("objectid", str(uuid4())), ("tenantid", str(uuid4())), ("principaltype", "user"), ("isadmin", 1))
        for column, value in cases:
            with self.subTest(column=column), psycopg.connect(self.url, autocommit=True) as connection:
                original = connection.execute(
                    sql.SQL("SELECT {} FROM {} WHERE rolname=%s").format(
                        sql.Identifier(column), sql.Identifier(self.mapping_table)
                    ),
                    (self.roles["worker"],),
                ).fetchone()[0]
                connection.execute(
                    sql.SQL("UPDATE {} SET {}=%s WHERE rolname=%s").format(
                        sql.Identifier(self.mapping_table), sql.Identifier(column)
                    ),
                    (value, self.roles["worker"]),
                )
                try:
                    with self.assertRaises(RuntimeError):
                        self.prepare(apply=True)
                finally:
                    connection.execute(
                        sql.SQL("UPDATE {} SET {}=%s WHERE rolname=%s").format(
                            sql.Identifier(self.mapping_table), sql.Identifier(column)
                        ),
                        (original, self.roles["worker"]),
                    )

    def test_existing_unmapped_role_or_other_alias_is_refused(self):
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("CREATE ROLE {} LOGIN").format(sql.Identifier(self.roles["worker"])))
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(self.roles["worker"])))
            connection.execute(
                sql.SQL("INSERT INTO {} VALUES (%s,%s,%s,'service',0,0)").format(sql.Identifier(self.mapping_table)),
                (self.legacy["worker"], self.principals["worker"], self.principals["tenant_id"]),
            )
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)

    def test_unknown_membership_is_refused_and_preserved(self):
        self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            connection.execute(
                sql.SQL("GRANT {} TO {}").format(
                    sql.Identifier(self.worker_group), sql.Identifier(self.roles["review"])
                )
            )
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)

    def test_elevated_role_attributes_are_refused_without_repair(self):
        self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            connection.execute(sql.SQL("ALTER ROLE {} CREATEDB").format(sql.Identifier(self.roles["worker"])))
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            self.assertTrue(
                connection.execute(
                    "SELECT rolcreatedb FROM pg_roles WHERE rolname=%s", (self.roles["worker"],)
                ).fetchone()[0]
            )

    def test_public_grants_or_missing_row_boundary_block_provisioning(self):
        with psycopg.connect(self.url) as connection:
            connection.execute("GRANT SELECT ON source_cache TO PUBLIC")
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            connection.execute("REVOKE SELECT ON source_cache FROM PUBLIC")
            connection.execute("ALTER POLICY runtime_datasets ON datasets USING (true)")
        with self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            self.assertFalse(
                connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(self.roles.values()),)
                ).fetchone()
            )

    def test_identity_transaction_failure_rolls_back_all_new_roles(self):
        calls = 0

        def fail_second(connection, role, object_id):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise RuntimeError("synthetic identity failure")
            self.fake_create(connection, role, object_id)

        with patch.object(entra, "create_principal", fail_second), self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        with psycopg.connect(self.url) as connection:
            self.assertFalse(
                connection.execute(
                    "SELECT 1 FROM pg_roles WHERE rolname=ANY(%s)", (list(self.roles.values()),)
                ).fetchone()
            )

    def test_grant_failure_leaves_unactivated_roles_and_rerun_resumes(self):
        original = permissions.verify_runtime_grants

        def fail_new(connection, database, *, roles):
            if any(role in self.roles.values() for role in roles.values()):
                raise RuntimeError("synthetic grant verification failure")
            original(connection, database, roles=roles)

        with patch.object(entra, "verify_runtime_grants", fail_new), self.assertRaises(RuntimeError):
            self.prepare(apply=True)
        result = self.prepare()
        self.assertTrue(all(result["roles_present"].values()))
        self.assertFalse(any(result["grants_verified"].values()))
        self.assertFalse(result["activated"])
        self.assertTrue(all(self.prepare(apply=True)["grants_verified"].values()))
