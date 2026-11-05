# Registrar Monitor

Registrar Monitor downloads registrar spreadsheets, stores enrollment history in
SQLite, reports changes to Telegram, and publishes a static dashboard as
Cloudflare Workers Static Assets. Telegram users can also watch courses or sections in a private chat.
Design and glossary: [`docs/architecture.md`](docs/architecture.md). Production,
publication, and Telegram operations: [`docs/operations.md`](docs/operations.md).

## Setup

Requirements: Python and Node.js versions from `.python-version` and
`.node-version`, [`uv`](https://docs.astral.sh/uv/), and
[`jj`](https://jj-vcs.github.io/jj/) for normal repository work.

```bash
jj git clone <repo-url>
cd registrar_monitor
make bootstrap
```

For an existing Git checkout, run `jj git init --colocate .` once. Copy
`.env.example` to `.env` and set only the credentials and endpoints you need; keep secrets out of
`settings.toml`, which holds semesters, URLs, milestones, timezone, paths, and
website settings.

## Commands

```bash
monitor db initialize --semester "Fall 2026"  # create a semester's storage (never implicit)
monitor poll                         # download and store the latest spreadsheet
monitor poll --file F.xls            # store a downloaded file under its configured semester
monitor poll --semester "Fall 2026"  # poll a specific semester
monitor status "CSCI 151"            # inspect current enrollment
monitor report --stateful            # report new changes
monitor run                          # poll, report, and generate the dashboard
monitor schedule                     # run the monitoring daemon
monitor bot                          # run private Telegram subscriptions
monitor deploy                       # generate the dashboard
monitor deploy --deploy              # generate and `wrangler deploy` the site Worker
monitor deploy --dry-run             # generate, then `wrangler deploy --dry-run` (no upload)
monitor deploy --upload-only         # deploy the existing output without regenerating
monitor baseline                     # mark the latest snapshot reported; sends nothing
monitor rehearse                     # one poll+report cycle on a scratch copy, nothing sent
monitor analyze                      # list saved DuckDB queries (analysis/*.sql)
monitor analyze fill_curves_by_department --csv out.csv
monitor db stats
monitor doctor
```

Add `--no-telegram` to `report`, `run`, or `schedule` for local operation; put the
global `--debug` option before the command. `monitor --help` is the full reference.

## Development

```bash
make check-fast        # Python format, lint, types, and tests
make check             # add frontend lint, typecheck, unit tests, and build
make test-browser      # Chromium smoke tests
make worker-check      # site Worker: types, tests, deploy dry-run
make deploy-vm REV=<revision> UNIT=<unit>.service   # authorized VM deploy only
```

## Layout

```text
src/registrarmonitor/  automation, data, reporting, services, subscriptions, website
frontend/              Vite dashboard
workers/site/          Worker: /preview/* images; serves frontend/public as Static Assets
scripts/               host setup and maintenance
tests/                 Python tests
settings.toml          non-secret configuration
```

Runtime databases, downloads, reports, generated site output, logs, caches, and
coverage are ignored by version control.

This is a personal, educational project.
