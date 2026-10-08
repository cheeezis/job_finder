# 0005: Run the two sources that block Azure locally

- Status: accepted
- Date: 2026-09 (cloud move), recorded 2026-10-08

## Context and problem

After the move to Azure, StepStone and Remotely returned nothing to the worker:
their bot protection blocks known cloud IP ranges. Both are relevant sources
for entry-level IT jobs in Germany. Working around a block is not an option: the
finder fetches openly with its own user agent, at a calm pace, and stops on
HTTP 403 and 429.

## Considered options

1. Drop both sources.
2. Route the cloud worker through a proxy or residential IPs.
3. Run only these two sources once a day from the owner's computer, in the same
   image as the cloud worker and against the same database.

## Decision

Option 3. A Windows task starts `scripts/run_local_hybrid.py` every morning: it
looks up the image the Azure worker runs, starts it in Docker with only
`stepstone,remotely`, and the run writes into the shared database like a cloud
run. The script points the database firewall rule at the current IP, keeps a log
and reports failures to Discord. StepStone is queried only with URLs its
robots.txt allows.

## Consequences

- Both sources stay in the finder, without hiding where the requests come from.
- The hybrid run depends on the owner's computer being on and signed in to
  Azure; if it does not run, the next cloud runs still cover all other sources,
  and each run replaces only the listings of its own sources.
- The local run gets its own service principal with exactly the roles it needs,
  and it shows up in the `runs` table and on the review's landing page.

## Revisit when

- One of the sources offers an API or accepts requests from Azure again.
- The owner's computer is no longer available every day.
