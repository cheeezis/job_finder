"""Authentication refresh and fail-closed checks with no Azure or model calls."""

import os
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import psycopg

from job_finder.persistence import database, database_auth

URL = "postgresql://jobfinder_worker_entra@db.example.test/jobfinder?sslmode=verify-full&sslrootcert=/roots.pem"


class DatabaseAuthTests(unittest.TestCase):
    def setUp(self):
        self.env = patch.dict(os.environ, {}, clear=True)
        self.env.start()
        self.addCleanup(self.env.stop)
        database_auth._credential.cache_clear()
        self.addCleanup(database_auth._credential.cache_clear)

    def test_password_default_preserves_existing_connection(self):
        with (
            patch.object(database_auth.psycopg, "connect") as connect,
            patch.object(database_auth, "_credential") as credential,
        ):
            database_auth.connect_runtime("postgresql://app:old-password@localhost/db", autocommit=True)
        connect.assert_called_once_with(
            "postgresql://app:old-password@localhost/db", autocommit=True, connect_timeout=10
        )
        credential.assert_not_called()

    def test_new_connections_request_current_token_and_pin_managed_identity(self):
        os.environ.update(JOBFINDER_DATABASE_AUTH="managed_identity", JOBFINDER_MANAGED_IDENTITY_CLIENT_ID="worker-id")
        provider = MagicMock()
        provider.get_token.side_effect = [
            SimpleNamespace(token="first-token"),
            SimpleNamespace(token="refreshed-token"),
        ]
        with (
            patch.object(database_auth, "ManagedIdentityCredential", return_value=provider) as constructor,
            patch.object(database_auth.psycopg, "connect") as connect,
        ):
            database_auth.connect_runtime(URL)
            database_auth.connect_runtime(URL, autocommit=True)
        constructor.assert_called_once_with(client_id="worker-id")
        self.assertEqual(provider.get_token.call_count, 2)
        provider.get_token.assert_called_with(database_auth.POSTGRES_SCOPE)
        self.assertEqual(
            [call.kwargs["password"] for call in connect.call_args_list], ["first-token", "refreshed-token"]
        )
        self.assertEqual(connect.call_args.kwargs["sslmode"], "verify-full")
        self.assertEqual(connect.call_args.kwargs["sslrootcert"], "/roots.pem")
        self.assertTrue(connect.call_args.kwargs["autocommit"])

    def test_hybrid_uses_only_explicit_service_principal(self):
        os.environ.update(
            JOBFINDER_DATABASE_AUTH="service_principal",
            AZURE_CLIENT_ID="hybrid-id",
            AZURE_TENANT_ID="tenant-id",
            AZURE_CLIENT_SECRET="test-secret",
        )
        provider = MagicMock()
        provider.get_token.return_value.token = "hybrid-token"
        with (
            patch.object(database_auth, "ClientSecretCredential", return_value=provider) as constructor,
            patch.object(database_auth.psycopg, "connect") as connect,
        ):
            database_auth.connect_runtime(URL)
        constructor.assert_called_once_with(tenant_id="tenant-id", client_id="hybrid-id", client_secret="test-secret")
        self.assertEqual(connect.call_args.kwargs["password"], "hybrid-token")

    def test_unknown_mode_missing_identity_and_missing_tls_refuse_connection(self):
        cases = [
            ({"JOBFINDER_DATABASE_AUTH": "typo"}, URL),
            ({"JOBFINDER_DATABASE_AUTH": "managed_identity"}, URL),
            ({"JOBFINDER_DATABASE_AUTH": "service_principal", "AZURE_CLIENT_ID": "hybrid-id"}, URL),
            (
                {"JOBFINDER_DATABASE_AUTH": "managed_identity", "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "worker-id"},
                URL.replace("verify-full", "require"),
            ),
            (
                {"JOBFINDER_DATABASE_AUTH": "managed_identity", "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "worker-id"},
                URL.replace("worker_entra@", "worker_entra:old-password@"),
            ),
            (
                {"JOBFINDER_DATABASE_AUTH": "managed_identity", "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "worker-id"},
                "user=worker sslmode=verify-full",
            ),
            (
                {
                    "JOBFINDER_DATABASE_AUTH": "managed_identity",
                    "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "worker-id",
                    "PGSERVICE": "developer-admin",
                },
                URL,
            ),
            (
                {"JOBFINDER_DATABASE_AUTH": "managed_identity", "JOBFINDER_MANAGED_IDENTITY_CLIENT_ID": "worker-id"},
                URL + "&passfile=/another-login",
            ),
        ]
        for env, url in cases:
            with (
                self.subTest(env=env),
                patch.dict(os.environ, env, clear=True),
                patch.object(database_auth.psycopg, "connect") as connect,
                patch.object(database_auth, "_credential") as credential,
            ):
                with self.assertRaises(RuntimeError):
                    database_auth.connect_runtime(url)
                connect.assert_not_called()
                credential.assert_not_called()

    def test_token_failure_is_redacted_and_never_uses_password_fallback(self):
        os.environ.update(
            JOBFINDER_DATABASE_AUTH="managed_identity",
            JOBFINDER_MANAGED_IDENTITY_CLIENT_ID="worker-id",
            PGPASSWORD="old-password",
        )
        with (
            patch.object(database_auth, "_credential", side_effect=RuntimeError("private-token")),
            patch.object(database_auth.psycopg, "connect") as connect,
            self.assertRaises(RuntimeError) as error,
        ):
            database_auth.connect_runtime(URL)
        self.assertNotIn("private-token", str(error.exception))
        self.assertIsNone(error.exception.__cause__)
        self.assertTrue(error.exception.__suppress_context__)
        connect.assert_not_called()

    def test_database_failure_is_redacted_and_never_retries(self):
        os.environ.update(JOBFINDER_DATABASE_AUTH="managed_identity", JOBFINDER_MANAGED_IDENTITY_CLIENT_ID="worker-id")
        provider = MagicMock()
        provider.get_token.return_value.token = "private-token"
        with (
            patch.object(database_auth, "_credential", return_value=provider),
            patch.object(
                database_auth.psycopg, "connect", side_effect=psycopg.OperationalError("private-token")
            ) as connect,
            self.assertRaises(RuntimeError) as error,
        ):
            database_auth.connect_runtime(URL)
        self.assertNotIn("private-token", str(error.exception))
        connect.assert_called_once()

    def test_nested_transactions_keep_one_connection_and_admin_stays_explicit(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        with (
            patch.object(database, "database_url", return_value=URL),
            patch.object(database, "connect_runtime", return_value=connection) as connect,
            database.transaction() as outer,
            database.transaction() as inner,
        ):
            self.assertIs(outer, inner)
        connect.assert_called_once_with(URL)
        os.environ["JOBFINDER_DATABASE_AUTH"] = "managed_identity"
        with (
            patch.object(database, "admin_database_url", return_value="admin-url"),
            patch.object(database.psycopg, "connect", return_value=connection) as admin,
            patch.object(database, "connect_runtime") as runtime,
            database.transaction(admin=True),
        ):
            pass
        admin.assert_called_once_with("admin-url", connect_timeout=10)
        runtime.assert_not_called()

    def test_worker_session_lock_uses_refresh_capable_connection(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = (True,)
        with (
            patch.object(database, "database_url", return_value=URL),
            patch.object(database, "connect_runtime", return_value=connection) as connect,
            database.worker_lock(),
        ):
            pass
        connect.assert_called_once_with(URL, autocommit=True)
        self.assertIn("pg_advisory_unlock", connection.execute.call_args.args[0])
