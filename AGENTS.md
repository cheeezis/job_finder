# Job Finder: instructions for coding assistants

Shared by all assistants: Claude Code reads this file through `CLAUDE.md`,
Codex reads it directly. Private notes with resource names, local files, costs
and the current project state are in `CLAUDE.local.md` (not in the repository);
read it too when it exists.

## Project at a glance

A job-search assistant for entry-level IT roles in Germany, running on Azure
(README for the overview, `docs/` for details):

| Area | Where |
| --- | --- |
| Finder run and pipeline order | `run_finder.py`, `job_finder/workflow/` |
| Sources (job boards, feeds, career pages) | `job_finder/sources/` |
| Prefilter rules and scoring | `job_finder/matching/` |
| Fact-sheet agent (LangGraph, cost guard) | `job_finder/agent/` |
| Persistence, migrations, runtime rights | `job_finder/persistence/` |
| Review web app (FastAPI, plain JavaScript) | `job_finder/review_app.py`, `job_finder/*.js`, `*.html`, `app.css` |
| Infrastructure (Terraform) | `infrastructure/` |
| Local hybrid run (StepStone, Remotely) | `scripts/run_local_hybrid.py` |
| Usage, operations, development, decisions | `docs/usage.md`, `docs/operations.md`, `docs/development.md`, `docs/adr/` |

The user interface, console and Discord texts and the agent's rules are German;
code, comments, docstrings, docs, commits and PR texts are English. Code style:
`docs/development.md#python-style-and-helpful-documentation`.

## Commands

Development happens on Windows (PowerShell); CI runs on Linux.

```powershell
uv sync                                        # dependencies from uv.lock
uv run ruff check .
uv run ruff format --check .
uv run pyright
uv run pyright --pythonplatform Linux          # CI is Linux; run both
uv run python scripts/test_postgres.py         # suite against the separate _test database
node --test tests/frontend.test.cjs
npm ci; npm run check:types                    # type check of the browser scripts
terraform -chdir=infrastructure validate; terraform -chdir=infrastructure test
uv run python scripts/demo_data.py --serve     # review with made-up demo data
```

`scripts/test_postgres.py` needs `JOBFINDER_TEST_DATABASE_URL` pointing at a
database whose name ends in `_test` (local setup in `docs/operations.md`); a
disposable PostgreSQL container works as well. Browser tests run only with
`JOBFINDER_BROWSER_TESTS=1`.

## Safety rules

- Never run tests, drills or experiments against the production database; the
  test runner refuses targets that do not end in `_test`. Read production data
  only read-only and only when asked.
- Changes in Azure (Terraform apply, firewall rules, Key Vault, database roles or
  data, deleting anything) only after the owner's explicit OK. Prefer a
  read-only `terraform plan` to show what would change.
- No paid language-model calls (evals, agent runs) without explicit approval.
- Unset `DISCORD_WEBHOOK_URL` before local runs of `run_finder.py`; it is set in
  the user environment and would post to the real channel.
- The main checkout runs the scheduled local hybrid job: do work in a separate
  worktree and do not switch branches in the main checkout.
- The repository is public: no personal job-search data (companies applied to,
  decisions, counts from own data), no Azure resource names or IDs, no secrets in
  code, commits or PR texts.
- Untracked local files must survive cleanups (`.env*`, tfvars, the CA bundle in
  `tmp/`); see `CLAUDE.local.md`. Do not delete plans or notes with open items.

## Working together

- Answer in German. Name steps in full German words with their purpose, not
  only with codes, and show choices in the chat text too. Prefer multiple-choice
  questions for decisions.
- Go step by step and ask; do not decide personal or learning content alone.
  Before a change, explain in plain German what problem it solves; afterwards,
  say in a sentence or two what is now different. Keep one change in flight at a
  time. Personal files such as `profile.local.yaml` and `user_settings.local.yaml`
  are the owner's own description: change them only question by question.
- Prefer few pull requests with separate commits over many small ones. Branches
  are named `<type>/<short-description>`; no `codex/` branches.
- PR texts: short, English, sections "Summary" and "Testing", no assistant
  footer (such as "Generated with Claude Code") and no Co-Authored-By trailer.
  Show the text before opening the PR.
- Once the owner confirms the PR text ("passt"), open the PR and merge it after
  green CI. Never poll or schedule CI checks; read the status when the owner is
  back. Green Dependabot PRs for GitHub Actions or Ruff may be merged directly.
- Deploys after a merge wait for approval in the `production` environment. The
  assistant may approve it only for low-risk changes (docs, tests, tooling) when
  a read-only plan shows no changes; other deploys are approved by the owner.
- Keep the private handover file `tmp/uebergabe.md` current after every step, so
  another assistant can continue.
