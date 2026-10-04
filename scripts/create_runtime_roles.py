"""Prepare separate DB credentials; inspection is read-only and --apply is explicit.

Does not migrate, rotate existing passwords, activate a runtime or write Key Vault.
Credentials remain in an ignored local file, outside Terraform and console output.
"""

import argparse
import os
import secrets
import sys
from pathlib import Path
from urllib.parse import quote, urlsplit, urlunsplit

import psycopg
from dotenv import dotenv_values
from psycopg.conninfo import conninfo_to_dict

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from create_app_role import _azure_target, _local_target  # noqa: E402
from job_finder.persistence.runtime_permissions import (  # noqa: E402
    REVIEW_GROUP,
    RUNTIME_ROLES,
    WORKER_GROUP,
    apply_runtime_grants,
    ensure_login_role,
    require_dataset_boundary,
    verify_runtime_grants,
)

READY = "JOBFINDER_RUNTIME_CREDENTIALS_READY"
PHASE = "JOBFINDER_RUNTIME_ACCESS"


def credential_key(component):
    return f"JOBFINDER_{component.upper()}_DATABASE_URL"


def runtime_url(admin_url: str, role: str, password: str) -> str:
    """Preserve libpq's TLS settings while replacing only the login."""
    parsed = urlsplit(admin_url)
    host = parsed.netloc.rsplit("@", 1)[1]
    return urlunsplit(parsed._replace(netloc=f"{quote(role, safe='')}:{quote(password, safe='')}@{host}"))


def write_credentials(path, values):
    """Replace the ignored file atomically; callers never pass its contents to a shell."""
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_suffix(path.suffix + ".staging")
    try:
        with staging.open("w", encoding="utf-8") as output:
            os.chmod(staging, 0o600)
            output.write("# F09: lokal geheim halten; erst nach Abnahme auf split stellen.\n")
            output.write("".join(f"{key}={value}\n" for key, value in values.items()))
        staging.replace(path)
    finally:
        staging.unlink(missing_ok=True)


def prepare(target, credential_file, *, apply=False):
    """Keep legacy access running until a separately approved phase switch."""
    database = target["database"]
    with psycopg.connect(**target["connect_kwargs"]) as admin:
        if not apply:
            admin.execute("SET TRANSACTION READ ONLY")
        else:
            # Serialize discovery and file staging, not just the final grants.
            admin.execute("SELECT pg_advisory_xact_lock(hashtextextended('jobfinder-runtime-grants',0))")
        values = (
            {key: value for key, value in dotenv_values(credential_file).items() if value is not None}
            if credential_file.exists()
            else {}
        )
        present = {
            key: bool(admin.execute("SELECT 1 FROM pg_roles WHERE rolname=%s", (role,)).fetchone())
            for key, role in RUNTIME_ROLES.items()
        }
        if not apply:
            return {"mode": "inspect", "roles_present": present}
        if values.get(PHASE, "legacy") != "legacy":
            raise RuntimeError(
                "Bereits umgeschalteter Zugang; weitere Wartung getrennt prüfen, keine automatische Rückschaltung."
            )
        require_dataset_boundary(admin, REVIEW_GROUP)
        expected = conninfo_to_dict(target["admin_url"])
        for component, role in RUNTIME_ROLES.items():
            key = credential_key(component)
            url = values.get(key)
            if url:
                actual = conninfo_to_dict(url)
                if actual.get("user") != role or any(
                    actual.get(item) != expected.get(item)
                    for item in ("host", "port", "dbname", "sslmode", "sslrootcert")
                ):
                    raise RuntimeError("Gespeicherter Laufzeitzugang gehört nicht zum gewählten Ziel; keine Änderung.")
                if not actual.get("password"):
                    raise RuntimeError("Gespeicherter Laufzeitzugang enthält kein Passwort; keine Änderung.")
            elif present[component]:
                raise RuntimeError(
                    "Bestehende Rolle ohne passenden lokalen Zugang; Passwort wird nicht automatisch ersetzt."
                )
            else:
                values[key] = runtime_url(target["admin_url"], role, secrets.token_hex(24))
            if present[component]:
                # Validate existing passwords before making any grants or writing a ready marker.
                with psycopg.connect(values[key], connect_timeout=10) as runtime:
                    runtime.execute("SET TRANSACTION READ ONLY")
                    identity = runtime.execute("SELECT current_user").fetchone()
                    if not identity or identity[0] != role:
                        raise RuntimeError("Unerwartete Datenbankidentität; keine Änderung.")
        values[READY] = "0"
        values[PHASE] = "legacy"
        write_credentials(credential_file, values)
        for component, role in RUNTIME_ROLES.items():
            ensure_login_role(admin, role, conninfo_to_dict(values[credential_key(component)])["password"])
        apply_runtime_grants(
            admin,
            database,
            target["admin_role"],
            roles=RUNTIME_ROLES,
            worker_group=WORKER_GROUP,
            review_group=REVIEW_GROUP,
        )
    for component, role in RUNTIME_ROLES.items():
        with psycopg.connect(values[credential_key(component)], connect_timeout=10) as runtime:
            runtime.execute("SET TRANSACTION READ ONLY")
            identity = runtime.execute("SELECT current_user").fetchone()
            if not identity or identity[0] != role:
                raise RuntimeError("Laufzeitzugang konnte nicht bestätigt werden; keine Umschaltung.")
            verify_runtime_grants(runtime, database, roles={component: role})
    values[READY] = "1"
    write_credentials(credential_file, values)
    return {"mode": "prepared", "roles_present": dict.fromkeys(RUNTIME_ROLES, True), "activated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--target", choices=("local", "azure"), default="local")
    parser.add_argument("--apply", action="store_true", help="Rollen/Rechte anlegen; keine Laufzeitumschaltung.")
    args = parser.parse_args()
    try:
        target = _azure_target() if args.target == "azure" else _local_target()
        path = target["env_file"].with_name(f".env.runtime-{args.target}")
        result = prepare(target, path, apply=args.apply)
    except (psycopg.Error, RuntimeError, OSError, ValueError):
        # Driver/URL errors can include credentials. Never print exception text.
        print(
            "F09-Vorbereitung abgebrochen. Ziel, Migration, Rollenattribute und lokale Zugänge prüfen; kein automatischer Passwortwechsel."
        )
        raise SystemExit(1) from None
    print(
        f"F09: {result['mode']}; "
        + ", ".join(f"{key}={'vorhanden' if present else 'fehlt'}" for key, present in result["roles_present"].items())
    )


if __name__ == "__main__":
    main()
