# Registrar Monitor

A Python application for monitoring university registrar data. Downloads enrollment data, processes it, and generates reports in PDF and text format. Features a modern CLI with debug modes and clear separation between polling and reporting functionality.

## Features

- **Modern CLI Interface**: Clean command-line interface with subcommands and debug modes
- **Separated Operations**: Clear distinction between polling (data collection) and reporting
- **Debug Modes**: Generate reports with or without sending to Telegram
- **Data Processing**: Parse Excel enrollment data and create structured snapshots
- **Report Generation**: Create PDF enrollment reports and text change summaries
- **Telegram Integration**: Send reports automatically via Telegram bot (optional)
- **Automated Scheduling**: Run scheduled monitoring with multiple modes
- **Database Management**: Built-in database operations and maintenance tools

## Quick Start

### Prerequisites

- Python 3.13+
- `uv` package manager
- Telegram bot token and chat ID (optional, for notifications)

### Installation

1. Clone the repository
2. Install dependencies:
   ```bash
   uv sync
   ```

### Configuration

Edit `settings.toml` to configure:
- Telegram bot credentials (optional)
- Data source URL
- Directory paths
- PDF font settings
- Notification preferences

## Usage

The application provides a modern CLI with clear commands:

### Basic Commands

#### Poll for Data
Download and process enrollment data (no reports generated):
```bash
monitor poll                    # Download latest data
monitor poll --file data.xlsx   # Process specific file
monitor poll --debug            # Enable debug output
```

#### Generate Reports
Generate reports from existing data:
```bash
monitor report                  # Generate and send reports to Telegram
monitor report --no-telegram    # Generate reports without sending
monitor report --debug          # Enable debug output
```

#### Complete Process
Run the full workflow (poll + report):
```bash
monitor run                     # Download data and generate reports
monitor run --no-telegram       # Complete process without Telegram
monitor run --debug             # Enable debug output
```

### Advanced Commands

#### Scheduler
Run automated monitoring:
```bash
monitor schedule                # Start the hybrid scheduler
monitor schedule --debug        # Scheduler with debug output
```

#### Database Operations
Manage the database:
```bash
monitor db stats                    # Show database statistics
monitor db cleanup                  # Clean up old snapshots (keep 50)
monitor db cleanup --keep 100       # Keep 100 most recent snapshots
monitor db migrate                  # Migrate JSON files to database
```

### Debug Mode

Debug mode provides verbose output and additional information:

```bash
monitor poll --debug            # See detailed polling information
monitor report --debug          # See report generation details
monitor run --debug             # Full debug output for complete process
```

### Telegram Control

Control whether reports are sent to Telegram:

```bash
monitor report                  # Send to Telegram (default)
monitor report --no-telegram    # Generate locally only
monitor run --no-telegram       # Complete process without sending
```

This is useful for:
- Testing report generation
- Running in environments without Telegram access  
- Debugging report formatting

## Automation

### Automated Scheduler

For production use, you can run automated scheduling:

```bash
# Run stateful reporter (only sends when changes detected - recommended)
monitor report --stateful

# Run full process (poll + report)
monitor run

# Run with debug
monitor report --stateful --debug
```

### Cron Integration

Add to crontab for automated monitoring:

```bash
# Run stateful reporter every 30 minutes
*/30 * * * * cd /path/to/registrar-monitor && uv run monitor report --stateful

# Run full process twice daily
0 8,20 * * * cd /path/to/registrar-monitor && uv run monitor run
```

## Architecture

### Polling vs Reporting

The system clearly separates two main operations:

- **Polling**: Downloads and processes enrollment data, stores in database
- **Reporting**: Generates PDF/text reports from stored data, optionally sends via Telegram

This separation allows for:
- Independent testing of each component
- Flexible scheduling (poll frequently, report only on changes)
- Debug modes for report generation

### Debug Modes

Two types of debug functionality:

1. **Debug Output** (`--debug`): Verbose logging and status information
2. **No Telegram** (`--no-telegram`): Generate reports without sending

### Command Structure

```
monitor
├── poll [--file PATH] [--debug]
├── report [--debug] [--no-telegram] [--stateful]
├── run [--debug] [--no-telegram]
├── schedule [--debug]
└── db
    ├── stats [--debug]
    ├── cleanup [--keep COUNT] [--debug]
    └── migrate [--debug]
```

## Development

### Setup Development Environment

```bash
# Install with development dependencies
uv sync --group dev

# Format code
uv run ruff format

# Check linting
uv run ruff check

# Run type checking
uv run ty check
```

### Testing

```bash
# Test the CLI functionality
python test_cli.py

# Run specific command tests
monitor --help
monitor poll --help
monitor report --help
```

## File Structure

### Cleaned Up Structure

The project has been refactored for better organization:

```
├── src/registrarmonitor/           # Main package
│   ├── cli/                        # CLI command implementations
│   ├── core/                       # Core utilities (logging, exceptions)
│   ├── data/                       # Data processing and database
│   ├── services/                   # High-level services (including reporting)
│   └── main.py                     # CLI entry point
├── scripts/                        # Automation scripts
│   └── manage_database.py          # Advanced database operations
└── settings.toml                   # Configuration
```

### Removed Files

The following redundant files have been removed:
- `reporter.py` (Merged into ReportingService and CLI)
- `run_reporter.py` (functionality moved to CLI)
- `scripts/run_stateful_reporter.py` (functionality moved to CLI)

## Migration from Old Interface

If you were using the old menu-driven interface:

| Old Menu Option | New Command |
|----------------|-------------|
| Run complete process | `monitor run` |
| Download and process only | `monitor poll` |
| Process specific file | `monitor poll --file PATH` |
| Generate and send reports | `monitor report` |
| Generate PDF only (debug) | `monitor report --no-telegram` |
| Run scheduler | `monitor schedule` |
| Database operations | `monitor db [stats\|cleanup\|migrate]` |

## Troubleshooting

### Common Issues

1. **Import Errors**: Ensure you're in the project directory and have run `uv sync`
2. **Permission Errors**: Check file permissions for data directories
3. **Telegram Errors**: Verify bot token and chat ID in `settings.toml`
4. **Database Errors**: Try `monitor db stats` to check database health

### Debug Mode

Use debug mode to troubleshoot issues:

```bash
monitor poll --debug           # Debug data downloading
monitor report --debug         # Debug report generation
monitor db stats --debug       # Debug database operations
```

### Logs

Check log files in the `logs/` directory for detailed error information.

## License

This project is for educational and personal use.