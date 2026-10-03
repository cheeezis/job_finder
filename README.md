# Job Finder

A personal job-search assistant for entry-level IT roles in Germany. It collects
listings from job boards, open feeds and company career pages, merges listings of
the same job, filters out clear misfits with transparent rules and lets an LLM
agent write a short fact sheet for the promising ones, within a hard cost limit.
A small web app supports the review and tracks applications.

The user interface and the detailed documentation are in German, because the tool
serves a job search in Germany. This page is the English overview.

## Highlights

- **LLM agent with guardrails:** a LangGraph graph with a LangChain model on Azure
  OpenAI (Responses API with web search) writes a structured fact sheet per job:
  seven traffic-light lines, a verdict and a short reason. Its tools only read, job
  ads and web pages count as material rather than instructions, and only links the
  model actually saw survive as sources.
- **Cost control in layers:** every model and tool call passes a cost guard backed
  by a ledger in PostgreSQL, with limits per job, day and month that fail closed.
  In Azure, a throttled model deployment, a token alert and a monthly budget add to
  it. A fact sheet costs a few cents.
- **Azure without account keys:** storage and the model accept only Entra ID,
  secrets come from Key Vault through a managed identity, and the review app sits
  behind Entra ID sign-in for one account. It becomes public only after its
  sign-in is configured, and every deploy checks that it serves nothing without
  sign-in.
- **Infrastructure as code and gated delivery:** Terraform manages all resources.
  GitHub Actions sign in to Azure with OIDC, using separate identities for plan,
  build and apply; production deploys wait for manual approval and roll out only
  the current `main`.
- **Supply chain:** builds install the exact versions from `uv.lock`, actions are
  pinned to commit SHAs, and the image runs as a non-root user without pip or uv.
  CI scans the image and the Terraform code with Trivy; a new Terraform finding
  fails the pull request, and the accepted ones are documented with a reason.
  CodeQL analyses the code, and Dependabot proposes updates every week.
- **Careful data handling:** PostgreSQL with advisory locks and consistent
  snapshot reads; a source that fails never wipes the listings it found before.
- **Observable agent:** every job leaves a JSON log line and an OpenTelemetry
  trace in Application Insights (run → job → model and tool calls) with ids,
  tokens, cost and the verdict, but never profile, prompt or ad text; a test
  checks that. Ingestion needs Entra ID, keeps 30 days and is capped per day.
- **Tests:** 500+ Python tests that run in CI against a real PostgreSQL with a
  coverage report, including boundary tests for the app role's database rights
  and the worker lock; Playwright browser tests click through the review on the
  demo data; Pyright checks the whole application package; frontend tests and
  Ruff for lint and formatting.
- **Measured quality:** an eval harness (`python -m evals`) runs the agent and a
  one-call baseline on 24 synthetic job ads with known verdicts, including two
  prompt-injection cases. Latest run, two passes: 44/48 correct verdicts for the
  agent and 42/48 for the baseline, no invented money amounts, about half a cent
  per fact sheet ([report](evals/results/2026-10-01-1451-synthetisch.md)). Two
  cases fail in every pass and are kept as known weaknesses: a pre-sales role and
  a foreign employer that only says "Germany (remote)". A second run against the
  owner's own decisions stays local.

## Screenshots

The review app with the made-up [demo data](#demo-with-made-up-data); the
interface is German.

| Reviewing a job | Waiting list | Applications |
| --- | --- | --- |
| ![A job card with the prefilter score, the agent's fact sheet and the decision buttons](docs/images/review.png) | ![A second job at a company with an open application, on the waiting list](docs/images/waiting-list.png) | ![Open applications with key figures](docs/images/applications.png) |
| Prefilter score, the agent's fact sheet with traffic lights, verdict and sources, and the decision. | A second job at a company with an open application: the card names it, and the job can wait. | Open and closed applications, their history and key figures. |

## Architecture

```mermaid
flowchart LR
    SRC["Job boards, feeds,<br/>career pages"]
    subgraph AZ["Azure"]
        JOB["Finder<br/>Container Apps job, twice a day"]
        AGENT["Fact-sheet agent<br/>LangGraph + LangChain"]
        AOAI["Azure OpenAI<br/>gpt-5-mini + web search"]
        DB[("PostgreSQL")]
        APP["Review app<br/>Container App"]
        BLOB[("Blob Storage<br/>documents")]
        KV["Key Vault"]
    end
    LOCAL["Local job<br/>Docker, daily"]
    USER["Browser"]
    DISCORD["Discord"]

    SRC --> JOB
    SRC --> LOCAL
    JOB --> DB
    LOCAL --> DB
    JOB --> AGENT
    AGENT <--> AOAI
    AGENT <--> DB
    JOB --> DISCORD
    USER -- "Entra ID sign-in" --> APP
    APP <--> DB
    APP <--> BLOB
    KV -. "secrets via managed identity" .-> JOB
    KV -.-> APP
```

A finder run works in five steps:

1. Sources deliver listings. If more than half of them fail, the run stops before
   it replaces any stored results.
2. A rule-based prefilter checks location, remote share, experience, employment
   type, travel and IT fit. Its score sorts; it does not predict suitability.
3. Promising candidates get their detail pages, and listings of the same job
   become one card, also across portals and runs.
4. Results, the job memory and the Discord queue are stored in PostgreSQL; new
   matches go to Discord.
5. The agent writes fact sheets for undecided jobs that are entry level or score
   above 50, best first, until a limit is reached.

### How the agent writes a fact sheet

```mermaid
flowchart TD
    PICK["Undecided jobs, entry level or score above 50<br/>best first"]
    RUN{"Money left today and this month,<br/>run under 35 minutes?"}
    MODEL["Model node: gpt-5-mini<br/>web search only while the job's search budget lasts"]
    LEDGER[("Cost ledger<br/>PostgreSQL")]
    TOOLS["Tool node: past_decisions<br/>earlier decisions on similar jobs"]
    CHECK["Check the answer<br/>schema, drop links the model never saw"]
    SAVE[("Fact sheet<br/>shown in the review")]
    ABORT[("Aborted job with its reason<br/>shown in the review")]
    STOP["Run stops<br/>Discord warning"]
    DONE["Delete the stored responses in Azure<br/>log line and trace: ids, counts, verdict"]

    PICK --> RUN
    RUN -- yes --> MODEL
    RUN -- no --> STOP
    MODEL -- "books every call" --> LEDGER
    MODEL -- "asks for a tool" --> TOOLS
    TOOLS --> MODEL
    MODEL -- "answers" --> CHECK
    MODEL -- "job limit: calls, cost or tool calls;<br/>answer incomplete or rejected" --> ABORT
    CHECK -- valid --> SAVE
    CHECK -- invalid --> ABORT
    SAVE --> DONE
    ABORT --> DONE
    DONE -- "next job" --> RUN
```

Model and tool node form a LangGraph graph per job (`job_finder/agent/runner.py`).
Before every model or tool call the cost guard checks the limits; a limit of one
job ends only that job, while a used-up day or month, an unusable ledger or an
unreachable model stops the run. The decision stays with the user: the sheet
suggests a verdict with traffic lights and sources, but no score.

Two sources run in a daily local job in Docker, with the same image and against
the same database. Every push to `main` runs the tests, builds the image and,
after approval, applies Terraform and rolls the image out to the finder job and
the review app.

## Tech stack

| Area | Technology |
| --- | --- |
| Language | Python 3.11+ (CI tests 3.11 and 3.13), dependencies locked with uv |
| AI agent | LangGraph, LangChain (`langchain-openai`), Azure OpenAI `gpt-5-mini` |
| Data | PostgreSQL 18 with psycopg 3, validated Alembic migrations, Azure Blob Storage |
| Web app | Python standard-library HTTP server, vanilla JavaScript |
| Cloud | Azure Container Apps (job and app), Database for PostgreSQL Flexible Server, Key Vault, Container Registry, Monitor |
| Infrastructure | Terraform (`azurerm`, `azapi`), Docker |
| CI/CD | GitHub Actions with OIDC and environment approval |
| Quality | pytest with coverage against PostgreSQL, Playwright, Pyright, `node:test`, Ruff, CodeQL, Trivy, tflint, Dependabot |

## Quickstart (local)

Requirements: Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) (for example
`python -m pip install --user uv`) and Docker; Node.js 18 or newer only for the
frontend tests. From the repository root:

```powershell
uv sync
uv run python scripts/setup_postgres.py
docker compose --env-file .env.postgres up -d --wait
uv run python -m job_finder.db init
uv run python run_finder.py
uv run python -m job_finder.review
```

`uv sync` installs exactly the versions in `uv.lock` into `.venv`. The review
opens at `http://127.0.0.1:8765`. Without `user_settings.local.yaml`
the anonymised example settings apply. The agent stays off until
`agent.enabled: true` is set and an Azure OpenAI endpoint and a profile are
configured.

## Demo with made-up data

The screenshots come from a demo you can run yourself, after the first three
commands of the quickstart:

```powershell
uv run python scripts/demo_data.py --serve
```

The script fills a separate local database `jobfinder_demo` with the fictional
job ads from the eval cases, a few decisions and applications and three fact
sheets the agent wrote for them, then opens the review at
`http://127.0.0.1:8770`. The real prefilter rates the ads; nothing calls the
model. Each run empties the demo database first, and the script refuses any
database server that is not local.

## Documentation (German)

- [Bedienung](docs/bedienung.md): features, sources, review workflow, rules and
  the agent's settings
- [Betrieb](docs/operations.md): database, backups, Azure, the local job and
  access paths
- [Entwicklung](docs/development.md): structure, data flow of a run, adding a
  source and code style

## License

MIT, see [LICENSE](LICENSE).
