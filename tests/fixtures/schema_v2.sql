-- Historical pre-Alembic DDL, kept independent from the new baseline.

CREATE TABLE IF NOT EXISTS schema_version (version integer PRIMARY KEY);
CREATE TABLE IF NOT EXISTS job_state (
    scope text NOT NULL,
    job_id text NOT NULL,
    title text,
    company text,
    workflow_status text,
    active boolean,
    missed_runs integer,
    salary_expectation_eur bigint,
    personal_rating text,
    review_note text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id)
);
CREATE INDEX IF NOT EXISTS job_state_status ON job_state(scope, workflow_status);
CREATE TABLE IF NOT EXISTS workflow_history (
    scope text NOT NULL,
    job_id text NOT NULL,
    position integer NOT NULL,
    status text,
    occurred_on date,
    scheduled_for timestamp,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id, position),
    FOREIGN KEY (scope, job_id) REFERENCES job_state ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS application_documents (
    scope text NOT NULL,
    job_id text NOT NULL,
    position integer NOT NULL,
    id text,
    name text,
    kind text,
    stored_name text,
    folder_name text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (scope, job_id, position),
    FOREIGN KEY (scope, job_id) REFERENCES job_state ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS datasets (
    name text PRIMARY KEY,
    metadata jsonb NOT NULL,
    updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS jobs (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    job_id text NOT NULL,
    position integer NOT NULL,
    title text,
    company text,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, position)
);
CREATE TABLE IF NOT EXISTS recommendations (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    job_id text NOT NULL,
    position integer NOT NULL,
    title text,
    company text,
    match_percent double precision,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, position)
);
CREATE TABLE IF NOT EXISTS notifications (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    notification_key text NOT NULL,
    delivery_state text NOT NULL CHECK (delivery_state IN ('sent', 'pending')),
    job_id text,
    sent_at text,
    attempts integer,
    present text[] NOT NULL,
    extra jsonb NOT NULL,
    PRIMARY KEY (dataset, notification_key, delivery_state)
);
CREATE TABLE IF NOT EXISTS source_cache (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    cache_key text NOT NULL,
    position integer NOT NULL,
    payload jsonb NOT NULL,
    stored_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (dataset, cache_key)
);
CREATE TABLE IF NOT EXISTS manual_sources (
    dataset text NOT NULL REFERENCES datasets(name) ON DELETE CASCADE,
    url text NOT NULL,
    position integer NOT NULL,
    payload jsonb NOT NULL,
    PRIMARY KEY (dataset, url)
);
CREATE TABLE IF NOT EXISTS agent_usage (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    called_at timestamptz NOT NULL DEFAULT now(),
    job_id text NOT NULL,
    model text NOT NULL,
    input_tokens integer NOT NULL,
    cached_input_tokens integer NOT NULL,
    output_tokens integer NOT NULL,
    reasoning_tokens integer NOT NULL,
    cost_eur numeric NOT NULL
);
ALTER TABLE agent_usage ADD COLUMN IF NOT EXISTS web_searches integer NOT NULL DEFAULT 0;
CREATE TABLE IF NOT EXISTS agent_fact_sheets (
    scope text NOT NULL,
    job_id text NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now(),
    model text NOT NULL,
    complete boolean NOT NULL,
    note text,
    fact_sheet jsonb,
    cost_eur numeric NOT NULL,
    PRIMARY KEY (scope, job_id)
);
