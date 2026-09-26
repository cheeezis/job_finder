"""Initialize, check, back up and restore the PostgreSQL data store."""

import argparse
import json

from job_finder.paths import APPLICATION_DOCUMENTS_DIR
from job_finder.persistence.database import initialize, transaction
from job_finder.persistence.postgres_backup import create_postgres_backup, restore_backup
from job_finder.persistence.postgres_store import prune_cache
from job_finder.workflow.duplicates import merge_duplicate_decisions


def main():
    """Run an explicit maintenance operation without printing connection secrets."""
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("init")
    commands.add_parser("check")
    cleanup = commands.add_parser("prune-cache")
    cleanup.add_argument("--days", type=int, default=30)
    duplicates = commands.add_parser(
        "merge-duplicates", help="join jobs decided more than once; lists only, unless --apply"
    )
    duplicates.add_argument("--apply", action="store_true")
    backup = commands.add_parser("backup")
    backup.add_argument("--documents-dir", default=str(APPLICATION_DOCUMENTS_DIR))
    restore = commands.add_parser("restore")
    restore.add_argument("archive")
    restore.add_argument("--documents-dir", required=True)
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        result = {"initialized": True}
    elif args.command == "backup":
        result = {"backup": str(create_postgres_backup(documents_dir=args.documents_dir))}
    elif args.command == "restore":
        result = restore_backup(args.archive, args.documents_dir)
    elif args.command == "prune-cache":
        result = {"removed_cache_entries": prune_cache(args.days)}
    elif args.command == "merge-duplicates":
        result = merge_duplicate_decisions(apply=args.apply)
    else:
        with transaction() as connection:
            result = {
                table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
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


if __name__ == "__main__":
    main()
