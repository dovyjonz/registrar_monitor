# Registrar Monitor

A Python application for monitoring university registrar enrollment data. Downloads enrollment data, processes it into a SQLite database, generates text change reports, and sends notifications via Telegram. Features a modern CLI with debug modes, automated scheduling, and a static website dashboard.

## Features

- **Modern CLI**: Clean command-line interface with subcommands and debug modes
- **Data Processing**: Parse Excel enrollment data and create structured snapshots in SQLite
- **Change Detection**: Compare snapshots to identify new, removed, or modified courses/sections
- **Text Reports**: Generate compact, emoji-based change summaries
- **Telegram Notifications**: Send change reports automatically via Telegram bot
- **Automated Scheduling**: Activity-aware hybrid scheduler with configurable time zones
- **Website Dashboard**: Generate and deploy a static enrollment dashboard to Cloudflare Pages
- **Database Management**: Built-in tools for stats, cleanup, and JSON migration

## Quick Start

### Prerequisites

- Python 3.13+
- [`uv`](https://docs.astral.sh/uv/) package manager
- Telegram bot token and chat ID (optional, for notifications)

### Installation

```bash
git clone <repo-url>
cd registrar_monitor
uv sync
```

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
monitor poll --debug               # Verbose output

# Generate and send text reports
monitor report                     # Generate and send to Telegram
monitor report --no-telegram       # Generate locally only
monitor report --stateful          # Only report if changes detected

# Complete workflow (poll + report)
monitor run
monitor run --no-telegram

# Check course status
monitor status "CSCI 101" "BUS 201"
```

### Scheduler

```bash
monitor schedule                   # Start hybrid scheduler
monitor schedule --scheduler two-phase
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
```

### Debug Mode

Add `--debug` to any command for verbose output:

```bash
monitor poll --debug
monitor report --debug
monitor schedule --debug
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
├── poll [--file PATH] [--debug]
├── status COURSES... [--semester] [--debug]
├── report [--debug] [--no-telegram] [--stateful]
├── run [--debug] [--no-telegram]
├── schedule [--scheduler TYPE] [--no-telegram] [--debug]
├── deploy [--deploy] [--semester] [--force] [--minify] [--debug]
└── db
    ├── stats [--debug]
    ├── cleanup [--keep COUNT] [--debug]
    └── migrate [--debug]
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
├── assets/                     # Generated output (gitignored)
│   ├── website/               # Cloudflare Workers deployment
│   ├── downloads/             # Downloaded XLS files
│   └── changes/               # Text change reports
├── data/                       # SQLite databases (gitignored)
├── tests/                      # Unit tests
├── settings.toml               # Application configuration
└── schedule.txt                # Scheduler time zones (generated)
```

## Development

### Setup

```bash
uv sync --group dev
```

### Tooling

| Tool   | Purpose          | Command              |
|--------|------------------|----------------------|
| `ruff` | Linting          | `uv run ruff check`  |
| `ruff` | Formatting       | `uv run ruff format` |
| `ty`   | Type checking    | `uv run ty check`    |
| `pytest` | Testing        | `uv run pytest`      |

### Pre-commit Checklist

1. Format: `uv run ruff format`
2. Lint: `uv run ruff check`
3. Type check: `uv run ty check`
4. Test: `uv run pytest`

### VPS Deployment

Use [docs/VPS_MIGRATION.md](docs/VPS_MIGRATION.md) when migrating an existing VPS. It covers runtime-data backup, secret rotation, `.env`, and the systemd service.

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
