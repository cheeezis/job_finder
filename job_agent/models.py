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

    def to_dict(self):
        """Return JSON-compatible source data."""
        return {
            "source": self.source,
            "url": self.url,
            "source_id": self.source_id,
            "application_url": self.application_url,
        }

    @classmethod
    def from_dict(cls, values):
        """Restore a source from the current JSON representation."""
        return cls(
            source=values["source"],
            url=values["url"],
            source_id=values.get("source_id"),
            application_url=values.get("application_url"),
        )


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
    rule_score: int | None = None
    llm_score: int | None = None
    llm_result: dict | None = None
    filter_status: FilterStatus | None = None
    score_reasons: list[str] = field(default_factory=list)
    is_new: bool = False
    content_changed: bool = False

    def __post_init__(self):
        """Reject invalid percentages, scores, and salary ranges."""
        validate_percentage("remote_percentage", self.remote_percentage)
        validate_percentage("rule_score", self.rule_score)
        validate_percentage("llm_score", self.llm_score)

        salary_values = [self.salary_min_eur, self.salary_max_eur]
        if any(value is not None and value < 0 for value in salary_values):
            raise ValueError("salary values cannot be negative")
        if (
            self.salary_min_eur is not None
            and self.salary_max_eur is not None
            and self.salary_min_eur > self.salary_max_eur
        ):
            raise ValueError("salary_min_eur cannot exceed salary_max_eur")

    @property
    def primary_source(self):
        """Return the preferred source listing for links and display."""
        return self.sources[0] if self.sources else None

    @property
    def primary_url(self):
        """Return the preferred public listing URL."""
        return self.primary_source.url if self.primary_source else ""

    @property
    def location_text(self):
        """Return all advertised locations as display text."""
        return ", ".join(self.locations) or "unbekannt"

    @property
    def remote_text(self):
        """Return remote information in the format used by scoring."""
        if self.remote_percentage is not None:
            return f"{self.remote_percentage}%"
        if self.work_mode is WorkMode.HYBRID:
            return "homeoffice"
        return "0%"

    def to_dict(self):
        """Return the complete job as JSON-compatible values."""
        return {
            "id": self.id,
            "title": self.title,
            "company": self.company,
            "locations": list(self.locations),
            "sources": [source.to_dict() for source in self.sources],
            "description_raw": self.description_raw,
            "description_clean": self.description_clean,
            "work_mode": self.work_mode.value,
            "remote_percentage": self.remote_percentage,
            "employment_type": self.employment_type,
            "salary_min_eur": self.salary_min_eur,
            "salary_max_eur": self.salary_max_eur,
            "published_at": format_temporal(self.published_at),
            "first_seen_at": format_temporal(self.first_seen_at),
            "last_seen_at": format_temporal(self.last_seen_at),
            "fetched_at": format_temporal(self.fetched_at),
            "workflow_status": self.workflow_status.value,
            "rule_score": self.rule_score,
            "llm_score": self.llm_score,
            "llm_result": self.llm_result,
            "filter_status": (
                self.filter_status.value if self.filter_status is not None else None
            ),
            "score_reasons": list(self.score_reasons),
        }

    @classmethod
    def from_dict(cls, values):
        """Restore a job from the current JSON representation."""
        filter_status = values.get("filter_status")
        return cls(
            id=values["id"],
            title=values["title"],
            company=values["company"],
            locations=list(values["locations"]),
            sources=[JobSource.from_dict(item) for item in values["sources"]],
            description_raw=values["description_raw"],
            description_clean=values["description_clean"],
            work_mode=WorkMode(values["work_mode"]),
            remote_percentage=values.get("remote_percentage"),
            employment_type=values.get("employment_type"),
            salary_min_eur=values.get("salary_min_eur"),
            salary_max_eur=values.get("salary_max_eur"),
            published_at=parse_date(values.get("published_at")),
            first_seen_at=parse_datetime(values.get("first_seen_at")),
            last_seen_at=parse_datetime(values.get("last_seen_at")),
            fetched_at=parse_datetime(values.get("fetched_at")),
            workflow_status=WorkflowStatus(
                values.get("workflow_status", WorkflowStatus.NEW.value)
            ),
            rule_score=values.get("rule_score"),
            llm_score=values.get("llm_score"),
            llm_result=values.get("llm_result"),
            filter_status=(
                FilterStatus(filter_status) if filter_status is not None else None
            ),
            score_reasons=list(values.get("score_reasons", [])),
        )


def validate_percentage(name, value):
    """Validate an optional integer percentage on the fixed 0-100 scale."""
    if value is not None and not 0 <= value <= 100:
        raise ValueError(f"{name} must be between 0 and 100")


def format_temporal(value):
    """Format an optional date or datetime for JSON storage."""
    return value.isoformat() if value is not None else None


def parse_date(value):
    """Parse an optional ISO date."""
    return date.fromisoformat(value) if value else None


def parse_datetime(value):
    """Parse an optional ISO datetime."""
    return datetime.fromisoformat(value) if value else None
