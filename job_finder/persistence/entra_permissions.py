"""Add Object-ID-bound Entra logins to the existing, verified F09 capability groups."""

from uuid import UUID

from psycopg import sql

from job_finder.persistence.runtime_permissions import (
    REVIEW_GROUP,
    RUNTIME_ROLES,
    WORKER_GROUP,
    _validate_role,
    require_dataset_boundary,
    verify_runtime_grants,
)

ENTRA_ROLES = {
    "worker": "jobfinder_worker_entra",
    "review": "jobfinder_review_entra",
    "hybrid": "jobfinder_hybrid_entra",
}


def validate_principals(values):
    """Require distinct service-principal Object IDs, not display names or client IDs."""
    if not isinstance(values, dict) or set(values) != {"tenant_id", *ENTRA_ROLES}:
        raise RuntimeError("Entra-Principals benötigen Tenant und drei Object-IDs.")
    try:
        parsed = {key: UUID(value) for key, value in values.items()}
    except (ValueError, TypeError, AttributeError):
        raise RuntimeError("Ungültige Entra-Object-ID oder Tenant-ID.") from None
    if any(value.int == 0 for value in parsed.values()) or len({parsed[key] for key in ENTRA_ROLES}) != 3:
        raise RuntimeError("Entra-Laufzeitidentitäten müssen verschieden und vollständig sein.")
    return {key: str(value) for key, value in parsed.items()}


def list_principals(connection):
    # Azure returns the role in column "rolname", although the create function's parameter is called rolename.
    cursor = connection.execute("SELECT * FROM pg_catalog.pgaadauth_list_principals(false)")
    columns = [column.name.casefold() for column in cursor.description]
    return [dict(zip(columns, row, strict=True)) for row in cursor.fetchall()]


def create_principal(connection, role, object_id):
    connection.execute(
        "SELECT * FROM pg_catalog.pgaadauth_create_principal_with_oid(%s,%s,%s,%s,%s)",
        (role, object_id, "service", False, False),
    )


def _mapped_roles(connection, principals, roles):
    mappings = list_principals(connection)
    present = {}
    for component, role in roles.items():
        object_id = principals[component]
        rows = [row for row in mappings if row["rolname"] == role]
        if any(row["objectid"].casefold() == object_id and row["rolname"] != role for row in mappings):
            raise RuntimeError("Laufzeitidentität ist bereits einer anderen Entra-Rolle zugeordnet.")
        exists = _validate_role(connection, role, login=True)
        if rows:
            if not exists or len(rows) != 1:
                raise RuntimeError("Mehrdeutige oder fehlende Entra-Datenbankrolle.")
            row = rows[0]
            if (
                row["objectid"].casefold() != object_id
                or row["tenantid"].casefold() != principals["tenant_id"]
                or row["principaltype"] != "service"
                or row["isadmin"] != 0
                or row["ismfa"] != 0
            ):
                raise RuntimeError("Entra-Rolle hat eine unerwartete Identität oder Admin-Zuordnung.")
        elif exists:
            raise RuntimeError("Bestehende Rolle ohne passende Entra-Zuordnung; keine automatische Änderung.")
        present[component] = exists
    return present


def _memberships(connection, role):
    return connection.execute(
        "SELECT parent.rolname,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m "
        "JOIN pg_roles parent ON parent.oid=m.roleid JOIN pg_roles member ON member.oid=m.member "
        "WHERE member.rolname=%s",
        (role,),
    ).fetchall()


def _require_existing_boundary(connection, database, legacy_roles, worker_group, review_group):
    require_dataset_boundary(connection, review_group)
    for group in (worker_group, review_group):
        if not _validate_role(connection, group, login=False) or _memberships(connection, group):
            raise RuntimeError("Bestehende Zugriffsgruppen fehlen oder haben zusätzliche Rechte.")
    for component, role in legacy_roles.items():
        group = review_group if component == "review" else worker_group
        if _memberships(connection, role) != [(group, False, True, True)]:
            raise RuntimeError("Bestehende Laufzeitrolle hat unerwartete Mitgliedschaften.")
    verify_runtime_grants(connection, database, roles=legacy_roles)
    if connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "CROSS JOIN LATERAL aclexplode(coalesce(c.relacl,acldefault('r',c.relowner))) a "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f') AND a.grantee=0)"
    ).fetchone()[0]:
        raise RuntimeError("PUBLIC hat Tabellenrechte; neue Logins werden nicht angelegt.")


def prepare(
    identity_connection,
    app_connection,
    principals,
    database,
    *,
    apply=False,
    roles=None,
    legacy_roles=None,
    worker_group=WORKER_GROUP,
    review_group=REVIEW_GROUP,
):
    """Inspect by default; provision identities in postgres and grant memberships in the app DB.

    The connections must be dedicated autocommit connections to the same server.
    Their two transactions cannot commit atomically across databases. If the grant
    phase fails, newly created logins remain without capability-group membership;
    rerunning resumes safely. No runtime, password, schema or table grant is changed.
    """
    principals = validate_principals(principals)
    roles = roles or ENTRA_ROLES
    legacy_roles = legacy_roles or RUNTIME_ROLES
    with app_connection.transaction():
        if apply:
            app_connection.execute("SELECT pg_advisory_xact_lock(hashtextextended('jobfinder-runtime-grants',0))")
        else:
            app_connection.execute("SET TRANSACTION READ ONLY")
        _require_existing_boundary(app_connection, database, legacy_roles, worker_group, review_group)
        # Same-server global roles, but pgaadauth must run in the postgres database.
        with identity_connection.transaction():
            if apply:
                identity_connection.execute(
                    "SELECT pg_advisory_xact_lock(hashtextextended('jobfinder-entra-principals',0))"
                )
            else:
                identity_connection.execute("SET TRANSACTION READ ONLY")
            present = _mapped_roles(identity_connection, principals, roles)
            if apply:
                for component, role in roles.items():
                    if not present[component]:
                        create_principal(identity_connection, role, principals[component])
                present = _mapped_roles(identity_connection, principals, roles)
                if not all(present.values()):
                    raise RuntimeError("Entra-Rollen konnten nicht vollständig bestätigt werden.")
        granted = {}
        for component, role in roles.items():
            if not present[component]:
                granted[component] = False
                continue
            _validate_role(app_connection, role, login=True)
            group = review_group if component == "review" else worker_group
            memberships = _memberships(app_connection, role)
            if memberships and memberships != [(group, False, True, True)]:
                raise RuntimeError("Entra-Laufzeitrolle hat unerwartete Mitgliedschaften; keine Rechteänderung.")
            if apply and not memberships:
                app_connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(role)))
                memberships = _memberships(app_connection, role)
            granted[component] = memberships == [(group, False, True, True)]
            if granted[component]:
                verify_runtime_grants(app_connection, database, roles={component: role})
        verify_runtime_grants(app_connection, database, roles=legacy_roles)
    return {
        "mode": "prepared" if apply else "inspect",
        "roles_present": present,
        "grants_verified": granted,
        "activated": False,
    }
