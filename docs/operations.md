# Operations

Database setup, backups, Azure, the local hybrid run and the access paths.
[Usage](usage.md) covers using the finder, the [developer guide](development.md)
its structure and development.

PostgreSQL is the only runtime store for jobs, decisions, application
timelines, recommendations, Discord delivery state, source caches and the
agent's fact sheets. Document contents stay separate files (local or in Blob
Storage); their metadata and assignment are in PostgreSQL.

## Local database

From the repository root in PowerShell:

```powershell
uv sync
uv run python scripts/setup_postgres.py
docker compose --env-file .env.postgres up -d --wait
uv run python -m job_finder.db init
```

The setup command creates a random password in `.env.postgres` once. The file
is excluded from Git and Docker builds. Existing settings are not overwritten.
The application loads it automatically; environment variables already set take
precedence.

The database is reachable only at `127.0.0.1:55432`. The separate Compose name
`jobfinder` avoids conflicts with other projects. The Docker volume
`jobfinder_postgres_data` (mounted at `/var/lib/postgresql`) survives replacing
the container. `docker compose down` keeps the volume; `down -v` would delete
it and is not part of the normal routine.

Worker and review do not connect as the admin user `jobfinder` but through the
role `jobfinder_app` with restricted rights (no access to `schema_version` or
`alembic_version`). Set it up once:

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py
```

The command creates the role or updates its rights, points
`JOBFINDER_DATABASE_URL` in `.env.postgres` at the new role and can be repeated
any number of times. It never prints passwords.

For the tests, create the separate test database once:

```powershell
docker compose --env-file .env.postgres exec postgres createdb -U jobfinder jobfinder_test
```

The test runner `scripts/test_postgres.py` requires
`JOBFINDER_TEST_DATABASE_URL` with its own database name ending in `_test`,
refuses the configured production name and empties only the application tables
of this test database.

## Data, documents and caches

- `job_state`: fixed columns for ID, title, company, status, activity, missed
  runs, salary expectation, personal rating and note; further attributes as
  JSONB.
- `workflow_history`: ordered events with status, date and interview time.
- `application_documents`: metadata and references, no document bytes.
- `jobs` / `recommendations`: one row per snapshot entry with fixed core
  columns; several source representations of a job ID are kept, the position is
  part of the key.
- `notifications`: pending and already sent entries, kept apart.
- `manual_sources`: permanently added URLs and their source data; an outdated
  cache write does not remove them.
- `source_cache`: individually stored cache contents as JSONB with their write
  time.
- `datasets`: small headers and format details for the reconstructed views.
- `agent_usage` / `agent_fact_sheets`: the agent's cost ledger and fact sheets.

Single review changes lock the affected job; status and timeline are saved
together. Finder reconciliations lock the changes to the job stock and write
back only changed records; a lock prevents two finders at once. The manual
import writes sources, memory and results in one transaction, with the HTTP
fetch before it. The finder writes memory, confirmed closings, both result
views and Discord jobs together in one transaction. Sending and acknowledging
follow after the commit. The existing table `notifications` holds the card
data, a stable event key and the delivery state; this outbox needed no schema
change.

Open jobs are retried in the next successful finder run, even without a new
find. Source failures and split schedules do not remove them. A newly set
review decision or a prefilter exclusion can discard the job. Successful
partial deliveries are saved one by one. An abort after Discord succeeded but
before the acknowledgement can cause a duplicate message. The run summary is
still sent straight away. Details and abort tests:
[developer docs](development.md#publishing-and-discord-outbox).

Documents are kept under `data/internal/application_documents` by default;
`JOBFINDER_DOCUMENTS_DIR` can point at another permanent location. With
`JOBFINDER_DOCUMENTS_BACKEND=blob` they live in the blob container named by
`JOBFINDER_STORAGE_ACCOUNT` and `JOBFINDER_STORAGE_CONTAINER`; worker, review
and hybrid run work that way.

When an application starts, the review first writes the documents under new
keys and then records them in a short transaction; if that fails, it deletes
the new files again. Only a crash exactly in between leaves a file without a
reference. The following command lists such files older than 24 hours; it
deletes only with `--delete`. Against Azure, use the same environment variables
as the review (`JOBFINDER_DOCUMENTS_BACKEND=blob`, account, container) and a
database URL with read access:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db orphaned-documents
.\.venv\Scripts\python.exe -m job_finder.db orphaned-documents --delete
```

Automatic cache entries not saved again or changed for at least 30 days can be
pruned; writing them again unchanged does not reset the period. Manual sources,
jobs, applications, delivery state and documents are not affected:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db prune-cache --days 30
```

## Backup and restore

```powershell
.\.venv\Scripts\python.exe -m job_finder.db backup
```

The ZIP holds a consistent application state including the agent's fact sheets
and cost ledger, all referenced document files and checksums. Every local
finder run also writes such a backup first; the rotation keeps seven archives.
In containers (Azure worker, hybrid run) it is skipped
(`JOBFINDER_SKIP_RUN_BACKUP=1`) because their file system does not outlive the
run. There the server's point-in-time restore (14 days) and the versioning with
soft delete in Blob Storage (14 days) protect the data.

A restore needs an empty, separately configured database and an empty document
target. First point `JOBFINDER_DATABASE_URL` at that target:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db restore "PATH_TO_BACKUP.zip" --documents-dir "PATH_TO_EMPTY_DOCUMENT_FOLDER"
```

The application verifies the checksums, compares the data written back and
overwrites nothing. This is an application backup, not a replacement for the
Azure server backups or for backups of roles or infrastructure.

The joint plan for the database and historical document versions is in
[Backup and restore](backup-recovery.md). A drill with made-up data on the
local PostgreSQL test container runs with `python scripts/restore_drill.py`; it
creates and removes its own test databases and checks the document references.
It does not replace the acceptance of Azure PITR and blob restore; that passed
on 05.10.2026 ([result](backup-recovery.md#result-of-the-azure-drill)).

## Azure

`infrastructure/postgresql.tf` manages the production server: a PostgreSQL
flexible server (`B_Standard_B1ms`, 32 GiB, France Central), the database
`jobfinder`, the firewall rules and `require_secure_transport`. In the `split`
mode, worker, review and hybrid run use their own restricted database roles;
the cloud URLs are in the worker and review database secrets, which each
component reads with its own managed identity. Only the earlier `legacy` mode
shares `jobfinder_app` and `JobfinderDatabaseUrl`. Rights and phases:
[runtime access](runtime-access.md). The separate switch to token sign-in is
described in [database sign-in with Entra](database-auth.md); password
authentication stays the default. The following steps concern administrative
access from the own computer.

The admin password and the IP to allow are kept locally in
`infrastructure/postgres.auto.tfvars.json`, further private values in
`infrastructure/terraform.tfvars` (both excluded from Git). Plan and apply from
the repository root:

```powershell
terraform -chdir=infrastructure fmt -check
terraform -chdir=infrastructure validate
terraform -chdir=infrastructure plan "-out=postgresql.tfplan"

$env:TF_VAR_postgres_admin_password = (Get-Content infrastructure/postgres.auto.tfvars.json -Raw | ConvertFrom-Json).postgres_admin_password
try {
    terraform -chdir=infrastructure apply "postgresql.tfplan"
} finally {
    Remove-Item Env:TF_VAR_postgres_admin_password -ErrorAction SilentlyContinue
}
```

The firewall rule `local-review` allows exactly one address, also for the
hybrid run. Because the home IP changes often, `scripts/run_local_hybrid.py`
points the rule at the current public IP (looked up via `api.ipify.org`)
through the signed-in `az` session before every run. Before a local review
against Azure, `uv run python scripts/run_local_hybrid.py --allow-ip` is
enough. Terraform creates the rule with `postgres_client_ipv4` and ignores its
address afterwards. A read-only access test with certificate verification that
changes nothing:

```powershell
.\.venv\Scripts\python.exe scripts/check_azure_postgres.py
```

Create the shared login only for the initial setup in `legacy` mode. Separate
worker, review and hybrid logins, the rights matrix and the two-stage procedure
are in [runtime-access.md](runtime-access.md). After `split`, use their
maintenance path instead of running the old app role command below again.

```powershell
.\.venv\Scripts\python.exe scripts/create_app_role.py --azure
```

New tables and columns are created only by explicit Alembic migrations through
`job_finder.db migrate`; `init` is a compatible alias for the same path. Worker
and review never change the schema. An empty database gets the schema through
the unchanged baseline revision `0001_baseline` and the following revisions up
to `head`. An existing database without an Alembic version is adopted only
after the complete baseline structure and the earlier version marker have been
checked. Deviations abort; missing tables or columns are not repaired
automatically on adoption. The known old import log table `migration_runs` may
exist with its original structure, which is checked as well. It and its data
are kept; fresh databases no longer get it since the data migration has ended.

`schema-status` checks structure and migration state with the admin login,
technically read-only. It shows `empty`, `legacy`, `current` or `outdated`, the
current revision and the target `head`, without row counts or connection
details. `migrate` performs the check, the baseline stamp, the upgrade and the
protection of the version tables in one transaction under the existing schema
lock. Inherited table rights are revoked for the version tables as well;
application tables keep their rights.

Before a production migration, back up the data and check `schema-status`. On a
deviation, investigate the cause and the actual data; no unchecked
`alembic stamp`. The baseline keeps the earlier `schema_version = 2` and all
existing data. A rollback to the previous app image needs no schema downgrade;
a baseline downgrade is refused on purpose because it would delete all data.
New revisions need their own compatibility and return plan.
`0003_agent_fact_sheet_state` only adds columns with defaults to
`agent_fact_sheets` (retry, attempts, basis, outdated parts): the previous image
keeps writing as before, so an image rollback needs no downgrade. The migration
runs before the merge of the matching release, because the new image reads the
columns.
`0004_review_lookup_indexes` creates two GIN indexes on `job_state.extra`
(listing URLs and linked IDs) that the review uses to find remembered jobs of a
listing; without them PostgreSQL reads the whole table for every job list. The
structure check expects exactly these two definitions. This migration also runs
before the merge of the matching release.
`0005_job_listings` (F17, stage 1) creates the tables `job_listings` (a job's
listings: position, URL, source) and `job_links` (linked jobs) and the columns
`first_seen_at`, `last_seen_at` and `locations` on `job_state`, takes over the
values from `job_state.extra` and gives the tables the same rights as
`job_state`. Until their removal in a later stage the JSON fields stay the
source: every save derives tables and columns from them again, so an older image
keeps running unchanged. Deviations, for example after a rollback, are reported
by `listing_drift` in the `run_summary` line; check and repair:

```powershell
.\.venv\Scripts\python.exe -m job_finder.db listings-drift
.\.venv\Scripts\python.exe -m job_finder.db listings-drift --repair
```

Migrations run separately from the deploy on purpose (see
[Deploy and rollback](#deploy-and-rollback)).

After an approved schema change, the command runs once against Azure, with the
connection details from `.env.postgres-azure` set only for this call. `check`
then counts the rows as the app role and so tests the data access:

```powershell
$azure = Get-Content .env.postgres-azure -Raw | ConvertFrom-StringData
try {
    $env:JOBFINDER_ADMIN_DATABASE_URL = $azure.JOBFINDER_ADMIN_DATABASE_URL
    $env:JOBFINDER_DATABASE_URL = $azure.JOBFINDER_DATABASE_URL
    .\.venv\Scripts\python.exe -m job_finder.db schema-status
    .\.venv\Scripts\python.exe -m job_finder.db migrate
    .\.venv\Scripts\python.exe -m job_finder.db check
} finally {
    Remove-Item Env:JOBFINDER_ADMIN_DATABASE_URL, Env:JOBFINDER_DATABASE_URL -ErrorAction SilentlyContinue
}
```

The server also runs between finder runs (prices under [Costs](#costs)).
Pausing saves only compute, not storage; a stopped server starts again by
itself after seven days:

```powershell
$jobfinderPostgresServer = terraform -chdir=infrastructure output -raw postgres_server_name
az postgres flexible-server stop --resource-group rg-jobfinder --name $jobfinderPostgresServer
# To continue working:
az postgres flexible-server start --resource-group rg-jobfinder --name $jobfinderPostgresServer
```

Server and storage account carry a delete lock (`no-delete`): every deletion,
also through `terraform destroy`, fails until the lock has been removed
deliberately and locally with Owner rights. `terraform destroy` affects the
whole infrastructure in the folder and needs a verified backup first.

What carries data or the access protection is also protected by Terraform with
`prevent_destroy`: server and database, storage account and document container,
the Key Vault, and the review app with its sign-in configuration. A plan that
would delete or recreate one of them aborts before the apply, also
`terraform destroy`; a deliberate teardown first needs a code change.

A newly created review is at first reachable only internally;
`review_public` makes it public only once the sign-in is in place. For a
deliberate rebuild, remove `prevent_destroy` from the app and the sign-in
configuration locally and replace the three parts together, with the same local
values as above:

```powershell
terraform -chdir=infrastructure apply -replace=azurerm_container_app.review -replace=azapi_resource.review_auth -replace=azapi_resource_action.review_public
```

## Deploy and rollback

A deploy after a merge runs in three stages:

1. **Plan:** The job „Release plan" creates the Terraform plan against the real
   Azure state and stores it privately in the state container
   (`release-plans/<commit>`). Plans can contain secret values; the run's
   summary therefore names only resources and actions. The environment
   `production-plan` is limited to `main` and needs no approval.
2. **Approval and apply:** After the approval in `production`, the apply
   applies exactly this plan, after checking its checksum. An outdated plan
   (state changed since) aborts; then start the run again. A run for an older
   commit than the current `main` aborts too. The plan is deleted afterwards.
3. **Roll out and verify:** The summary notes the previous image under
   „Rückweg" (way back). Then the job rolls out the new image by digest, checks
   that the review answers only with 302 (redirect to the sign-in) or 401
   without sign-in, and waits until worker and newest review revision run the
   new image and the revision is healthy
   ([verify_rollout.sh](../.github/scripts/verify_rollout.sh)).

**Rollback:** Under Actions, start the workflow „Rollback" from `main` and enter
the image noted under „Rückweg" (`…/jobfinder@sha256:…`). It also runs only
after approval in `production`, resets only the image of worker and review and
runs the same checks. Terraform and database stay unchanged.

That works only as long as the schema fits the old image. Migrations are
therefore additive (new tables and columns, nothing removed) and run before the
deploy, separately, as described above under Azure; cleanup migrations follow
only once no way back to an older image is needed. After an incompatible schema
change an old image is no longer a way back; then the restore from
[Backup and restore](backup-recovery.md) applies.

## Local hybrid run (StepStone/Remotely)

StepStone and Remotely return nothing to Azure; they run once a day through a
local Windows task against the same Azure database
(`scripts/run_local_hybrid.py`). The task starts exactly the image the Azure
worker currently uses: it looks it up through the local `az` sign-in, signs in
to the registry and fetches it with `docker pull`. If Docker does not answer,
the script starts Docker Desktop and stops it again after the run; if it was
running already, it stays on. The app role's database URL is in
`.env.postgres-azure`. `user_settings.local.yaml` and `profile.local.yaml` are
passed to the container as `JOBFINDER_USER_SETTINGS` and `JOBFINDER_PROFILE`;
with the model address from `.env.docker-local` the agent then writes the fact
sheets of the new jobs. A native Windows connection at times returned outdated
reads from Azure; the container avoids that, the cause is still unexplained.

The output of every run goes to `data/logs/hybrid-<time>.log` (kept for 14
days, without the secret start parameters). If a step fails, for example
because Docker does not start or the `az` sign-in has expired, the script
reports the reason and the log file to Discord (`DISCORD_WEBHOOK_URL` as a user
variable).

The container has neither a managed identity nor an `az` sign-in. Instead there
is a separate service principal with exactly two roles: `Storage Blob Data
Contributor` only on the container `application-documents`
(`infrastructure/storage.tf`) and `Cognitive Services OpenAI User` for the agent
(`infrastructure/openai.tf`). Set it up once:

```powershell
az ad app create --display-name "jobfinder-local-docker"
az ad sp create --id <appId from the previous command>
az ad app credential reset --id <appId> --display-name "local-docker-worker" --years 2
```

The secret is valid for two years and is renewed with the last command. The
values go into `.env.docker-local` (excluded from Git):

```text
AZURE_CLIENT_ID=<appId>
AZURE_TENANT_ID=<tenant>
AZURE_CLIENT_SECRET=<password>
JOBFINDER_REVIEW_HOST=<hostname of the review app, for direct links in Discord>
JOBFINDER_OPENAI_ENDPOINT=<address of the Azure OpenAI account, for the agent>
```

Then enter the service principal's object ID
(`az ad sp show --id <appId> --query id`) as `local_docker_sp_object_id` in
`infrastructure/variables.tf` and apply the role assignments.

## Logs and traces

Every run writes JSON lines with the same `run_id` (`job_finder/console.py`);
in the Log Analytics workspace they are in `ContainerAppConsoleLogs_CL`, column
`Log_s`. The agent adds one `agent_job` line per job: job ID, outcome
(`fertig` done, `abgebrochen` aborted, `gestoppt` stopped), the abort reason as
a fixed word (such as `job_cost`, `incomplete`, `rejected`), the attempt and
whether another follows, the verdict level, model and tool calls, web searches,
tokens, cost and seconds. The most expensive jobs and abort reasons of the last
seven days:

```kusto
ContainerAppConsoleLogs_CL
| where TimeGenerated > ago(7d) and Log_s startswith "{"
| extend e = parse_json(Log_s)
| where e.event == "agent_job"
| summarize Jobs = count(), Cost = sum(todouble(e.cost_eur)), WebSearches = sum(toint(e.web_searches))
    by Outcome = tostring(e.outcome), Reason = tostring(e.reason), Verdict = tostring(e.verdict)
| order by Cost desc
```

The same figures go to Application Insights as traces (`appi-jobfinder`,
`job_finder/telemetry.py`): a tree of `agent_run`, one `agent_job` per job and
below it `model_call` and `tool_call` with their duration. The attributes of
the model calls follow the OpenTelemetry names for generative AI
(`gen_ai.usage.input_tokens` and so on). In the portal, „Transaction search"
shows a run's tree; in the workspace the spans are in `AppDependencies`:

```kusto
AppDependencies
| where TimeGenerated > ago(7d) and Name == "model_call"
| summarize Calls = count(), Median_ms = percentile(DurationMs, 50), P95_ms = percentile(DurationMs, 95),
    Output = sum(toint(Properties["gen_ai.usage.output_tokens"])) by bin(TimeGenerated, 1d)
```

`span()` in `job_finder/telemetry.py` decides what is recorded: only numbers,
booleans and short fixed words; on an error only the exception's type name.
Profile, prompt, listing text, notes, title, company and the model's answers are
left out; a test in `tests/test_agent_runner.py` checks that. Application
Insights accepts data only with Entra ID sign-in (the managed identities of
worker and review), keeps it for 30 days and takes at most 0.1 GB a day.
Without `APPLICATIONINSIGHTS_CONNECTION_STRING`, so locally, in tests and evals,
nothing is sent.

The review sends one span `review_request` per API request with route, status,
duration and `jobfinder.first_request` (the first request after the start, so a
cold start). Below it hang the steps `db_connect` (connection including the
Entra token), `read_recommendations`, `read_memory`, `build_cards`,
`read_fact_sheets` and `read_document`, with row and card counts but without IDs
or contents. Where the job list's loading time goes:

```kusto
AppDependencies
| where TimeGenerated > ago(7d) and Name in ("review_request", "db_connect", "read_recommendations",
    "read_memory", "build_cards", "read_fact_sheets", "read_document")
| summarize Count = count(), Median_ms = percentile(DurationMs, 50), P95_ms = percentile(DurationMs, 95)
    by Name, Route = tostring(Properties["jobfinder.route"]), ColdStart = tostring(Properties["jobfinder.first_request"])
| order by Median_ms desc
```

Every finder run also records itself in the table `runs` (revision
`0006_runs`): at the start with its sources and where it runs (`cloud` from the
worker job, `hybrid` from the local hybrid run, otherwise `local`, set through
`JOBFINDER_RUNNER`), at the end with its outcome (`finished` or `failed`) and
the key figures. So the hybrid runs, which send no logs to Azure, show up as
well. The review's landing page shows the latest run per place (`/api/runs`).

At the end of every run, after the agent, comes a `run_summary` line: duration,
jobs, new jobs in total and per source (`new_by_source`), new review cards,
partly or completely failed sources, sent and failed Discord messages, and the
backlog afterwards: open Discord jobs, the age of the oldest in hours, aborted
fact sheets and those that will be tried again. With Application Insights the
whole run is also a trace `finder_run` with the phases (`collect_sources` with
one `source` step each, `prefilter`, `enrich_details`, `evaluate`,
`availability_checks`, `publish`, `notifications`) and `agent_run` below.

The workbook „Job Finder – Betrieb" (Azure portal, Application Insights
`appi-jobfinder` or Log Analytics, „Workbooks"; Terraform:
`infrastructure/workbooks/operations.json`) shows on one page the run duration,
these key figures, status and hits per source, new jobs per source, the agent's
cost per day and the review's loading times with their steps, for a selectable
period. Workbooks cost nothing; they only read the existing logs.

## Costs

List prices in France Central, without taxes, according to the Azure Retail
Prices API on 30.09.2026:

| Item | Kind | About per month |
| --- | --- | --- |
| PostgreSQL flexible server B1ms with 32 GiB | fixed | 15.55 € (11.90 € compute, 3.65 € storage) |
| Container Registry Basic | fixed | 4.35 € |
| Container Apps (finder job, review) | by use | so far within the free monthly grant |
| Log Analytics, Application Insights, Blob Storage, metric alerts | by use | cents |
| The agent's language model and web search | by use | a few cents per fact sheet, at most 1 € a day and 20 € a month (default limits) |

The server currently runs on a free grant of the subscription; after that the
15.55 € are added. The monthly budget of 25 € in `infrastructure/monitoring.tf`
covers the registry and the agent's limit and then has to rise to a good 40 €,
otherwise it reports an overrun every month. A budget only warns, it stops
nothing; the hard limits are set by the agent's cost guard.

## Access paths

The Container Apps environment runs in the Consumption profile without VNet
integration: worker and review have no fixed outbound IP, and all services are
reachable through their public endpoint. The protection therefore lies in
sign-in, RBAC and TLS, not at the network boundary.

| Resource | Actual access protection |
| --- | --- |
| Review container app | Easy Auth (Entra ID), only the own account; public only after the sign-in configuration, every deploy checks access without sign-in (`review.tf`) |
| PostgreSQL | TLS with `sslmode=verify-full`, restricted database roles with their password or, after the separate Entra acceptance, a token; firewall: Azure services (`0.0.0.0`, any subscription) and `local-review` |
| Blob Storage | RBAC, account keys switched off; write access only to `application-documents` |
| Key Vault | RBAC: in `split` each runtime has only `Key Vault Secrets User` on the secrets it needs; `Secrets Officer` only for the own account |
| Azure OpenAI | RBAC without API keys: worker, hybrid run and the own account with `Cognitive Services OpenAI User` |
| Container Registry | RBAC, `admin_enabled = false` |
| Worker (Container Apps job) | no ingress, outbound only |

GitHub Actions sign in through OIDC without a stored Azure secret, with three
separate identities (`infrastructure/cicd.tf`): apply with `Contributor` and
`Role Based Access Control Administrator` on `rg-jobfinder` plus write access to
the `tfstate` container, build only with `AcrPush` and `Reader` on the registry,
and plan for pull requests read-only.

**Why no VNet and no private endpoints:** Technically it would work, since the
workload profiles environment supports VNet integration in the Consumption
profile too. But the network type can only be set when an environment is
created, so the switch would mean moving to a new environment. A private
endpoint (about 6.30 € a month) would then let the containers reach the database
privately, and the rule for all Azure services could go; access from the own
computer would remain possible through `local-review`. A completely private
server, on the other hand, would shut out the hybrid run and admin access
without a VPN. A NAT gateway (about 31 € a month including the public IP) would
only be needed for a fixed outbound address, for example to limit the firewall
to the containers. For a single user without third-party data and without
compliance requirements, TLS, RBAC and the app role's password carry the
protection. If more users or third-party applicant data were added, this would
be the first thing to change.
