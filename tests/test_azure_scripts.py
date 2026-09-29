"""TLS preparation of the Azure database scripts, without Terraform or network."""

import importlib.util
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "azure_postgres.py"
OUTPUTS = {
    "postgres_host": {"value": "psql-example.postgres.database.azure.com"},
    "postgres_database": {"value": "jobfinder"},
}


def load_azure_postgres():
    spec = importlib.util.spec_from_file_location("azure_postgres", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class AzureAdminConnectionTests(unittest.TestCase):
    def connect(self, certificates):
        """Build the admin arguments with a fake Terraform output and OS trust store."""
        module = load_azure_postgres()
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        project = Path(directory.name)
        (project / "infrastructure").mkdir()
        tfvars = project / "infrastructure/postgres.auto.tfvars.json"
        tfvars.write_text(json.dumps({"postgres_admin_password": "secret"}), encoding="utf-8")
        context = SimpleNamespace(get_ca_certs=lambda binary_form: list(certificates))
        with (
            patch.object(module, "PROJECT", project),
            patch.object(
                module.subprocess, "run", return_value=SimpleNamespace(stdout=json.dumps(OUTPUTS))
            ),
            patch.object(module.ssl, "create_default_context", return_value=context),
        ):
            return module.admin_connection(), project

    def test_admin_connection_verifies_the_server_certificate(self):
        connection, project = self.connect([b"certificate"])

        bundle = project / "tmp/azure-postgres-trusted-roots.pem"
        self.assertEqual(connection["sslmode"], "verify-full")
        self.assertEqual(connection["sslrootcert"], str(bundle))
        self.assertTrue(
            bundle.read_text(encoding="ascii").startswith("-----BEGIN CERTIFICATE-----")
        )
        self.assertEqual(connection["host"], OUTPUTS["postgres_host"]["value"])

    def test_admin_connection_refuses_an_empty_trust_store(self):
        with self.assertRaisesRegex(RuntimeError, "CA-Zertifikate"):
            self.connect([])


class AppRoleSqlTests(unittest.TestCase):
    """The exact statements create_app_role.py sends, with a fake connection."""

    def statements(self, function_name, *args, row=("found",)):
        with patch.dict(sys.modules, {"azure_postgres": load_azure_postgres()}):
            spec = importlib.util.spec_from_file_location(
                "create_app_role", SCRIPT.with_name("create_app_role.py")
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
        connection = Mock()
        connection.execute.return_value.fetchone.return_value = row
        with patch.object(module.secrets, "token_hex", return_value="fixed"):
            result = getattr(module, function_name)(connection, *args)
        sent = [call.args[0] for call in connection.execute.call_args_list]
        return result, [s if isinstance(s, str) else s.as_string(None) for s in sent]

    def test_grants_cover_the_app_role_and_keep_schema_version_admin_only(self):
        _, statements = self.statements("_apply_grants", "jobfinder", "jobfinder_admin")

        self.assertEqual(
            statements,
            [
                'GRANT CONNECT ON DATABASE "jobfinder" TO "jobfinder_app"',
                'GRANT USAGE ON SCHEMA public TO "jobfinder_app"',
                'GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO "jobfinder_app"',
                "SELECT to_regclass('public.schema_version')",
                'REVOKE ALL ON schema_version FROM "jobfinder_app"',
                'ALTER DEFAULT PRIVILEGES FOR ROLE "jobfinder_admin" IN SCHEMA public '
                'GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO "jobfinder_app"',
            ],
        )

    def test_grants_skip_the_revoke_before_the_schema_exists(self):
        _, statements = self.statements(
            "_apply_grants", "jobfinder", "jobfinder_admin", row=(None,)
        )

        self.assertNotIn('REVOKE ALL ON schema_version FROM "jobfinder_app"', statements)
        self.assertEqual(len(statements), 5)

    def test_role_password_is_reset_or_the_role_created(self):
        for row, expected in (
            (("found",), "ALTER ROLE \"jobfinder_app\" PASSWORD 'fixed'"),
            (
                None,
                "CREATE ROLE \"jobfinder_app\" LOGIN PASSWORD 'fixed' "
                "NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION",
            ),
        ):
            with self.subTest(row=row):
                password, statements = self.statements("_ensure_role", row=row)
                self.assertEqual(password, "fixed")
                self.assertEqual(statements[1:], [expected])
