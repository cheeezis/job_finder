"""Explicit PostgreSQL capabilities for worker, review and the local hybrid worker."""

from typing import LiteralString

from psycopg import sql

from job_finder.persistence.migrations.runtime_boundaries import REVIEW_GROUP, rendered_predicate
from job_finder.persistence.schema_migrations import _sql_shape

WORKER_GROUP = "jobfinder_worker_access"
RUNTIME_ROLES = {"worker": "jobfinder_worker", "review": "jobfinder_review", "hybrid": "jobfinder_hybrid"}
STATE_TABLES = ("job_state", "workflow_history", "application_documents")
REVIEW_WRITE_TABLES = (*STATE_TABLES, "datasets", "jobs", "recommendations", "manual_sources")
REVIEW_READ_TABLES = ("agent_usage", "agent_fact_sheets")
WORKER_TABLES = (*REVIEW_WRITE_TABLES, *REVIEW_READ_TABLES, "notifications", "source_cache")


def require_dataset_boundary(connection, review_group=REVIEW_GROUP):
    """Fail closed before granting review access if F09's row policy is absent or changed."""
    row = connection.execute(
        "SELECT c.relrowsecurity,p.polpermissive,p.polcmd,p.polroles,pg_get_expr(p.polqual,p.polrelid),"
        "pg_get_expr(p.polwithcheck,p.polrelid) FROM pg_class c "
        "JOIN pg_namespace n ON n.oid=c.relnamespace "
        "LEFT JOIN pg_policy p ON p.polrelid=c.oid AND p.polname='runtime_datasets' "
        "WHERE n.nspname='public' AND c.relname='datasets'"
    ).fetchone()
    expected = _sql_shape(rendered_predicate(review_group))
    if not row or row[:4] != (True, True, "*", [0]) or any(_sql_shape(value) != expected for value in row[4:]):
        raise RuntimeError("F09-Datensatzgrenze fehlt oder weicht ab. Migration/Rechte zuerst administrativ prüfen.")
    policies = connection.execute(
        "SELECT count(*) FROM pg_policy WHERE polrelid='public.datasets'::regclass"
    ).fetchone()[0]
    if policies != 1:
        raise RuntimeError("Unerwartete zusätzliche Datensatz-Policy; keine Rollenfreigabe.")


def ensure_access_group(connection, role):
    """Create a non-login capability group, refusing privileged existing identities."""
    exists = _validate_role(connection, role, login=False)
    if not exists:
        connection.execute(
            sql.SQL("CREATE ROLE {} NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS").format(
                sql.Identifier(role)
            )
        )


def ensure_login_role(connection, role, password):
    """Create a runtime principal without rotating any existing password."""
    exists = _validate_role(connection, role, login=True)
    if not exists:
        connection.execute(
            sql.SQL(
                "CREATE ROLE {} LOGIN INHERIT PASSWORD {} NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS"
            ).format(sql.Identifier(role), sql.Literal(password))
        )


def _validate_role(connection, role, *, login):
    """Refuse existing roles with elevated attributes or object ownership."""
    row = connection.execute(
        "SELECT oid,rolcanlogin,rolsuper,rolcreatedb,rolcreaterole,rolreplication,rolbypassrls,rolinherit FROM pg_roles WHERE rolname=%s",
        (role,),
    ).fetchone()
    if row is None:
        return False
    if row[1] != login or any(row[2:7]) or not row[7]:
        raise RuntimeError("Bestehende Laufzeitrolle hat unerwartete Rollenattribute; keine automatische Änderung.")
    owned = connection.execute(
        "SELECT EXISTS(SELECT 1 FROM pg_class WHERE relowner=%s UNION ALL "
        "SELECT 1 FROM pg_namespace WHERE nspowner=%s UNION ALL SELECT 1 FROM pg_database WHERE datdba=%s "
        "UNION ALL SELECT 1 FROM pg_proc WHERE proowner=%s UNION ALL SELECT 1 FROM pg_type WHERE typowner=%s)",
        (row[0],) * 5,
    ).fetchone()[0]
    if owned:
        raise RuntimeError("Laufzeitrolle besitzt Datenbankobjekte; Rechte müssen administrativ geprüft werden.")
    return True


def apply_runtime_grants(
    connection, database, admin_role, *, roles=None, worker_group=WORKER_GROUP, review_group=REVIEW_GROUP
):
    """Apply a closed table allowlist; new tables receive no implicit runtime grants."""
    roles = roles or RUNTIME_ROLES
    require_dataset_boundary(connection, review_group)
    ensure_access_group(connection, worker_group)
    ensure_access_group(connection, review_group)
    expected_groups = {
        roles["worker"]: worker_group,
        roles["hybrid"]: worker_group,
        roles["review"]: review_group,
        worker_group: None,
        review_group: None,
    }
    for role, allowed_group in expected_groups.items():
        memberships = list(
            connection.execute(
                "SELECT parent.rolname,m.admin_option,m.inherit_option,m.set_option FROM pg_auth_members m JOIN pg_roles parent ON parent.oid=m.roleid "
                "JOIN pg_roles member ON member.oid=m.member WHERE member.rolname=%s",
                (role,),
            )
        )
        if any(
            group != allowed_group or admin_option or not inherit_option or not set_option
            for group, admin_option, inherit_option, set_option in memberships
        ):
            raise RuntimeError("Unerwartete Rollenmitgliedschaft; keine automatische Rechteänderung.")
        recipient = sql.Identifier(role)
        for statement in (
            "REVOKE ALL ON ALL TABLES IN SCHEMA public FROM {}",
            "REVOKE CREATE ON SCHEMA public FROM {}",
        ):
            connection.execute(sql.SQL(statement).format(recipient))
        connection.execute(
            sql.SQL("ALTER DEFAULT PRIVILEGES FOR ROLE {} IN SCHEMA public REVOKE ALL ON TABLES FROM {}").format(
                sql.Identifier(admin_role), recipient
            )
        )
        connection.execute(sql.SQL("GRANT CONNECT ON DATABASE {} TO {}").format(sql.Identifier(database), recipient))
        connection.execute(sql.SQL("GRANT USAGE ON SCHEMA public TO {}").format(recipient))
    for role, group in expected_groups.items():
        if group is not None:
            connection.execute(sql.SQL("GRANT {} TO {}").format(sql.Identifier(group), sql.Identifier(role)))
    grants: tuple[tuple[str, LiteralString, tuple[str, ...]], ...] = (
        (worker_group, "SELECT, INSERT, UPDATE, DELETE", WORKER_TABLES),
        (review_group, "SELECT, INSERT, UPDATE, DELETE", REVIEW_WRITE_TABLES),
        (review_group, "SELECT", REVIEW_READ_TABLES),
    )
    for group, privileges, tables in grants:
        connection.execute(
            sql.SQL("GRANT {} ON {} TO {}").format(
                sql.SQL(privileges),
                sql.SQL(",").join(sql.Identifier("public", table) for table in tables),
                sql.Identifier(group),
            )
        )
    verify_runtime_grants(connection, database, roles=roles)


def verify_runtime_grants(connection, database, *, roles=None):
    """Inspect effective rights, including inherited PUBLIC grants; never repair them implicitly."""
    roles = roles or RUNTIME_ROLES
    tables = connection.execute(
        "SELECT c.relname,c.oid FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
        "WHERE n.nspname='public' AND c.relkind IN ('r','p','v','m','f')"
    ).fetchall()
    for component, role in roles.items():
        _validate_role(connection, role, login=True)
        writable = REVIEW_WRITE_TABLES if component == "review" else WORKER_TABLES
        readable = (*writable, *REVIEW_READ_TABLES) if component == "review" else writable
        for table, oid in tables:
            for privilege in ("SELECT", "INSERT", "UPDATE", "DELETE", "TRUNCATE", "REFERENCES", "TRIGGER"):
                allowed = table in (readable if privilege == "SELECT" else writable) and privilege in {
                    "SELECT",
                    "INSERT",
                    "UPDATE",
                    "DELETE",
                }
                actual = connection.execute("SELECT has_table_privilege(%s,%s,%s)", (role, oid, privilege)).fetchone()[
                    0
                ]
                if actual != allowed:
                    raise RuntimeError(
                        "Wirksame Laufzeitrechte weichen von der erlaubten Matrix ab; keine Umschaltung."
                    )
        if connection.execute(
            "SELECT has_schema_privilege(%s,'public','CREATE') OR has_database_privilege(%s,%s,'CREATE')",
            (role, role, database),
        ).fetchone()[0]:
            raise RuntimeError("Laufzeitrolle hat wirksame DDL-Rechte; keine Umschaltung.")
