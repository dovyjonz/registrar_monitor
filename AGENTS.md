# Agent guide

Registrar Monitor is a Python 3.13 application with a Vite dashboard. It stores
enrollment history in SQLite, reports changes to Telegram, and uploads a static
site to Cloudflare Pages.

## Start here

- `settings.toml`, `Makefile`, and `monitor --help` are executable sources of truth.
- `README.md` covers setup; `docs/architecture.md` and `docs/operations.md`
  explain the current system and define domain terms.
- Use the focused repo skill under `.agents/skills/` for storage, Telegram,
  dashboard, or production work.

## Agent skills

Use `/ask-matt` as a routing reference when the appropriate engineering workflow
is unclear. For a well-scoped authorized request, use the relevant focused skill
and carry out the work. Enter an interview or planning-only flow when the user
requests it or material unresolved product decisions require it.

### Issue tracker

Issues and specs live in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Matt's five canonical triage roles use their default label names. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository; domain terms are in `docs/architecture.md`. See
`docs/agents/domain.md`.

## Invariants

- SQLite is the enrollment source of truth. Do not restore JSON snapshot paths.
- Keep bot state separate from enrollment databases.
- Derive semester, scheduler, notification, and website behavior from
  `settings.toml`.
- Keep secrets in `.env`. Never print tokens, Telegram IDs, environment dumps, or
  unrestricted journals.
- Keep runtime/generated output untracked: `data/`, logs, downloads, reports,
  generated website output, `output/`, coverage, and caches.
- Remove obsolete paths instead of adding compatibility layers.
- Treat code sharing, VM synchronization, Pages upload, database changes, and
  service state as separate actions. Production mutation requires explicit
  authorization.

## Boundaries

- `automation/`: downloads and scheduling
- `data/`: parsing, SQLite, and comparisons
- `reporting/`: channel text and Telegram
- `services/`: workflow orchestration
- `subscriptions/`: private bot interactions and delivery state
- `website/`: static read models and page generation
- `models.py`: enrollment domain models

Put reusable pytest fixtures in `tests/conftest.py`. Prefer the existing libraries
and the simplest complete design.

## Verification

Run the closest focused test while iterating. Use `make check-fast` for Python
changes and `make check` for cross-cutting or frontend changes. Browser and
Worker checks (`make test-browser`, `make worker-check`) are opt-in and documented
in `docs/operations.md`.

If the default uv cache is unavailable, use
`UV_CACHE_DIR=/private/tmp/uv-cache`.

## Version control

This is a colocated Jujutsu/Git repository. Use `jj status`, bounded `jj log`, and
`jj diff`; treat `@` as the working-copy commit and preserve unrelated changes.
Inspect `jj op log` before recovery. Avoid Git worktree/history rewrites and do
not add co-author trailers.

Cloud sessions have no `jj`; use plain `git` on a feature branch there.
`scripts/cloud_setup.sh` runs from `.claude/settings.json` at session start and
installs Python and npm dependencies. Cloud sessions have no `.env`, no
production access, and no `data/` databases; tests use temporary fixtures.
