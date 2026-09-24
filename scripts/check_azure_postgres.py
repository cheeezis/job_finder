"""Read-only TLS check of the provisioned Azure database, without changing local config."""

import psycopg

import azure_postgres


def main():
    """Use the setup administrator only to verify server readiness and TLS."""
    with psycopg.connect(
        **azure_postgres.admin_connection(),
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
