# 0001: One Python codebase on Azure Container Apps

- Status: accepted
- Date: 2026-09 (cloud move), recorded 2026-10-08

## Context and problem

The finder started as a script on one computer. It should run twice a day
without that computer being on, and the review should be reachable from
anywhere, for one person, at a cost of a few euros a month. The work splits into
a batch part (collect, score, notify, write fact sheets) that runs for minutes
and a small web app that is used now and then.

## Considered options

1. A virtual machine with cron and a web server.
2. Several services (sources, scoring, agent, review) with queues between them.
3. One codebase in one image, run as a scheduled Container Apps job and as a
   Container Apps app, both scaling to zero.
4. Azure Functions for the batch part and a static web app for the review.

## Decision

Option 3. `run_finder.py` and `job_finder.review` share the package
`job_finder` and the image; the worker job starts on a schedule, the review app
scales to zero between visits. The modules keep clear boundaries (sources,
matching, workflow, persistence, agent), but they are deployed together.

## Consequences

- One build, one image, one version: worker and review cannot drift apart, and
  a rollback is one image reference.
- No servers to patch and almost no cost while idle; the review has a cold start
  of a few seconds after a pause.
- A run is limited to one hour and cannot share memory with the review; all
  state goes through PostgreSQL.
- Splitting a module out later stays possible, because the boundaries exist in
  the code already, but nothing forces it now.

## Revisit when

- Parts need to scale independently, for example many users or a much larger
  agent workload.
- A run regularly comes close to the one-hour limit.
- The cold start of the review becomes a real nuisance.
