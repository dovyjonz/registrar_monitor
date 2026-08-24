# Agent guide

Registrar Monitor is a Python 3.13 application with a Vite dashboard. It stores
enrollment history in SQLite, reports changes to Telegram, and uploads a static
site to Cloudflare Pages.

## Start here

- `CONTEXT.md` defines domain terms.
- `settings.toml`, `Makefile`, and `monitor --help` are executable sources of truth.
- `README.md`, `DATABASE.md`, and `docs/adr/` explain the current system.
- Use the focused repo skill under `.agents/skills/` for storage, Telegram,
  dashboard, or production work.

## Agent skills

Check each new request against `/ask-matt` before choosing an engineering flow.
Follow the routed skill or flow, while preserving this guide's authorization and
production boundaries.

### Issue tracker

Issues and specs live in GitHub Issues. See `docs/agents/issue-tracker.md`.

### Triage labels

Matt's five canonical triage roles use their default label names. See
`docs/agents/triage-labels.md`.

### Domain docs

This is a single-context repository with `CONTEXT.md` and root `docs/adr/`. See
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
- `data/`: parsing, SQLite, migrations, and comparisons
- `reporting/`: channel text, Telegram, and PDF output
- `services/`: workflow orchestration
- `subscriptions/`: private bot interactions and delivery state
- `website/`: static read models and page generation
- `models.py`: enrollment domain models

Put reusable pytest fixtures in `tests/conftest.py`. Prefer the existing libraries
and the simplest complete design.

## Verification

Run the closest focused test while iterating. Use `make check-fast` for Python
changes and `make check` for cross-cutting or frontend changes. Browser, generated
site, Worker, security, benchmark, and release gates are opt-in and documented in
`docs/operations/tooling.md`.

If the default uv cache is unavailable, use
`UV_CACHE_DIR=/private/tmp/uv-cache`.

## Version control

This is a colocated Jujutsu/Git repository. Use `jj status`, bounded `jj log`, and
`jj diff`; treat `@` as the working-copy commit and preserve unrelated changes.
Inspect `jj op log` before recovery. Avoid Git worktree/history rewrites and do
not add co-author trailers.
