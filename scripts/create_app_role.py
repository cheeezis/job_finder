"""Create or refresh the least-privilege jobfinder_app role for worker and review.

Grants only SELECT/INSERT/UPDATE/DELETE on application tables, never on
schema_version (which stays admin-only; only initialize() touches it).
Safe to rerun: an already-configured target is left untouched except for
reapplying the grants, which are themselves idempotent. Passwords are never
printed.
"""

import argparse
import json
import secrets
import ssl
import subprocess
from pathlib import Path
from urllib.parse import quote

import psycopg
from dotenv import dotenv_values
from psycopg import sql

PROJECT = Path(__file__).resolve().parents[1]
APP_ROLE = "jobfinder_app"


def _local_target():
    """Admin connection details for the local Docker Postgres container."""
    env = dotenv_values(PROJECT / ".env.postgres")
    password = env.get("POSTGRES_PASSWORD")
    if not password:
        raise RuntimeError(
            ".env.postgres fehlt oder enthält kein POSTGRES_PASSWORD. "
            "Erst scripts/setup_postgres.py und docker compose up ausführen."
        )
    port = env.get("POSTGRES_PORT", "55432")
    return {
        "connect_kwargs": dict(
            host="127.0.0.1",
            port=port,
            dbname="jobfinder",
            user="jobfinder",
            password=password,
            connect_timeout=10,
        ),
        "database": "jobfinder",
        "admin_role": "jobfinder",
        "admin_url": f"postgresql://jobfinder:{quote(password)}@127.0.0.1:{port}/jobfinder",
        "app_url": lambda app_password: (
            f"postgresql://{APP_ROLE}:{quote(app_password)}@127.0.0.1:{port}/jobfinder"
        ),
        "env_file": PROJECT / ".env.postgres",
    }


def _azure_target():
    """Admin connection details for the Terraform-provisioned Azure server."""
    result = subprocess.run(
        ["terraform", "-chdir=infrastructure", "output", "-json"],
        cwd=PROJECT,
        check=True,
        capture_output=True,
        text=True,
    )
    outputs = json.loads(result.stdout)
    settings = json.loads(
        (PROJECT / "infrastructure/postgres.auto.tfvars.json").read_text(encoding="utf-8")
    )
    host = outputs["postgres_host"]["value"]
    database = outputs["postgres_database"]["value"]
    admin_password = settings["postgres_admin_password"]

    # libpq needs a PEM bundle; export the OS trust store, same as check_azure_postgres.py.
    certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not certificates:
        raise RuntimeError("Keine vertrauenswürdigen CA-Zertifikate verfügbar.")
    bundle = PROJECT / "tmp/azure-postgres-trusted-roots.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates), encoding="ascii"
    )
    tls_suffix = f"?sslmode=verify-full&sslrootcert={quote(str(bundle))}"
    return {
        "connect_kwargs": dict(
            host=host,
            dbname=database,
            user="jobfinder_admin",
            password=admin_password,
            sslmode="verify-full",
            sslrootcert=str(bundle),
            connect_timeout=20,
        ),
        "database": database,
        "admin_role": "jobfinder_admin",
        "admin_url": (
            f"postgresql://jobfinder_admin:{quote(admin_password)}@{host}:5432/"
            f"{database}{tls_suffix}"
        ),
        "app_url": lambda app_password: (
            f"postgresql://{APP_ROLE}:{quote(app_password)}@{host}:5432/{database}{tls_suffix}"
        ),
        "env_file": PROJECT / ".env.postgres-azure",
    }


def _read_env_value(env_file, key):
    if not env_file.exists():
        return None
    for line in env_file.read_text(encoding="utf-8").splitlines():
        if line.startswith(f"{key}="):
            return line[len(key) + 1 :]
    return None


def _write_env_value(env_file, key, value):
    lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    for index, line in enumerate(lines):
        if line.startswith(f"{key}="):
            lines[index] = f"{key}={value}"
            break
    else:
        lines.append(f"{key}={value}")
    env_file.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _ensure_role(connection):
    """Create jobfinder_app if missing; reset its password if we're about to lose track of it."""
    exists = connection.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (APP_ROLE,)).fetchone()
    # CREATE/ALTER ROLE take PASSWORD as a literal, not a bind parameter;
    # sql.Literal still escapes it safely, just at SQL-composition time.
    password = secrets.token_hex(24)
    if exists:
        connection.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(APP_ROLE), sql.Literal(password)
            )
        )
    else:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION"
            ).format(sql.Identifier(APP_ROLE), sql.Literal(password))
        )
    return password


def _apply_grants(connection, database, admin_role):
    """Idempotent; safe to reapply on every run, including for future tables."""
    connection.execute(
        sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(
            sql.Identifier(database), sql.Identifier(APP_ROLE)
        )
    )
    connection.execute(
        sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(sql.Identifier(APP_ROLE))
    )
    connection.execute(
        sql.SQL("GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO {}").format(
            sql.Identifier(APP_ROLE)
        )
    )
    # Schema may not be initialized yet (job_finder.db init runs independently
    # of this script); skip the revoke rather than fail on a missing table.
    schema_version_exists = connection.execute(
        "SELECT to_regclass('public.schema_version')"
    ).fetchone()[0]
    if schema_version_exists:
        connection.execute(
            sql.SQL("REVOKE ALL ON schema_version FROM {}").format(sql.Identifier(APP_ROLE))
        )
    connection.execute(
        sql.SQL(
            "ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public "
            "GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO {}"
        ).format(sql.Identifier(admin_role), sql.Identifier(APP_ROLE))
    )


def main():
    """Create/refresh jobfinder_app and its env entries for the chosen target."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--azure", action="store_true", help="Gegen Azure statt die lokale Docker-DB ausführen."
    )
    args = parser.parse_args()
    target = _azure_target() if args.azure else _local_target()
    env_file = target["env_file"]

    current_app_url = _read_env_value(env_file, "JOBFINDER_DATABASE_URL") or ""
    already_configured = APP_ROLE in current_app_url

    with psycopg.connect(**target["connect_kwargs"], autocommit=True) as connection:
        if already_configured:
            _apply_grants(connection, target["database"], target["admin_role"])
        else:
            password = _ensure_role(connection)
            _apply_grants(connection, target["database"], target["admin_role"])

    _write_env_value(env_file, "JOBFINDER_ADMIN_DATABASE_URL", target["admin_url"])
    if not already_configured:
        _write_env_value(env_file, "JOBFINDER_DATABASE_URL", target["app_url"](password))
        print(f"{env_file.name}: {APP_ROLE} angelegt und Grants gesetzt.")
    else:
        print(f"{env_file.name}: {APP_ROLE} bereits konfiguriert; Grants erneut angewendet.")
    print("Zugangsdaten werden nicht ausgegeben.")


if __name__ == "__main__":
    main()
