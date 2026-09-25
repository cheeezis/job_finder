"""Read-only TLS check of the provisioned Azure database, without changing local config."""

import json
import ssl
import subprocess
from pathlib import Path

import psycopg

PROJECT = Path(__file__).resolve().parents[1]


def main():
    """Use the setup administrator only to verify server readiness and TLS."""
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
    # libpq needs a PEM bundle. Export the OS trust store through Python's SSL
    # context so Windows can verify the server without disabling certificate checks.
    certificates = ssl.create_default_context().get_ca_certs(binary_form=True)
    if not certificates:
        raise RuntimeError("Keine vertrauenswürdigen CA-Zertifikate verfügbar.")
    bundle = PROJECT / "tmp/azure-postgres-trusted-roots.pem"
    bundle.parent.mkdir(parents=True, exist_ok=True)
    bundle.write_text(
        "".join(ssl.DER_cert_to_PEM_cert(cert) for cert in certificates), encoding="ascii"
    )
    with psycopg.connect(
        host=outputs["postgres_host"]["value"],
        dbname=outputs["postgres_database"]["value"],
        user="jobfinder_admin",
        password=settings["postgres_admin_password"],
        sslmode="verify-full",
        sslrootcert=str(bundle),
        connect_timeout=20,
        options="-c default_transaction_read_only=on -c statement_timeout=15000",
    ) as connection:
        version = connection.execute("SHOW server_version").fetchone()[0]
        tls = connection.execute(
            "SELECT ssl,version FROM pg_stat_ssl WHERE pid=pg_backend_pid()"
        ).fetchone()
        if not tls or not tls[0]:
            raise RuntimeError("Die Verbindung verwendet kein TLS.")
        print(f"Azure PostgreSQL {version}: erreichbar; {tls[1]}, sslmode=verify-full.")


if __name__ == "__main__":
    main()
