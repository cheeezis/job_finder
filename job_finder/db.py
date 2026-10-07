"""Initialize, check, back up and restore the PostgreSQL data store."""

import argparse
import json
from datetime import UTC, datetime, timedelta

from job_finder.paths import APPLICATION_DOCUMENTS_DIR
from job_finder.persistence import document_store, job_listings
from job_finder.persistence.application_documents import orphaned_documents
from job_finder.persistence.database import initialize, transaction
from job_finder.persistence.postgres_backup import create_postgres_backup, restore_backup
from job_finder.persistence.postgres_store import prune_cache
from job_finder.persistence.schema_migrations import migrate, schema_status
from job_finder.workflow.memory import load_memory


def main():
    """Run an explicit maintenance operation without printing connection secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("migrate", help="Leere DB anlegen, geprüfte Alt-DB übernehmen oder auf head migrieren.")
    commands.add_parser("schema-status", help="Struktur und Migrationsstand ausschließlich lesend prüfen.")
    commands.add_parser("check")
    cleanup = commands.add_parser("prune-cache")
    cleanup.add_argument("--days", type=int, default=30)
    backup = commands.add_parser("backup")
    backup.add_argument("--documents-dir", default=str(APPLICATION_DOCUMENTS_DIR))
    orphans = commands.add_parser(
        "orphaned-documents", help="Dokumente ohne Verweis auflisten; löschen nur mit --delete."
    )
    orphans.add_argument("--hours", type=int, default=24)
    orphans.add_argument("--delete", action="store_true")
    orphans.add_argument("--documents-dir", default=str(APPLICATION_DOCUMENTS_DIR))
    listings = commands.add_parser(
        "listings-drift", help="Anzeigen-Tabellen mit den JSON-Feldern vergleichen; --repair leitet sie neu ab."
    )
    listings.add_argument("--repair", action="store_true")
    restore = commands.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--documents-dir", required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        result = {"initialized": True}
    elif args.command == "migrate":
        result = migrate()
    elif args.command == "schema-status":
        result = schema_status()
    elif args.command == "backup":
        result = {"backup": str(create_postgres_backup(documents_dir=args.documents_dir))}
    elif args.command == "restore":
        result = restore_backup(args.archive, args.documents_dir)
    elif args.command == "orphaned-documents":
        result = clean_orphaned_documents(args.documents_dir, timedelta(hours=args.hours), delete=args.delete)
    elif args.command == "listings-drift":
        with transaction() as connection:
            if args.repair:
                result = {
                    "drift_before": job_listings.resync(connection),
                    "drift_after": job_listings.drift(connection),
                }
            else:
                result = {"drift": job_listings.drift(connection)}
    elif args.command == "prune-cache":
        result = {"removed_cache_entries": prune_cache(args.days)}
    else:
        with transaction() as connection:
            result = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchall()[0][0]
                for table in (
                    "job_state",
                    "workflow_history",
                    "application_documents",
                    "jobs",
                    "recommendations",
                    "notifications",
                    "manual_sources",
                    "source_cache",
                    "agent_usage",
                    "agent_fact_sheets",
                )
            }
    print(json.dumps(result, indent=2, ensure_ascii=False))


def clean_orphaned_documents(root, older_than, *, delete=False):
    """List documents no remembered job refers to; remove them only when asked."""
    if older_than < timedelta(hours=1):
        raise ValueError("Nur Dokumente, die mindestens eine Stunde alt sind; ein Upload könnte noch laufen.")
    keys = orphaned_documents(load_memory(), root, older_than=older_than, now=datetime.now(UTC))
    if delete:
        for key in keys:
            document_store.delete(key, root)
    return {"orphaned": keys, "deleted": len(keys) if delete else 0}


if __name__ == "__main__":
    main()
