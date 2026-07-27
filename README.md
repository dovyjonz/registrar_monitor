# Registrar Monitor

A Python application for monitoring university registrar enrollment data. Downloads enrollment data, processes it into a SQLite database, generates text change reports, and sends notifications via Telegram. Features a modern CLI with global debug mode, automated scheduling, and a static website dashboard.

## Features

- **Modern CLI**: Clean command-line interface with subcommands and global debug mode
- **Data Processing**: Parse Excel enrollment data and create structured snapshots in SQLite
- **Change Detection**: Compare snapshots to identify new, removed, or modified courses/sections
- **Text Reports**: Generate compact, emoji-based change summaries
- **Telegram Notifications**: Send change reports automatically via Telegram bot
- **Automated Scheduling**: Activity-aware hybrid scheduler with configurable time zones
- **Website Dashboard**: Generate and deploy a static enrollment dashboard to Cloudflare Pages
- **Database Management**: Built-in tools for stats, cleanup, and JSON migration

## Quick Start

### Prerequisites

- Python 3.13.5 (the default pin; Python 3.14 is also tested in CI)
- Node.js 24.18.0 LTS and npm 11.x for the website tooling
- [`uv`](https://docs.astral.sh/uv/) package manager
- [`jj`](https://jj-vcs.github.io/jj/) version control
- Codex CLI 0.144.0 or newer for the project tooling workflow
- Telegram bot token and chat ID (optional, for notifications)

### Installation

```bash
jj git clone <repo-url>
cd registrar_monitor
make bootstrap
```

For an existing Git checkout, initialize Jujutsu in colocated mode:

```bash
jj git init --colocate .
```

The colocated repository keeps `.git` for GitHub and Git-aware tooling while
using Jujutsu for normal local version-control work.

### Configuration

1. Copy `.env.example` to `.env` and fill in your Telegram credentials:
   ```
   TELEGRAM_BOT_TOKEN=your_token_here
   TELEGRAM_CHAT_ID=your_chat_id_here
   ```

2. Edit `settings.toml` to configure:
   - Data source URL
   - Directory paths
   - Notification preferences

Keep real credentials out of `settings.toml`; it only contains non-secret defaults.

## Usage

### Basic Commands

```bash
# Download and process enrollment data
monitor poll
monitor poll --file data.xlsx      # Process specific file

# Generate and send text reports
monitor report                     # Generate and send to Telegram
monitor report --no-telegram       # Generate locally only
monitor report --stateful          # Only report if changes detected

# Complete workflow (poll + report + website generation)
monitor run
monitor run --no-telegram
monitor run --deploy              # Also deploy the generated website

# Check course status
monitor status "CSCI 101" "BUS 201"
```

### Scheduler

```bash
monitor schedule                   # Start hybrid scheduler
monitor schedule --no-telegram     # Disable Telegram during scheduling
```

### Database Operations

```bash
monitor db stats                   # Show database statistics
monitor db cleanup                 # Clean up old snapshots (keep 50)
monitor db cleanup --keep 100      # Keep 100 most recent
monitor db migrate                 # Migrate legacy JSON files to database
```

### Website

```bash
monitor deploy                     # Generate website
monitor deploy --deploy            # Generate and deploy to Cloudflare
monitor deploy --semester fall2025 # Generate for specific semester
monitor deploy --no-minify         # Disable minified generated output
```

### Debug Mode

Put `--debug` before the command for verbose output:

```bash
monitor --debug poll
monitor --debug report
monitor --debug schedule
```

## Architecture

### Data Flow

```
Registrar URL → Excel Download → Parse & Process → SQLite Database
                                                        ↓
                                          Snapshot Comparison → Text Report → Telegram
```

### Command Structure

```
monitor
├── [--debug] poll [--file PATH]
├── [--debug] status COURSES... [--semester]
├── [--debug] report [--no-telegram] [--stateful]
├── [--debug] run [--no-telegram] [--deploy]
├── [--debug] schedule [--no-telegram]
├── [--debug] deploy [--deploy] [--semester] [--force] [--no-minify]
└── db
    ├── stats
    ├── cleanup [--keep COUNT]
    └── migrate
```

### Key Design Decisions

- **Database-first storage**: All enrollment data is persisted in SQLite databases (one per semester). No JSON snapshot files are written.
- **Text-only automated reports**: The automated pipeline generates compact text reports with emoji-based formatting. PDF generation remains available as an opt-in library method but is not part of the standard workflow.
- **Stateful reporting**: The `--stateful` flag enables change-based reporting — reports are only sent when differences are detected vs. the last reported snapshot.

## Project Structure

```
├── src/registrarmonitor/
│   ├── cli/                    # CLI command implementations
│   ├── core/                   # Logging, exceptions
│   ├── data/                   # Database, Excel reader, snapshot processing
│   ├── models/                 # Data models (EnrollmentSnapshot, Course, Section)
│   ├── reporting/              # Report formatter, Telegram reporter, PDF generator
│   ├── services/               # High-level service orchestrators
│   ├── automation/             # Scheduler, downloader
│   └── main.py                # CLI entry point
├── scripts/                    # Utility and maintenance scripts
├── assets/
│   ├── website/               # Vite/Cloudflare Workers dashboard scaffold
│   ├── downloads/             # Downloaded XLS files
│   └── changes/               # Text change reports
├── data/                       # SQLite databases (gitignored)
├── tests/                      # Unit tests
├── settings.toml               # Application configuration
└── schedule.txt                # Scheduler time zones (generated)
```

## Development

### Version Control

Use Jujutsu for day-to-day work. The working copy is already a commit, so there
is no staging area.

```bash
# Inspect the working copy and recent changes
jj status
jj diff
jj log -r 'present(@) | ancestors(@, 5) | present(trunk())'

# Start, describe, and finish a change
jj new 'trunk()' -m "Implement feature"
jj describe -m "Implement feature"
jj commit -m "Implement feature"
```

Bookmarks are named pointers used when sharing changes; they do not follow the
working copy automatically. Fetch before publishing, set or advance the
intended bookmark, preview the push, and then push only that bookmark:

```bash
jj git fetch --remote origin
jj bookmark set feature-name -r @-
jj git push --dry-run --remote origin --bookmark feature-name
jj git push --remote origin --bookmark feature-name
```

Use `jj op log` to inspect repository operations and `jj undo` to reverse the
latest operation. Avoid Git commands that rewrite the worktree or history
inside this colocated repository; they can leave Jujutsu's working-copy state
out of sync.

### Setup

```bash
make bootstrap
make doctor
```

`make bootstrap` installs the lockfile-pinned Python and website dependencies;
it does not install Python or Node itself. Use the versions in `.python-version`
and `.node-version`, then run `make doctor` to verify the tools, configuration,
paths, and optional secret-file presence.

### Tooling

| Tool   | Purpose          | Command              |
|--------|------------------|----------------------|
| `ruff` | Linting          | `uv run ruff check`  |
| `ruff` | Formatting       | `uv run ruff format` |
| `ty`   | Type checking    | `uv run ty check`    |
| `pytest` | Testing        | `uv run pytest`      |
| `pre-commit` | Local quality hooks | `uv run pre-commit run --all-files` |
| `eslint` | Website linting | `npm --prefix assets/website run lint` |
| `vite` | Website build    | `npm --prefix assets/website run build` |

Convenience `make` targets wrap the common commands:

```bash
make format
make lint
make type
make test
make website-lint
make website-build
make check-fast
make check
```

`make check` intentionally retains its existing full quality gate: formatting,
Ruff, ty, pytest, website lint, and website build. The new `make check-fast`
runs the Python formatting/lint/type/unit-test loop only. Browser and production
site checks remain opt-in so that pre-commit stays quick:

```bash
make test-browser  # Chromium check against generated dashboard output
make site-smoke    # generate and crawl deployable output
```

`make clean-generated` removes only reproducible website output (HTML, JSON,
headers, Vite assets, and course pages). It deliberately leaves runtime data,
logs, and databases untouched. `make site-smoke` fails if those non-publishable
files are present in `assets/website/public`, which prevents accidental
deployment of local data.

### Reproducible Baseline and Benchmarks

Use `make baseline` to write `output/tooling-baseline.json`. The JSON is stable
for identical tracked tooling inputs and records the Python/Node pins plus
SHA-256 hashes of `pyproject.toml`, `uv.lock`, and the website lockfile. Compare
it with a previous baseline using a JSON-aware diff after a deliberate tooling
change. `make benchmark` is opt-in and runs the deterministic mocked downloader
benchmark; it does not run during pre-commit or `make check`.

### Change Validation

Jujutsu changes do not invoke Git commit hooks. Run the checks explicitly
before publishing:

```bash
uv run pre-commit run --all-files
```

The hooks cover Ruff formatting/linting, TOML/YAML/basic file hygiene, private-key detection, type checking, and website linting. They intentionally exclude full pytest and browser checks; for larger changes, run `make check` and the opt-in browser/site targets when relevant.

### VPS Deployment

When migrating to a new VPS, remember to back up the `data/` directory (SQLite databases), the `.env` file with secrets, and the systemd service unit. Runtime data and secrets are not stored in the repository.

## Troubleshooting

| Issue | Solution |
|-------|----------|
| Import errors | Ensure you're in the project directory and have run `uv sync` |
| Telegram errors | Verify `TELEGRAM_BOT_TOKEN` and `TELEGRAM_CHAT_ID` in `.env` |
| Database errors | Run `monitor db stats` to check database health |
| Permission errors | Check file permissions for `data/` and `assets/` directories |

Check `logs/` directory for detailed error information.

## License

This project is for educational and personal use.
