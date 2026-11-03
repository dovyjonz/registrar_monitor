# Agent guide

Python 3.13 app with a Vite dashboard: stores enrollment history in SQLite, reports
changes to Telegram, deploys a static site to Cloudflare as Workers Static Assets.
```text
registrar XLS -> automation/ -> data/ (per-semester SQLite)
                                 |- services/reporting -> reporting/ -> Telegram channel
                                 |                     `- subscriptions/ (bot DB, digests)
                                 `- services/website -> website/ -> static site -> site Worker
frontend/ (Vite + Chart.js)   workers/site/ (Worker: static assets + /preview/*)
```

## Sources of truth

`settings.toml`, `Makefile`, `monitor --help`, `docs/architecture.md` (glossary,
lasting decisions), `docs/operations.md` (production), `README.md` (setup).

## Invariants

- SQLite is the enrollment source of truth, v2 schema only. Do not restore JSON
  snapshot paths.
- Keep bot state separate from enrollment databases.
- Derive semester, scheduler, notification, and website behavior from
  `settings.toml`.
- Keep secrets in `.env`; never print tokens, Telegram IDs, env dumps, or journals.
- Keep runtime/generated output untracked: `data/`, logs, downloads, reports,
  generated site, `output/`, coverage, caches.
- Remove obsolete paths instead of adding compatibility layers.
- Code sharing, VM sync, Worker deploys (site), database changes, and service
  state are separate actions; each production mutation needs explicit authorization.

## Boundaries

`automation/` downloads and scheduling; `data/` parsing, SQLite, comparisons;
`reporting/` channel text and Telegram; `services/` workflow orchestration;
`subscriptions/` private bot and delivery state; `website/` static read models and
pages; `models.py` enrollment domain models.
Reusable pytest fixtures go in `tests/conftest.py`. Prefer existing libraries.

## Commands

- Setup: `make bootstrap` (uv and npm installs).
- One test: `uv run pytest tests/test_x.py -k name`;
  `node --test frontend/test/x.test.mjs`; `npm --prefix workers/site test`.
- `make check-fast`: Ruff, ty, pytest. `make check`: that plus frontend lint,
  typecheck, unit tests, build. Both run offline.
- Network or browsers: `make test-browser` (Chromium), `make worker-check`
  (wrangler dry-run), `make site-generate`; `make deploy-vm` is production-only.
- Without a default uv cache, set `UV_CACHE_DIR=/private/tmp/uv-cache`.

## Verification

Run the closest focused test, then `make check-fast` (`make check` for cross-cutting
or frontend changes). Browser tests only when UI behavior changed. No evidence docs.

## Version control

This is a colocated Jujutsu/Git repository. Use `jj status`, bounded `jj log`, and
`jj diff`; treat `@` as the working-copy commit and preserve unrelated changes.
Inspect `jj op log` before recovery. Avoid Git worktree/history rewrites and do
not add co-author trailers.

Cloud sessions have no `jj`; use plain `git` on a feature branch there.
`scripts/cloud_setup.sh` runs from `.claude/settings.json` at session start and
installs Python and npm dependencies. Cloud sessions have no `.env`, no
production access, and no `data/` databases; tests use temporary fixtures.

## Registrar calendar

Milestones, deadlines, and semesters live in `settings.toml`; the scheduler polls
fastest around them and is stopped between periods. Read the next milestone there
to tell if production is live; deploy only well outside a milestone window.

## Skills

Load from `.agents/skills/` when relevant:
- `registrar-monitor-storage`: SQLite, snapshots, Litestream, analysis.
- `registrar-monitor-telegram`: channel reports, bot, digests, deep links, Mini App API.
- `registrar-monitor-dashboard`: `frontend/`, site publication, the Worker.
- `registrar-monitor-production`: VM, systemd units, deploys, permissions.

## Issues and docs

Issues live in GitHub Issues (`gh` from this checkout): `gh issue create --title
"..." --body "..."`, `gh issue view <n> --comments`,
`gh issue list --state open --json number,title,labels`,
`gh issue comment <n> --body "..."`, `gh issue edit <n> --add-label "..."`,
`gh issue close <n> --comment "..."`.

No ADRs or separate specs; lasting decisions go in `docs/architecture.md`.
