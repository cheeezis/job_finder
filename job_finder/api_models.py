"""Response shapes of the review's read endpoints, published in its OpenAPI schema.

The browser code takes its types from that schema. The endpoints answer with
the fields each item actually has (response_model_exclude_unset), so optional
fields are missing rather than null, as before these models. Fields a model
does not name are left out, so a new field of a card or application needs its
place here, and the browser code gets its type with it.
"""

from typing import Literal

from pydantic import BaseModel

Number = int | float


class SourceLink(BaseModel):
    source: str
    url: str


class CompanyApplication(BaseModel):
    """An application at the card's company, for the review's hint."""

    title: str
    workflow_status: str
    open: bool


class FactSheetInfo(BaseModel):
    """The agent's fact sheet for a card, or why it stopped."""

    model: str
    complete: bool
    note: str | None = None
    sheet: dict | None = None
    cost_eur: float
    created_at: str
    retryable: bool
    outdated: list[str] = []


class ReviewCard(BaseModel):
    """One card of the review: a job with its listings and the user's decision."""

    id: str
    recommendation_id: str | None = None
    title: str
    company: str | None = None
    locations: list[str] = []
    work_mode: str | None = None
    remote_percentage: Number | None = None
    career_levels: list[str] = []
    published_at: str | None = None
    fetched_at: str | None = None
    first_seen_at: str | None = None
    cache_stale: bool = False
    is_new: bool = False
    match_percent: Number | None = None
    role_group: str | None = None
    role_label: str | None = None
    experience_level: str | None = None
    experience_rank: int | None = None
    url: str = ""
    source_links: list[SourceLink] = []
    international: bool = False
    location_precheck: str | None = None
    prefilter_warning: str | None = None
    current_snapshot_missing: bool = False
    workflow_status: str
    application_tracked: bool = False
    review_note: str = ""
    fact_sheet_rerun: bool = False
    company_applications: list[CompanyApplication] = []
    fact_sheet: FactSheetInfo | None = None


class RecommendationsResponse(BaseModel):
    recommendations: list[ReviewCard]
    workflow_statuses: list[str]
    route_origin: str


class HistoryEvent(BaseModel):
    status: str
    occurred_on: str | None = None
    scheduled_for: str | None = None
    reason: str | None = None
    event_index: int | None = None


class Document(BaseModel):
    id: str | None = None
    kind: str | None = None
    name: str | None = None


class LinkedListing(BaseModel):
    title: str
    company: str
    review_note: str


class Application(BaseModel):
    """One application with its timeline, documents and derived figures."""

    id: str
    title: str
    company: str
    url: str
    source_links: list[SourceLink]
    active: bool
    workflow_status: str
    review_note: str
    salary_expectation_eur: int | None = None
    applied_on: str | None = None
    response_on: str | None = None
    days_to_response: int | None = None
    next_interview_at: str | None = None
    last_interview_at: str | None = None
    last_event_on: str | None = None
    workflow_history: list[HistoryEvent]
    documents: list[Document]
    linked_listings: list[LinkedListing]
    automatic_no_response: bool
    has_response: bool
    has_interview: bool
    has_rejection: bool
    has_no_response: bool
    has_offer: bool
    has_withdrawal: bool


class ApplicationStatistics(BaseModel):
    total: int
    open: int
    completed: int
    responses: int
    interviews: int
    rejections: int
    no_responses: int
    offers: int
    withdrawals: int
    response_rate_percent: int
    average_response_days: float | None = None
    response_time_samples: int


class ApplicationsResponse(BaseModel):
    applications: list[Application]
    completed_applications: list[Application]
    statistics: ApplicationStatistics
    application_statuses: list[str]
    workflow_statuses: list[str]


class Run(BaseModel):
    """The newest finder run of one runner with its key figures."""

    run_id: str
    runner: Literal["cloud", "hybrid", "local"]
    started_at: str
    finished_at: str | None = None
    outcome: Literal["running", "finished", "failed"]
    jobs_total: int | None = None
    jobs_new: int | None = None
    review_new: int | None = None
    sources_partial: int | None = None
    sources_failed: int | None = None


class RunsResponse(BaseModel):
    runs: list[Run]


class SourcesResponse(BaseModel):
    labels: dict[str, str]
