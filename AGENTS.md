# Agent guide

Registrar Monitor is a Python 3.13 application that downloads registrar
spreadsheets, stores normalized enrollment snapshots in SQLite, reports changes,
and publishes a static dashboard to Cloudflare Pages.

## Sources of truth

- `settings.toml`: semester dates, milestones, timezone, paths, and website settings.
- `Makefile` and `monitor --help`: commands and quality gates.
- `docs/operations/production-topology.md`: runtime state and production runbook.
- `README.md` and `DATABASE.md`: operator usage and storage.
- `docs/adr/`: architectural decisions.

For the 2026 stabilization regressions or release evidence, read
`docs/agents/stabilization-release-verification-2026-08-20.md`.

## Invariants

- SQLite is the enrollment source of truth. Keep JSON snapshots out of the normal
  monitor path.
- Derive scheduler and website behavior from `settings.toml`.
- Keep secrets in `.env`; never print or copy tokens, chat IDs, environments, or
  unrestricted runtime configuration.
- Keep generated/runtime output untracked: `data/`, `logs/`, downloads, change
  reports, `assets/website/public/`, `output/`, coverage, and caches.
- Deploy the website by direct Cloudflare Pages upload. The preview-image Worker is
  separate and does not deploy Pages assets.
- Treat code sharing, Pages deployment, VM synchronization, and service changes as
  separate actions. Production mutation requires explicit operator authorization.

## Boundaries

Python lives under `src/registrarmonitor`; the Vite dashboard lives under
`assets/website`.

- `automation/`: downloads and scheduling
- `data/`: parsing, SQLite, migrations, and comparisons
- `reporting/`: text, Telegram, and PDF output
- `services/`: workflow orchestration
- `website/`: static read models and page generation
- `models.py`: enrollment domain models

Prefer these boundaries and put reusable pytest fixtures in `tests/conftest.py`.
Remove obsolete paths instead of adding compatibility layers.

## Verification

Use pinned toolchains and lockfiles. Start a new environment with `make bootstrap`
and `make doctor`. Run the closest focused test while iterating, `make check-fast`
for Python-only work, and `make check` before handing off cross-cutting changes.
Browser, generated-site, Worker, security, benchmark, and release gates are
documented in `docs/operations/tooling.md`.

If the default uv cache is unavailable, use
`UV_CACHE_DIR=/private/tmp/uv-cache`.

Generated-dashboard tests must serve `assets/website/public`, not Vite's source
shell. If generated HTML references a stale Vite hash, compare it with
`assets/website/public/assets/.vite/manifest.json`, run the supported asset-hash
patch through `WebsiteService`, and hard-refresh. Validate the deployed hashed
asset, not only a successful build or upload log.

Chart.js regular point and hover radii remain zero; capacity-change markers remain
visible. Horizontal guides render before datasets with negative z-order so they
stay behind course series and markers.

## Version control

This is a colocated Jujutsu/Git repository. Use Jujutsu for normal work:

1. Inspect `jj status`, a bounded `jj log`, and `jj diff`.
2. Treat `@` as the working-copy commit and preserve unrelated changes.
3. Inspect `jj op log` before recovery; prefer targeted Jujutsu undo/restore.
4. Fetch and inspect bookmarks before sharing, then preview the exact bookmark push.

Jujutsu commands can snapshot `@`; after history or recovery operations, re-check
status, the affected log, and the diff. A clean empty `@` alone does not prove the
intended files are present. Avoid Git worktree/history rewrites in this colocated
repository. Do not add `Co-Authored-By` trailers.

## Production

Read `docs/operations/production-topology.md` before any production check or
change. Use the `gcloud` skill, validate the exact leaf command with installed
help, specify project and zone, bound output, and preview SSH commands. Keep
checks read-only until the operator explicitly authorizes a mutation.

Only `registrarmonitor.service` is supported. A code sync does not reload its
Python process; after an explicitly authorized restart, verify active state, new
process identity/start time, and source checksums. Never revive the retired unit.

## Agent references

- Local issues: `docs/agents/issue-tracker.md`
- Triage roles: `docs/agents/triage-labels.md`
- Domain documents: `docs/agents/domain.md`
