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

Aliases exist for common workflows:

- `monitor fetch` is an alias for `monitor poll`.
- `monitor sync` is an alias for `monitor run`.
- `monitor website` is an alias for `monitor deploy`.

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

## Verification

Before handing off code changes, run the narrow relevant checks. For scaffold or
cross-cutting changes, run:

```bash
make check
```

If `uv` cannot access its default cache in a sandboxed environment, rerun the
same command with an approved cache location or normal cache permissions.
