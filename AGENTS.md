# Agent Notes

## Project Overview

Registrar Monitor is a Python 3.13 application for downloading registrar
enrollment spreadsheets, storing normalized snapshots in SQLite, comparing
changes, sending Telegram reports, and generating a static website dashboard.

The Python package lives under `src/registrarmonitor`. The browser dashboard has
its own Vite/Cloudflare Workers scaffold under `assets/website`.

## Setup

- Use `uv sync --group dev` for the Python environment.
- Use `npm ci --prefix assets/website` for website dependencies when needed.
- Secrets belong in `.env`; keep real tokens out of `settings.toml`, tests, and
  committed files.
- Runtime output is intentionally gitignored: `data/`, `logs/`,
  `assets/downloads/`, `assets/changes/`, generated website files, coverage
  output, and local caches.

## Common Commands

```bash
uv run ruff format
uv run ruff check
uv run ty check
uv run pytest
npm --prefix assets/website run build
```

The same checks are available through `make`:

```bash
make format
make lint
make type
make test
make website-build
make check
```

## Website Local Debugging

- To inspect the generated dashboard locally, serve the generated static site,
  not the Vite asset base:

```bash
cd assets/website/public
python3 -m http.server 8000 --bind 127.0.0.1
```

- Open `http://127.0.0.1:8000/` or a semester page such as
  `http://127.0.0.1:8000/summer2026.html`.
- Do not use `http://127.0.0.1:5173/assets/` for generated pages; that path is
  only the production asset base and will 404 under Vite dev serving.
- After running `npm --prefix assets/website run build`, make sure generated
  HTML files point at the current hashed JS/CSS from
  `assets/website/public/assets/.vite/manifest.json`. If the page stays on
  "Loading enrollment data...", check the local server log for missing
  `assets/main-*.js` requests and run:

```bash
UV_CACHE_DIR=/private/tmp/uv-cache uv run python -c "from registrarmonitor.services.website_service import WebsiteService; WebsiteService()._patch_asset_hashes_in_html()"
```

- If a browser appears stuck, hard refresh after patching asset hashes.

## CLI

The package installs the `monitor` command:

```bash
monitor poll
monitor report --no-telegram
monitor run --no-telegram
monitor schedule --no-telegram
monitor deploy
monitor db stats
```

## Implementation Notes

- Treat SQLite as the source of truth for enrollment snapshots. Do not introduce
  new JSON snapshot persistence for normal monitor data.
- `settings.toml` is the single source for semester milestones and deadlines.
  Website milestone rendering and scheduler behavior derive from it.
- Keep generated website pages and payloads out of source edits unless the task
  is explicitly about generated artifacts.
- Prefer existing service boundaries:
  - `automation/` for download and scheduler behavior
  - `data/` for parsing, database, migration, and comparison logic
  - `reporting/` for report formatting and Telegram/PDF output
  - `services/` for workflow orchestration
  - `website/` for static page/data generation
- When adding tests, keep fixtures in `tests/conftest.py` if they are reused.

## Version Control

- Use Jujutsu for normal repository inspection and changes. Start with
  `jj status`, a bounded `jj log`, and `jj diff`.
- Treat `@` as the working-copy commit and use change IDs when referring to
  evolving work. There is no Git-style staging area.
- Use bookmarks only when a named pointer is needed for sharing. Inspect local
  and remote bookmark targets, fetch first, and preview pushes with
  `jj git push --dry-run`.
- This is a colocated repository: `.git` remains for GitHub and compatibility
  tooling, while `.jj` owns the working-copy workflow. Do not use Git commands
  that rewrite or clean the worktree or history unless Git-specific behavior is
  explicitly required and the Jujutsu consequences have been inspected.
- Use `jj op log` before recovery operations. Prefer `jj undo` or a targeted
  operation restore/revert over Git reset commands.

## Commits

Do not add `Co-Authored-By` trailers to commit messages.

## Agent skills

### Issue tracker

Local markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical roles: needs-triage, needs-info, ready-for-agent, ready-for-human, wontfix. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout — `CONTEXT.md` + `docs/adr/` at the repo root. See `docs/agents/domain.md`.

## Verification

Before handing off code changes, run the narrow relevant checks. For scaffold or
cross-cutting changes, run:

```bash
make check
```

If `uv` cannot access its default cache in a sandboxed environment, rerun the
same command with an approved cache location or normal cache permissions.
