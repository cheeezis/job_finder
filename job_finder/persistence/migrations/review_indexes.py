"""Frozen lookup indexes of revision 0004; the migration creates them and the validation expects them.

The review finds the remembered jobs of a listing by its URLs and linked ids,
which live in the JSONB column extra. Without these GIN indexes PostgreSQL
reads the whole job_state table for each review list.
"""

REVISION = "0004_review_lookup_indexes"
# Name -> JSONB key in job_state.extra.
INDEXES = {"job_state_source_urls": "source_urls", "job_state_linked_job_ids": "linked_job_ids"}


def create_statement(name, key):
    return f"CREATE INDEX {name} ON public.job_state USING gin ((extra -> '{key}'))"


def expected_definition(name, key):
    """Return the definition as pg_get_indexdef renders it."""
    return f"CREATE INDEX {name} ON public.job_state USING gin (((extra -> '{key}'::text)))"
