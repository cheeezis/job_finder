"""Shared domain models for jobs collected from different sources."""

from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum


class WorkflowStatus(str, Enum):
    """Manual lifecycle of a job or application."""

    NEW = "new"
    REVIEW = "review"
    INTERESTING = "interesting"
    IGNORED = "ignored"
    APPLIED = "applied"
    INTERVIEW = "interview"
    REJECTED = "rejected"
    OFFER = "offer"
    CLOSED = "closed"


class FilterStatus(str, Enum):
    """Result of the rule-based job filter."""

    INCLUDED = "included"
    EXCLUDED = "excluded"


class WorkMode(str, Enum):
    """Where the advertised work is performed."""

    ONSITE = "onsite"
    HYBRID = "hybrid"
    REMOTE = "remote"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class JobSource:
    """One portal listing that belongs to a canonical job."""

    source: str
    url: str
    source_id: str | None = None
    application_url: str | None = None


@dataclass(slots=True)
class Job:
    """Canonical job data shared by imports, scoring, and persistence."""

    id: str
    title: str
    company: str
    locations: list[str]
    sources: list[JobSource]
    description_raw: str
    description_clean: str
    work_mode: WorkMode = WorkMode.UNKNOWN
    remote_percentage: int | None = None
    employment_type: str | None = None
    salary_min_eur: int | None = None
    salary_max_eur: int | None = None
    published_at: date | None = None
    first_seen_at: datetime | None = None
    last_seen_at: datetime | None = None
    fetched_at: datetime | None = None
    workflow_status: WorkflowStatus = WorkflowStatus.NEW
    match_score: int | None = None
    filter_status: FilterStatus | None = None
    score_reasons: list[str] = field(default_factory=list)

    def __post_init__(self):
        """Reject invalid percentages, scores, and salary ranges."""
        validate_percentage("remote_percentage", self.remote_percentage)
        validate_percentage("match_score", self.match_score)

        salary_values = [self.salary_min_eur, self.salary_max_eur]
        if any(value is not None and value < 0 for value in salary_values):
            raise ValueError("salary values cannot be negative")
        if (
            self.salary_min_eur is not None
            and self.salary_max_eur is not None
            and self.salary_min_eur > self.salary_max_eur
        ):
            raise ValueError("salary_min_eur cannot exceed salary_max_eur")


def validate_percentage(name, value):
    """Validate an optional integer percentage on the fixed 0-100 scale."""
    if value is not None and not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")
