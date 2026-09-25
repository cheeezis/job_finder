"""Azure preparation of the database scripts, without Terraform, TLS or network."""

import importlib.util
import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from urllib.parse import quote

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
OUTPUTS = {
    "postgres_host": {"value": "psql-example.postgres.database.azure.com"},
    "postgres_database": {"value": "jobfinder"},
}
PASSWORD = "p@ss w/rd"


def load_script(name):
    spec = importlib.util.spec_from_file_location(f"script_{name}", SCRIPTS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    # A direct start puts scripts/ on sys.path, where the shared Azure helper lives.
    with patch.object(sys, "path", [*sys.path, str(SCRIPTS)]):
        spec.loader.exec_module(module)
    return module


class FakeConnection:
    def __init__(self, tls):
        self.tls = tls

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def execute(self, query):
        row = ("16.4",) if query.startswith("SHOW") else self.tls
        return SimpleNamespace(fetchone=lambda: row)


class AzureScriptTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.project = Path(self.temporary_directory.name)
        (self.project / "infrastructure").mkdir()
        self.tfvars = self.project / "infrastructure/postgres.auto.tfvars.json"
        self.tfvars.write_text(json.dumps({"postgres_admin_password": PASSWORD}), encoding="utf-8")
        self.bundle = self.project / "tmp/azure-postgres-trusted-roots.pem"

    def prepared(self, module, *, certificates=(b"certificate",), terraform=None):
        """Patch project root, Terraform call and OS trust store of the shared helper."""
        shared = module.azure_postgres
        run = patch.object(
            shared.subprocess,
            "run",
            side_effect=terraform
            or (
                lambda args, **kwargs: subprocess.CompletedProcess(args, 0, json.dumps(OUTPUTS), "")
            ),
        )
        context = SimpleNamespace(get_ca_certs=lambda binary_form: list(certificates))
        return (
            patch.object(shared, "PROJECT", self.project),
            run,
            patch.object(shared.ssl, "create_default_context", return_value=context),
        )

    def test_role_script_builds_verified_admin_and_app_targets(self):
        module = load_script("create_app_role")
        project, run, ssl_context = self.prepared(module)
        with project, run as terraform, ssl_context, patch.object(module, "PROJECT", self.project):
            target = module._azure_target()

        terraform.assert_called_once_with(
            ["terraform", "-chdir=infrastructure", "output", "-json"],
            cwd=self.project,
            check=True,
            capture_output=True,
            text=True,
        )
        host = OUTPUTS["postgres_host"]["value"]
        suffix = f"?sslmode=verify-full&sslrootcert={quote(str(self.bundle))}"
        self.assertEqual(
            target["connect_kwargs"],
            {
                "host": host,
                "dbname": "jobfinder",
                "user": "jobfinder_admin",
                "password": PASSWORD,
                "sslmode": "verify-full",
                "sslrootcert": str(self.bundle),
                "connect_timeout": 20,
            },
        )
        self.assertEqual(target["database"], "jobfinder")
        self.assertEqual(target["admin_role"], "jobfinder_admin")
        self.assertEqual(
            target["admin_url"],
            f"postgresql://jobfinder_admin:{quote(PASSWORD)}@{host}:5432/jobfinder{suffix}",
        )
        self.assertEqual(
            target["app_url"]("a b"),
            f"postgresql://jobfinder_app:a%20b@{host}:5432/jobfinder{suffix}",
        )
        self.assertEqual(target["env_file"], self.project / ".env.postgres-azure")
        self.assertTrue(
            self.bundle.read_text(encoding="ascii").startswith("-----BEGIN CERTIFICATE-----")
        )

    def test_check_script_connects_read_only_and_reports_tls(self):
        module = load_script("check_azure_postgres")
        project, run, ssl_context = self.prepared(module)
        output = io.StringIO()
        with (
            project,
            run,
            ssl_context,
            patch.object(
                module.psycopg, "connect", return_value=FakeConnection((True, "TLSv1.3"))
            ) as connect,
            redirect_stdout(output),
        ):
            module.main()

        self.assertEqual(
            connect.call_args.kwargs,
            {
                "host": OUTPUTS["postgres_host"]["value"],
                "dbname": "jobfinder",
                "user": "jobfinder_admin",
                "password": PASSWORD,
                "sslmode": "verify-full",
                "sslrootcert": str(self.bundle),
                "connect_timeout": 20,
                "options": "-c default_transaction_read_only=on -c statement_timeout=15000",
            },
        )
        self.assertEqual(
            output.getvalue(), "Azure PostgreSQL 16.4: erreichbar; TLSv1.3, sslmode=verify-full.\n"
        )

        with (
            project,
            run,
            ssl_context,
            patch.object(module.psycopg, "connect", return_value=FakeConnection((False, None))),
            self.assertRaisesRegex(RuntimeError, "kein TLS"),
        ):
            module.main()

    def test_preparation_errors_propagate_unchanged(self):
        def terraform_fails(args, **kwargs):
            raise subprocess.CalledProcessError(1, args)

        for name, run_script in (
            ("create_app_role", lambda module: module._azure_target()),
            ("check_azure_postgres", lambda module: module.main()),
        ):
            module = load_script(name)
            cases = [
                ("terraform", {"terraform": terraform_fails}, None, subprocess.CalledProcessError),
                ("trust store", {"certificates": ()}, None, RuntimeError),
                ("broken tfvars", {}, "{", json.JSONDecodeError),
                ("missing tfvars", {}, "", FileNotFoundError),
            ]
            for label, options, tfvars, error in cases:
                with self.subTest(script=name, case=label):
                    if tfvars == "":
                        self.tfvars.unlink(missing_ok=True)
                    elif tfvars is not None:
                        self.tfvars.write_text(tfvars, encoding="utf-8")
                    project, run, ssl_context = self.prepared(module, **options)
                    with project, run, ssl_context, self.assertRaises(error):
                        run_script(module)
                    self.tfvars.write_text(
                        json.dumps({"postgres_admin_password": PASSWORD}), encoding="utf-8"
                    )

    def test_scripts_import_without_side_effects_and_role_script_starts_directly(self):
        with patch("subprocess.run") as run:
            load_script("azure_postgres")
            for name in ("check_azure_postgres", "create_app_role"):
                self.assertTrue(callable(load_script(name).main))
        run.assert_not_called()

        result = subprocess.run(
            [sys.executable, str(SCRIPTS / "create_app_role.py"), "--help"],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("--azure", result.stdout)
