# 0002: Layered cost limits for the AI agent

- Status: accepted
- Date: 2026-09-25 (agent live), recorded 2026-10-08

## Context and problem

The agent calls a paid language model and a paid web search on its own, twice a
day, without anyone watching. The subscription runs on a limited credit: if it
is used up, Azure switches off the whole subscription, operation included. A
bug, a loop or an unusually long listing must therefore never be able to spend
more than a small, known amount.

## Considered options

1. Rely on the Azure budget alert.
2. A fixed number of fact sheets per run.
3. A cost guard in the code that checks every call against a ledger, plus
   independent limits in Azure.

## Decision

Option 3, in layers that do not depend on each other:

- **Before every call** the cost guard (`job_finder/agent/cost_guard.py`)
  checks the switch, a known price, the ledger `agent_usage` in PostgreSQL with
  a daily and a monthly limit (all runs together), and the limits per job for
  cost, calls and paid web searches. Invalid settings switch the agent off, and
  fixed ceilings in the code (5 € per day, 50 € per month) catch typos.
- **In Azure** the model deployment has a throttle in tokens per minute, a token
  alert fires on unusual days and a monthly budget sends email warnings.
- The model account has no API keys; only the worker, the hybrid run and the
  owner may call it.

## Consequences

- The worst case of a bug is bounded by the daily limit plus at most one call.
- Every answered call is booked in the ledger right away, so costs are known per
  job and per day and show up in the logs and the operations workbook. The SDK
  repeats no request on its own; a call whose answer is lost (timeout, lost
  connection, server error) is booked at the job maximum before the run stops,
  as it may have been billed.
- The run's time budget is checked before every model call and before waiting
  out a throttle, not only between jobs.
- A reached limit stops the agent for the run; the remaining jobs wait for the
  next run, and Discord reports it.
- Prices are maintained in the code (`pricing.py`); a model change needs a price
  update, which is why the model version is pinned.

## Revisit when

- The model or its pricing changes.
- The daily limit is reached regularly although the results are worth it.
- The subscription leaves its credit and a different budget applies.
