# Registrar Monitor

Registrar Monitor downloads registrar spreadsheets, stores enrollment history in
SQLite, reports changes to Telegram, and publishes a static Cloudflare Pages
dashboard. Telegram users can also watch courses or sections in a private chat.
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
`.env.example` to `.env` and set only the credentials you need; keep secrets out of
`settings.toml`, which holds semesters, URLs, milestones, timezone, paths, and
website settings.

## Commands

```bash
monitor poll                         # download and store the latest spreadsheet
monitor status "CSCI 151"            # inspect current enrollment
monitor report --stateful            # report new changes
monitor run                          # poll, report, and generate the dashboard
monitor schedule                     # run the monitoring daemon
monitor bot                          # run private Telegram subscriptions
monitor health-monitor               # alert the test operator about service outages
monitor deploy                       # generate the dashboard
monitor deploy --deploy              # generate and upload to Cloudflare Pages
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
make worker-check      # preview-image Worker
```

## Layout

```text
src/registrarmonitor/  automation, data, reporting, services, subscriptions, website
frontend/              Vite dashboard
workers/               preview-images Worker
scripts/               host setup and maintenance
tests/                 Python tests
settings.toml          non-secret configuration
```

Runtime databases, downloads, reports, generated site output, logs, caches, and
coverage are ignored by version control.

This is a personal, educational project.
