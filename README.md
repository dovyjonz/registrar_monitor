# Registrar Monitor

Registrar Monitor downloads university registrar enrollment spreadsheets,
normalizes them into SQLite snapshots, detects enrollment changes, sends
optional Telegram reports, and generates a static Cloudflare Pages dashboard.

## What it provides

- Excel ingestion and normalized, per-semester SQLite storage
- Course and section change detection
- Stateful text reporting with optional Telegram delivery
- Activity-aware scheduling derived from configured registrar milestones
- Static dashboard generation and direct Cloudflare Pages deployment
- Database inspection and cleanup tools

## Quick start

### Requirements

- Python version from `.python-version`
- Node.js version from `.node-version`
- [`uv`](https://docs.astral.sh/uv/)
- [`jj`](https://jj-vcs.github.io/jj/) for the repository workflow

Clone and bootstrap:

```bash
jj git clone <repo-url>
cd registrar_monitor
make bootstrap
make doctor
```

`make` is a development wrapper, not a runtime requirement. On a host where
the SSH operator does not have `make`, run `scripts/runtime_doctor.sh` as the
application runtime user. It uses the existing `uv` environment with
`--no-sync --no-cache`, so the diagnostic does not install dependencies or
require a writable user cache.

For an existing Git checkout, initialize colocated Jujutsu with
`jj git init --colocate .`.

### Configuration

Copy `.env.example` to `.env` and add only the credentials you need. Telegram
delivery uses `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID`; Cloudflare deployment
uses its documented API credentials. Keep all real credentials out of
`settings.toml`.

Edit `settings.toml` for non-secret application configuration:

- registrar source and timezone;
- data and generated-output paths;
- semesters, milestones, and deadlines;
- reporting and website behavior;
- Cloudflare Pages project settings.

`settings.toml` is the authoritative source for semester scheduling. No generated
`schedule.txt` is required.

## Usage

The installed entry point is `monitor`. Use `monitor --help` and
`monitor <command> --help` for the complete option reference.

Common workflows:

```bash
monitor poll
monitor report --no-telegram
monitor report --stateful
monitor run --no-telegram
monitor schedule --no-telegram
monitor deploy
monitor deploy --deploy
monitor db stats
```

Put the global `--debug` option before the command, for example
`monitor --debug poll`.

## Architecture

```text
Registrar XLS
    ↓
download → parse and normalize → per-semester SQLite snapshots
                                      ↓
                              compare snapshots
                               ↙            ↘
                     text/Telegram       static dashboard
                                              ↓
                                      Cloudflare Pages
```

Key decisions:

- SQLite, not JSON files, is the source of truth for normal enrollment data.
- Legacy JSON snapshot migration is no longer a supported operator path.
- Automated reports are compact text; PDF generation is an opt-in library
  capability, not part of the default pipeline.
- Stateful reporting records the last reported snapshot in SQLite.
- Schema v2 uses checkpointed state plus contiguous event sequences while
  retaining and atomically dual-writing the legacy tables for one compatibility
  release.
- Static v3 data is published as a no-cache semester pointer to immutable,
  content-addressed manifests and summary/department blobs.
- Scheduler decisions and dashboard milestone rendering use the registrar
  timezone and milestones from `settings.toml`.
- Direct Pages upload through `WebsiteService` is the supported production
  deployment path.

## Repository layout

```text
src/registrarmonitor/
├── automation/       downloader and scheduler
├── cli/              command implementations
├── core/             logging and exceptions
├── data/             parsing, SQLite, migrations, comparisons
├── reporting/        text, Telegram, and PDF output
├── services/         workflow orchestration
├── website/          static page and payload generation
├── models.py         enrollment domain models
└── main.py           CLI entry point

assets/website/       Vite frontend and Cloudflare Pages configuration
scripts/              maintenance and host-setup utilities
tests/                automated tests
settings.toml         non-secret application configuration
```

Runtime databases, downloads, reports, logs, generated site output, caches, and
coverage artifacts are intentionally gitignored.

## Development

Use Jujutsu for normal local version-control work. There is no Git staging area:

```bash
jj status
jj diff
jj log -r 'present(@) | ancestors(@, 5) | present(trunk())'
```

The Makefile is the command index for setup and verification:

```bash
make check-fast
make check
make test-browser
make site-smoke
```

- `make check-fast` runs the Python formatting, lint, type, and test loop.
- `make check` adds the full website quality gate.
- `make test-browser` checks generated dashboard behavior in Chromium.
- `make site-smoke` generates and crawls deployable output.

`make clean-generated` removes only reproducible website output and deliberately
leaves runtime databases, downloads, reports, and logs untouched.

Jujutsu changes do not run Git commit hooks automatically. Run
`uv run pre-commit run --all-files` before publishing when the full hook suite is
appropriate.

## Production

Production monitoring is active as of 2026-08-04. The canonical unit is
`registrarmonitor.service`; it is installed, enabled, and running. The obsolete
`registrar-monitor.service` unit has been removed. Cron is running but is not a
Registrar Monitor execution path. The canonical daemon owns adaptive polling,
event-driven updates, and stateful reports at minutes 15 and 45 in the registrar
timezone.

Do not infer that repository or deployment work authorizes service-state
changes. See
[`docs/operations/production-topology.md`](docs/operations/production-topology.md)
for current resource identifiers, safe inspection commands, service-state
boundaries, and evidence boundaries.

When migrating a host, protect the SQLite databases and `.env` separately. The
canonical unit is reproducibly generated by `scripts/setup_vps.sh`; do not treat
the installed unit file as the only copy.

## Documentation

- [`DATABASE.md`](DATABASE.md): schema and database operations
- [`docs/operations/production-topology.md`](docs/operations/production-topology.md):
  current production topology and runbook
- [`docs/operations/reproducible-baseline.md`](docs/operations/reproducible-baseline.md):
  reproducibility contract and latest verification evidence
- [`docs/operations/tooling.md`](docs/operations/tooling.md): unit, browser,
  generated-output, doctor, baseline, CI, and dependency-update tooling
- [`AGENTS.md`](AGENTS.md): repository constraints for coding agents

## Troubleshooting

- Run `make doctor` locally, or `scripts/runtime_doctor.sh` on a runtime host,
  for toolchain, path, and optional-secret checks.
- Use `monitor db stats` for database visibility.
- Check `logs/` for application diagnostics.
- For generated dashboard failures, follow the local serving and asset-hash notes
  in `AGENTS.md`.

## License

This project is for educational and personal use.
