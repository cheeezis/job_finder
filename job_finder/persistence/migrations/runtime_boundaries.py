"""Frozen F09 dataset boundary; retain legacy access until identities are switched."""

from psycopg import sql

REVIEW_GROUP = "jobfinder_review_access"
REVISION = "0002_runtime_boundaries"
REVIEW_DATASETS = ("internal/jobs.json", "output/recommendations.json", "internal/manual_jobs_cache.json")


def dataset_condition(review_group=REVIEW_GROUP):
    """Restrict review members, including SET ROLE, without requiring the group yet."""
    return sql.SQL(
        "NOT coalesce(pg_catalog.pg_has_role(current_user, pg_catalog.to_regrole({}), 'MEMBER'), false) OR name IN ({})"
    ).format(sql.Literal(review_group), sql.SQL(",").join(map(sql.Literal, REVIEW_DATASETS)))


def dataset_predicate(review_group=REVIEW_GROUP):
    return dataset_condition(review_group).as_string(None)


def rendered_predicate(review_group=REVIEW_GROUP):
    """PostgreSQL's parsed expression, frozen for read-only catalog validation."""
    group = sql.Literal(review_group).as_string(None)
    names = ", ".join(f"{sql.Literal(name).as_string(None)}::text" for name in REVIEW_DATASETS)
    return (
        f"((NOT COALESCE(pg_has_role(CURRENT_USER, (to_regrole({group}::text))::oid, 'MEMBER'::text), false)) "
        f"OR (name = ANY (ARRAY[{names}])))"
    )
