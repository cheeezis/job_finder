"""TLS preparation of the Azure database scripts, without Terraform or network."""

import importlib.util
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

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
