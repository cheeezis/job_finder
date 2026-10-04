"""Explicit runtime authentication; acquire a token for each new Entra connection."""

import os
from functools import lru_cache

import psycopg
from azure.core.credentials import TokenCredential
from azure.identity import ClientSecretCredential, ManagedIdentityCredential
from psycopg.conninfo import conninfo_to_dict

POSTGRES_SCOPE = "https://ossrdbms-aad.database.windows.net/.default"


@lru_cache(maxsize=4)
def _credential(mode: str, client_id: str, tenant_id: str = "", client_secret: str = "") -> TokenCredential:
    """Reuse the SDK's token cache while pinning the chosen identity and auth method."""
    if mode == "managed_identity":
        return ManagedIdentityCredential(client_id=client_id)
    return ClientSecretCredential(tenant_id=tenant_id, client_id=client_id, client_secret=client_secret)


def entra_parameters(url: str):
    """Validate the explicit password-free target before acquiring any token."""
    try:
        parameters = conninfo_to_dict(url)
    except psycopg.Error:
        raise RuntimeError("Ungültige Entra-Datenbankkonfiguration.") from None
    # No libpq defaults from PGPASSWORD, .pgpass, services or local sockets.
    if (
        any(parameters.get(key) for key in ("password", "passfile", "service", "servicefile", "hostaddr"))
        or any(os.environ.get(key) for key in ("PGSERVICE", "PGSERVICEFILE", "PGHOSTADDR"))
        or not all(parameters.get(key) for key in ("host", "dbname", "user"))
        or parameters.get("sslmode") != "verify-full"
    ):
        raise RuntimeError("Entra benötigt einen passwortfreien DSN mit Host, Datenbank, Rolle und verify-full.")
    return parameters


def connect_runtime(url: str, *, autocommit: bool = False) -> psycopg.Connection:
    """Keep password mode unchanged; Entra never falls back to a developer or password login."""
    mode = os.environ.get("JOBFINDER_DATABASE_AUTH", "password")
    if mode == "password":
        return psycopg.connect(url, autocommit=autocommit, connect_timeout=10)
    if mode not in {"managed_identity", "service_principal"}:
        raise RuntimeError("Unbekannte JOBFINDER_DATABASE_AUTH; keine Datenbankanmeldung.")
    parameters = entra_parameters(url)
    if mode == "managed_identity":
        client_id = os.environ.get("JOBFINDER_MANAGED_IDENTITY_CLIENT_ID", "")
        tenant_id = client_secret = ""
    else:
        client_id = os.environ.get("AZURE_CLIENT_ID", "")
        tenant_id = os.environ.get("AZURE_TENANT_ID", "")
        client_secret = os.environ.get("AZURE_CLIENT_SECRET", "")
    if not client_id or (mode == "service_principal" and (not tenant_id or not client_secret)):
        raise RuntimeError("Explizite Entra-Laufzeitidentität fehlt; keine alternative Anmeldung.")
    try:
        token = _credential(mode, client_id, tenant_id, client_secret).get_token(POSTGRES_SCOPE).token
        if not token:
            raise RuntimeError("Empty token")
    except Exception:
        # Identity errors can contain tenant details or HTTP response bodies.
        raise RuntimeError("Entra-Token für die Datenbank konnte nicht bezogen werden.") from None
    try:
        return psycopg.connect(**{**parameters, "password": token, "connect_timeout": 10}, autocommit=autocommit)
    except psycopg.Error:
        # Connection diagnostics can echo a DSN or token. Do not retry writes or change identities.
        raise RuntimeError(
            "Entra-Datenbankverbindung fehlgeschlagen; Identität, Rolle, TLS und Erreichbarkeit prüfen."
        ) from None
