# Usage, Rules and Operation

The user guide to the Job Finder. The [README](../README.md) gives an overview
with the architecture; [Operations](operations.md) and the
[developer guide](development.md) cover running and developing it.

A Python job finder for entry-level IT positions. It collects listings from
several sources, merges listings of the same job, filters out clear misses with
rules and supports the review up to the application. It runs locally or in its
own Azure subscription (see [Operation](#operation)).

Only compact job cards and run statistics leave the system, to Discord, when a
webhook is set. With the AI agent switched on, the listing, the personal
profile and earlier decisions also go to the language model in the own Azure
subscription, and its search queries go to Bing search.

The review interface is German, so its labels are quoted in German below, with
an English gloss where the meaning is not obvious.

## Features

- Job boards, open feeds and selected career pages; manual import of a single
  listing by URL
- one job model, deduplication across sources and a memory of known, decided
  and inactive jobs
- a rule-based prefilter for location, remote share, experience, employment
  type, travel and IT fit; junior-hybrid special cases and international jobs
  can be switched on in the review
- review with interesting, inquiry, waiting list, ignore and apply, plus an own
  note; if an application at the same company is already running, the card
  names it with its status
- waiting list for jobs at a company where another application is still open:
  the job stays visible under the status filter "Warteliste" (waiting list), and
  once that application has ended (rejection, no response, withdrawn) the card
  says "jetzt entscheiden" (decide now)
- optional AI agent (LangGraph and LangChain on Azure OpenAI) that writes a fact
  sheet with traffic lights, verdict and short reason for prefiltered jobs,
  limited by a multi-level cost guard
- application overview with timeline, interview dates, salary expectation
  (entered per month or year, stored as annual gross) and statistics
- Discord cards for new jobs and a run summary; source failures stay isolated
  and are reported

The prefilter score is a sorting aid, not a prediction of fit. The decision
stays with the user.

## Sources

| Group | Sources |
| --- | --- |
| Job boards | Arbeitsagentur, StepStone, get-in-IT |
| Feeds and aggregators | Arbeitnow, GermanTechJobs, Himalayas, Jobicy, Remotely, Startup Jobs, StudySmarter |
| Direct career pages | Compose IT, bytewerk, RhönEnergie, JUMO, EDAG, CSS, Proemion, NETHINKS |
| Own entries | manual import of a public job URL |

Startup Jobs runs only with `STARTUP_JOBS_API_KEY`. When a source returns only
partial results, for example because of rate limits, the run reports it in the
console, the log and Discord. When more than half of the sources are unusable,
it stops and leaves the previous state unchanged.

### Fair fetching

The sources are fetched by a recognisable client (`job-finder/0.1`): one shared
HTTPX client with fixed timeouts (`job_finder/http.py`). When a request fails
briefly (timeout, connection error, HTTP 5xx), at most two more attempts follow
after 2 and 4 seconds. HTTP 429 is retried only when the server names a wait of
up to 60 seconds in `Retry-After`; HTTP 403 and other client errors never. Up to
four sources run at the same time, each with its own pauses; still only one
request at a time goes to the same host.

StepStone only requests addresses its robots.txt allows: search pages without
parameters, so only the first result page per search term and place, without a
radius. In return StepStone searches with all general search terms. Two
requests are 1.5 seconds apart; if HTTP 403 or 429 persists, the source stops
and uses its last cache instead of working around the block. Exception:
Remotely checks linked LinkedIn originals for closed jobs with a browser user
agent, at most once a day per job.

## Setup

```powershell
uv sync
Copy-Item user_settings.example.yaml user_settings.local.yaml
```

It needs Python 3.11 or newer, [uv](https://docs.astral.sh/uv/) (for example
via `python -m pip install --user uv`) and PostgreSQL
([database setup](operations.md#local-database)). `uv sync` creates `.venv`
and installs exactly the versions in `uv.lock`.

`user_settings.local.yaml` (ignored by Git) holds the search location, radius,
commuter places, domain keywords and the agent's limits. Optionally
`search.terms`, `search.stepstone_terms` and `search.commuter_terms` replace the
built-in search terms, and `sources.companies` chooses the career pages of
single companies; the example file shows both. Without the file the anonymised
example configuration applies. A finder run checks the settings before it asks
any source and stops on an error with the name of the field. A running review
picks up changes only after a restart. In Azure the content comes from the Key
Vault secret `JobfinderUserSettings`, which is set again after changes:

```powershell
& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -X utf8 -IBm azure.cli keyvault secret set --vault-name <key-vault> --name JobfinderUserSettings --file user_settings.local.yaml --encoding utf-8 --output none
```

On Windows, `az keyvault secret set --file` otherwise reads the file in the
Windows encoding (cp1252) instead of UTF-8, even with `--encoding utf-8`:
"München" turns into "MÃ¼nchen" in the secret. The call above starts the Azure
CLI's Python in UTF-8 mode; `az.cmd` starts it isolated and therefore ignores
an environment variable such as `PYTHONUTF8`. On Linux and macOS
`az keyvault secret set` with the same arguments is enough. `--output none`
keeps the CLI from printing the stored content.

`az keyvault list -g rg-jobfinder --query [].name -o tsv` names the Key Vault.
Every finder run states at its start where its settings come from.

Optional are `DISCORD_WEBHOOK_URL` for Discord and `JOBFINDER_REVIEW_HOST`
(the review's hostname) for direct links in the cards. Secrets do not belong in
YAML files or the repository.

## Use

```powershell
.\.venv\Scripts\python.exe run_finder.py
review_jobs.bat
```

`run_finder.py` searches, scores and sends new matches to Discord, if
configured. `review_jobs.bat` (or `python -m job_finder.review`) starts the
interface at `http://127.0.0.1:8765`: a landing page with the manual import,
`/review` for reviewing and `/applications` for applications and statistics.
Changed scoring rules take effect from the next finder run.

- **Review:** The filter "Neu" (new) shows every job not yet decided,
  regardless of the run that found it. International and junior-hybrid jobs
  have their own filters, off by default. Cards are sorted by the agent's
  verdict, and by prefilter score within the same verdict.
- **Manual import:** Explicitly added listings stay visible even as
  international or junior-hybrid special cases. After the import the matching
  card opens directly.
- **Another listing for an application:** If a card belongs to an application
  already saved, choose "Bestehender Bewerbung zuordnen" (attach to existing
  application) and the application. This also works with differing company
  names, for example with recruiters. The application keeps its employer,
  timeline, salary and documents; the additional listing's links appear there
  under "Links".
- **Note:** "Meine Notiz" (my note) records the reason for a decision (up to
  2,000 characters) and is saved when leaving the card. For similar jobs the
  agent reads its first 300 characters.
- **One card per job:** Listings with the same title and company form one card
  with all places and links, across boards and runs; one decision applies to
  all. A job already decided takes over a new listing only without a new place
  or when both are fully remote.
- **Interviews:** The card highlights the next interview; once it is over, it
  shows "Letztes Gespräch" (last interview) until a new event is entered.
  "Gespräch absagen" (cancel interview) ends the application as withdrawn; that
  counts neither as a rejection nor as no response.
- **Order of applications:** Upcoming interviews come first, the next date at
  the top. Past interviews follow, the most recent first. The other open
  applications come below, still by application date with the newest first.
- **Links:** "Links" on the card lists every listing the finder found for the
  job, also on other boards, and the pages the agent opened for the fact sheet
  (such as the company page or the applicant portal).
- **Company and title:** If the company is missing or the title is wrong, both
  can be corrected on the card; later runs no longer overwrite them.
- **No response:** If nothing new happens 14 days after the application, a
  response or the last interview, the overview shows "Keine Rückmeldung" (no
  response); a later event reopens the application. The status can also be
  entered by hand. The response rate counts only completed applications.

## Operation

- **Local:** Finder and review run on the own computer, PostgreSQL in Docker
  Compose, documents under `data/internal/application_documents`. Every finder
  run first writes a rotating backup.
- **Azure:** The finder runs as a Container Apps job at 06:00 and 16:00 UTC,
  without StepStone and Remotely. The review is a container app behind a
  Microsoft Entra ID sign-in for the own account; every deploy checks that it
  serves nothing without sign-in. Data lives in Azure PostgreSQL, documents in
  Blob Storage, secrets in Key Vault; instead of ZIP backups, point-in-time
  restore and blob versioning protect the data.
- **Hybrid:** StepStone and Remotely return nothing to Azure. A local Windows
  task starts them daily in Docker, with the Azure worker's image and against
  the same database (`scripts/run_local_hybrid.py`); afterwards the agent writes
  the fact sheets for these jobs. Each run replaces only the listings of its own
  boards. The script starts Docker when needed, points the database firewall
  rule at the current IP, writes a log and reports failures to Discord.

[Operations](operations.md) covers setup, backups, Azure and access paths.

## AI agent and cost guard

After every finder run in Azure and in the hybrid run, the agent
(`job_finder/agent/`, a LangGraph graph with a LangChain model on the own Azure
OpenAI) writes a fact sheet for the best waiting jobs: seven traffic-light lines
(status, career entry, technical fit, gaps, home office / location, travel,
salary), up to two extra lines, a verdict and a short reason. It takes
undecided jobs visible in the default review without a fact sheet that are
entry-level positions or score more than 50 points, the best first. It may
search the web and look up earlier decisions with their notes, but it changes
nothing. Listings and web pages are material for it, not instructions, and only
links it actually saw remain as sources. Its standards are in
`job_finder/agent/instructions.py`.

It runs only when `agent.enabled: true` is set, the run knows the model's
address and a profile (`profile.local.yaml`) exists. In Azure Terraform sets
the address and the profile comes from the Key Vault secret `JobfinderProfile`;
the hybrid run takes both from local files (`.env.docker-local`,
`profile.local.yaml`). After profile changes, set the secret again:

```powershell
& "C:\Program Files\Microsoft SDKs\Azure\CLI2\python.exe" -X utf8 -IBm azure.cli keyvault secret set --vault-name <key-vault> --name JobfinderProfile --file profile.local.yaml --encoding utf-8 --output none
```

For the UTF-8 call on Windows see the settings above.

If a fact sheet ends with an incomplete or unusable answer, the next run tries
that job exactly once more; the review says so. A refused request or a reached
per-job limit is not retried, because it would only cost money again.

Every fact sheet remembers what it was based on: the profile (by content,
comments do not count), the rules, the listing, the model with its reasoning
effort and the agent's flow. If any of it has changed since, the review shows
"Veraltet" (outdated) with the changed parts; nothing is evaluated again
because of it. The button "Neu bewerten" (evaluate again) on the fact sheet
marks the job, and the next agent run rewrites its fact sheet before all
others, even if the job is already decided. The review itself never calls a
model.

Every run reports in its section "Steckbriefe (Agent)" why the agent did not
run, or how many fact sheets are done, aborted, open or outdated and what the
day has cost. If the agent aborts or stops before the last job, for example at
the daily limit, a warning goes to Discord as well. `agent.reasoning_effort`
sets the reasoning effort (default `medium`).

**Cost guard:** Before every model and tool call, the cost guard
(`job_finder/agent/cost_guard.py`) checks the switch, the stored price, the
cost ledger `agent_usage` with its daily and monthly limit (German time, all
runs together) and the job's limits for cost, calls and paid web searches
(`job_max_web_searches`, default 3). A reached job limit ends only that job, a
daily or monthly limit ends the agent for the run; a limit can be exceeded by at
most one call. On HTTP 429 it waits 5 to 65 seconds and tries up to three more
times. The limits are in the `agent` section of the settings, the defaults in
`user_settings.example.yaml`. Invalid values switch the agent off, and fixed
ceilings in the code (5 € per day, 50 € per month) catch typos.

In Azure a throttle on the model deployment (`infrastructure/openai.tf`), a
token alert and a monthly budget with email warnings
(`infrastructure/monitoring.tf`) apply as well. The model account has no API
keys. Only the worker, the hybrid run and the own account may call it, each
with a role that cannot change the deployment.

## Rules at a glance

The [developer guide](development.md#data-flow-of-a-finder-run) describes the
course of a run.

- **Age:** Automatically found listings whose known publication date is more
  than 60 days ago drop out; a missing date alone does not. Old listings
  imported by hand stay reviewable with a warning.
- **Notifications:** New jobs can go to Discord. Later text changes trigger
  neither a new message nor a new "Neu".
- **Offline check:** Interesting and waiting jobs stay marked even without a
  search hit. If they are missing in a run whose sources were all complete, the
  run checks their URLs. Only when all of them are clearly closed (HTTP 404/410
  or a closing notice) does the job switch to "Nicht interessant" (not
  interesting), with date and reason; applications are left alone. The review
  loads these cards only when the filter "Alle Status" (all statuses) or "Nicht
  interessant" is chosen or a link points to such a job; the usual list stays
  small that way.
- **Caches:** Details count as fresh for seven days. On a network error an
  entry at most 14 days old may appear as a marked fallback. A partly failed
  search segment sets no jobs inactive.
- **Sources:** Arbeitnow loads the original listing only for the known
  placeholder text; review and Discord then prefer its URL. Remotely takes only
  listings of the last seven days and leaves out LinkedIn originals that no
  longer accept applications. get-in-IT and StudySmarter load detail pages only
  after the first, generous prefilter; StudySmarter searches within the radius
  and Germany-wide for remote jobs. Salary ranges from GermanTechJobs count as
  euros gross per year.

## Tests and development

```powershell
uv sync
uv run python scripts/test_postgres.py
uv run ruff check .
node --test tests/frontend.test.cjs
```

The Python tests need their own test database
([Operations](operations.md#local-database)), Node.js 18 or newer only the
frontend tests. The [developer guide](development.md) explains structure, data
flow, new sources and style rules. GitHub Actions check every pull request;
after a merge to `main` they roll out infrastructure and image only after
manual approval.

```text
job_finder/            core logic, review and applications
job_finder/sources/    source adapters
job_finder/agent/      AI agent with cost guard
scripts/               setup, tests and the local hybrid run
infrastructure/        Terraform for Azure
docs/                  usage, operations and development
tests/                 automated tests
run_finder.py          entry point for a finder run
review_jobs.bat        starts the local interface
data/                  local run data (not versioned)
```

## License

MIT, see [LICENSE](../LICENSE).
