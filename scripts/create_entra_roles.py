"""Inspect Entra roles; --apply adds only new identities and F09 group memberships.

    python scripts/create_entra_roles.py --principals-file tmp/entra-principals.json

Uses a separate Azure CLI Entra administrator in postgres and the existing password
administrator in the application database. Never activates a runtime or writes secrets.
"""

import argparse
import json
import os
from pathlib import Path

import psycopg
from azure.identity import AzureCliCredential
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

from job_finder.persistence.database_auth import POSTGRES_SCOPE, entra_parameters
from job_finder.persistence.entra_permissions import prepare, validate_principals

PROJECT = Path(__file__).resolve().parents[1]


def targets(admin_url, entra_url):
    """Refuse ambiguous or mismatched server targets before acquiring a token."""
    try:
        admin = conninfo_to_dict(admin_url)
    except psycopg.Error:
        raise RuntimeError("Ungültiger administrativer Datenbankzugang.") from None
    identity = entra_parameters(entra_url)
    if (
        identity["dbname"] != "postgres"
        or not all(admin.get(key) for key in ("host", "user", "dbname", "password"))
        or admin["dbname"] == "postgres"
        or admin.get("sslmode") != "verify-full"
        or any(admin.get(key) for key in ("service", "servicefile", "hostaddr"))
        or admin["host"] != identity["host"]
        or admin.get("port", "5432") != identity.get("port", "5432")
        or any(character in admin["host"] for character in (",", "/", "\\"))
    ):
        raise RuntimeError(
            "Einrichtungszugänge müssen zum selben expliziten Server und zu getrennten Datenbanken gehören."
        )
    return admin, identity


def configured_value(path, key):
    return os.environ.get(key) or dotenv_values(path, interpolate=False).get(key) or ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--principals-file", type=Path, required=True, help="Passwortfreier Terraform-Output entra_principals."
    )
    parser.add_argument("--admin-env-file", type=Path, default=PROJECT / ".env.postgres-azure")
    parser.add_argument("--entra-env-file", type=Path, default=PROJECT / ".env.entra-azure")
    parser.add_argument("--apply", action="store_true", help="Rollen anlegen und bestehende F09-Gruppen zuordnen.")
    args = parser.parse_args()
    try:
        principals = validate_principals(json.loads(args.principals_file.read_text(encoding="utf-8-sig")))
        admin, identity = targets(
            configured_value(args.admin_env_file, "JOBFINDER_ADMIN_DATABASE_URL"),
            configured_value(args.entra_env_file, "JOBFINDER_ENTRA_ADMIN_DATABASE_URL"),
        )
        credential = AzureCliCredential(tenant_id=principals["tenant_id"])
        try:
            token = credential.get_token(POSTGRES_SCOPE).token
        finally:
            credential.close()
        if not token:
            raise RuntimeError("Kein Entra-Token.")
        with (
            psycopg.connect(**{**admin, "connect_timeout": 10}, autocommit=True) as app_connection,
            psycopg.connect(
                **{**identity, "password": token, "connect_timeout": 10}, autocommit=True
            ) as identity_connection,
        ):
            result = prepare(identity_connection, app_connection, principals, admin["dbname"], apply=args.apply)
    except Exception:
        # DSNs, Azure errors and database diagnostics can carry credentials or identities.
        print("Entra-Einrichtung abgebrochen. Ziele, Anmeldung, Object-IDs, Rollen und F09-Rechte prüfen.")
        raise SystemExit(1) from None
    print(json.dumps(result))


if __name__ == "__main__":
    main()
