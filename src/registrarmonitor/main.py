#!/usr/bin/env python3
"""
Registrar Monitor - Main CLI Entry Point

A modern command-line interface for monitoring university registrar data.
This application can poll for enrollment data, generate reports, and send
notifications via Telegram.

Usage:
    monitor [--debug] poll [--file PATH]
    monitor [--debug] report [--no-telegram]
    monitor [--debug] run [--no-telegram] [--deploy]
    monitor [--debug] schedule
    monitor [--debug] bot
    monitor [--debug] deploy [--deploy]
    monitor [--debug] doctor [--json]
    monitor [--debug] db <command>
"""

import argparse
import asyncio
import json
import logging
import os
import sys
from pathlib import Path

from .cli import (
    DatabaseCommands,
    DeployCommand,
    PollCommand,
    ReportCommand,
    RunCommand,
    ScheduleCommand,
    StatusCommand,
)
from .core import setup_logging
from .observability import init_sentry
from .operational import build_doctor_report, write_json

logger = logging.getLogger(__name__)


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="Registrar Monitor - Monitor university enrollment data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  monitor poll                          # Download latest enrollment data
  monitor poll --file data.xlsx         # Process specific file
  monitor status "CSCI 101"             # Check status of specific course
  monitor report                        # Generate and send reports
  monitor report --no-telegram          # Generate reports without sending
  monitor run                           # Complete workflow (poll + report + website)
  monitor schedule                      # Run the scheduler
  monitor bot                           # Run the subscription bot
  monitor db stats                      # Show database statistics

Debug Mode:
  Use --debug before the command to enable verbose output:
  monitor --debug poll

Telegram Control:
  Use --no-telegram with report/run commands to generate reports locally.
        """,
    )

    # Global options
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    parser.add_argument(
        "--log-level",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        default="INFO",
        help="Set logging level (default: INFO)",
    )

    # Create subparsers
    subparsers = parser.add_subparsers(
        dest="command",
        help="Available commands",
        metavar="COMMAND",
    )

    # Poll command
    poll_parser = subparsers.add_parser(
        "poll",
        help="Download and process enrollment data",
        description="Poll for new enrollment data from the registrar",
    )
    poll_parser.add_argument(
        "--file",
        type=str,
        metavar="PATH",
        help="Process a specific Excel file instead of downloading latest",
    )
    # Status command
    status_parser = subparsers.add_parser(
        "status",
        help="Check status of specific courses",
        description="Check current enrollment status for one or more courses",
    )
    status_parser.add_argument(
        "courses",
        nargs="+",
        help="Course code(s) (e.g., 'CSCI 101' 'BUS 201')",
    )
    status_parser.add_argument(
        "--semester",
        type=str,
        help="Specific semester to check (optional)",
    )
    # Report command
    report_parser = subparsers.add_parser(
        "report",
        help="Generate and send reports from existing data",
        description="Generate text reports from stored enrollment data",
    )
    report_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Generate reports without sending to Telegram",
    )
    report_parser.add_argument(
        "--stateful",
        action="store_true",
        help="Run in stateful mode (only report if changes detected vs last report)",
    )

    # Run command
    run_parser = subparsers.add_parser(
        "run",
        help="Run complete process",
        description="Execute the complete workflow: download data, generate reports, and generate the website",
    )
    run_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Generate reports without sending to Telegram",
    )
    run_parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy the generated website with `wrangler deploy` (Workers Static Assets)",
    )

    # Schedule command
    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Run the two-phase scheduler",
        description="Start the scheduler that monitors for changes based on settings.toml milestones and activity patterns using two-phase scheduling",
    )
    schedule_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Run scheduler without sending Telegram reports",
    )

    subparsers.add_parser(
        "bot",
        help="Run the Telegram subscription bot",
        description="Start private-chat subscription commands and digest delivery",
    )

    # Deploy command
    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Generate and deploy the website",
        description="Generate the enrollment website and optionally deploy it as Worker static assets",
    )
    deploy_parser.add_argument(
        "--deploy",
        action="store_true",
        help="Run `wrangler deploy` after generation",
    )
    deploy_parser.add_argument(
        "--semester",
        type=str,
        help="Generate only for specific semester (e.g. 'fall2025')",
    )
    deploy_parser.add_argument(
        "--force",
        action="store_true",
        help="Force regeneration of all pages",
    )
    deploy_parser.add_argument(
        "--no-minify",
        action="store_true",
        help="Disable generated asset minification",
    )
    deploy_parser.add_argument(
        "--output-dir",
        type=Path,
        help="Generate into an isolated directory (deployment is refused)",
    )

    # Database commands
    doctor_parser = subparsers.add_parser(
        "doctor",
        help="Check local operational prerequisites and database health",
        description="Check tools, configuration, writable paths, frontend prerequisites, and SQLite health",
    )
    doctor_parser.add_argument(
        "--json",
        action="store_true",
        help="Print the full machine-readable report",
    )
    doctor_parser.add_argument(
        "--output",
        type=Path,
        help="Also write the JSON report to this path",
    )

    analyze_parser = subparsers.add_parser(
        "analyze",
        help="Run a saved cross-semester DuckDB query",
        description="Run analysis/<name>.sql read-only against every semester database",
    )
    analyze_parser.add_argument(
        "name", nargs="?", help="Query name (omit to list the saved queries)"
    )
    analyze_parser.add_argument(
        "--csv", type=Path, metavar="PATH", help="Write CSV instead of a table"
    )

    export_parser = subparsers.add_parser(
        "export-parquet",
        help="Export one semester's per-snapshot enrollment as Parquet",
        description="Write <semester>.parquet, one row per section per snapshot",
    )
    export_parser.add_argument("semester", help='Semester name, e.g. "Fall 2026"')
    export_parser.add_argument(
        "--out",
        type=Path,
        metavar="DIR",
        help="Output directory (default: data/open-data)",
    )

    # Database commands
    db_parser = subparsers.add_parser(
        "db",
        help="Database operations",
        description="Perform various database maintenance operations",
    )
    db_subparsers = db_parser.add_subparsers(
        dest="db_command",
        help="Database operations",
        metavar="OPERATION",
    )

    # Database stats
    db_subparsers.add_parser(
        "stats",
        help="Show database statistics",
        description="Display statistics about stored enrollment data",
    )

    initialize_parser = db_subparsers.add_parser(
        "initialize",
        help="Create an empty semester database",
        description=(
            "Initialize a fresh semester directly with checkpointed v2 tables."
        ),
    )
    initialize_parser.add_argument("--semester", required=True)
    initialize_parser.add_argument("--database", type=Path)
    return parser


async def handle_poll_command(args) -> int:
    """Handle the poll command."""
    command = PollCommand(debug=args.debug)
    success = await command.run(file_path=getattr(args, "file", None))
    return 0 if success else 1


async def handle_report_command(args) -> int:
    """Handle the report command."""
    no_telegram = getattr(args, "no_telegram", False)
    stateful = getattr(args, "stateful", False)
    command = ReportCommand(
        debug=args.debug, no_telegram=no_telegram, stateful=stateful
    )
    success = await command.run()
    return 0 if success else 1


async def handle_run_command(args) -> int:
    """Handle the run command."""
    no_telegram = getattr(args, "no_telegram", False)
    deploy = getattr(args, "deploy", False)
    command = RunCommand(debug=args.debug, no_telegram=no_telegram, deploy=deploy)
    success = await command.run()
    return 0 if success else 1


async def handle_schedule_command(args) -> int:
    """Handle the schedule command."""
    no_telegram = getattr(args, "no_telegram", False)
    command = ScheduleCommand(debug=args.debug, no_telegram=no_telegram)
    try:
        await command.run()
        return 0
    except KeyboardInterrupt:
        return 0  # Normal exit for scheduler


async def handle_bot_command(_args) -> int:
    """Run the webhook Telegram subscription service."""
    from .subscriptions.runtime import SubscriptionBotRuntime

    await SubscriptionBotRuntime().run()
    return 0


async def handle_deploy_command(args) -> int:
    """Handle the deploy command."""
    command = DeployCommand(debug=args.debug)
    success = command.run(
        deploy=getattr(args, "deploy", False),
        semester=getattr(args, "semester", None),
        force=getattr(args, "force", False),
        minify=not getattr(args, "no_minify", False),
        output_dir=getattr(args, "output_dir", None),
    )
    return 0 if success else 1


async def handle_status_command(args) -> int:
    """Handle the status command."""
    command = StatusCommand(debug=args.debug)
    success = await command.run(
        courses=args.courses,
        semester=getattr(args, "semester", None),
    )
    return 0 if success else 1


async def handle_analyze_command(args) -> int:
    """Run a saved DuckDB analysis query."""
    from . import analysis

    if not args.name:
        print("\n".join(analysis.available_queries()))
        return 0
    try:
        connection = analysis.open_analysis(analysis.configured_databases())
        try:
            columns, rows = analysis.run_query(connection, args.name)
        finally:
            connection.close()
    except analysis.AnalysisError as error:
        logger.warning("Analysis failed: %s", error)
        return 1
    if args.csv:
        analysis.write_csv(args.csv, columns, rows)
        print(f"Wrote {len(rows)} rows to {args.csv}")
    else:
        print(analysis.format_table(columns, rows))
    return 0


async def handle_export_parquet_command(args) -> int:
    """Export one semester as Parquet."""
    from . import analysis

    try:
        target = analysis.export_parquet(
            args.semester, args.out or analysis.DEFAULT_EXPORT_DIR
        )
    except analysis.AnalysisError as error:
        logger.warning("Parquet export failed semester=%s: %s", args.semester, error)
        return 1
    print(f"Wrote {target}")
    return 0


async def handle_db_command(args) -> int:
    """Handle database commands."""
    command = DatabaseCommands(debug=args.debug)

    if args.db_command == "stats":
        success = await command.stats()
    elif args.db_command == "initialize":
        success = await command.initialize_fresh_storage(
            semester=args.semester,
            database=getattr(args, "database", None),
        )
    else:
        logger.error("Invalid database command: %s", args.db_command)
        return 1

    return 0 if success else 1


async def handle_doctor_command(args) -> int:
    """Handle bounded operational diagnostics."""
    report = build_doctor_report()
    output = getattr(args, "output", None)
    if output:
        write_json(report, output)
    if getattr(args, "json", False):
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        for check in report["checks"]:
            print(f"[{check['status'].upper():4}] {check['name']}: {check['message']}")
        summary = report["summary"]
        print(
            f"Doctor: {summary['pass']} passed, {summary['warn']} warnings, "
            f"{summary['fail']} failed"
        )
    return 0 if report["ok"] else 1


async def async_main() -> int:
    """Main async entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Keep structured doctor output free of log lines and side effects.
    log_level = "DEBUG" if args.debug else args.log_level
    is_doctor = args.command == "doctor"
    # Long-running services log JSON lines for journald; the interactive CLI
    # keeps colored human-readable output.
    is_service = args.command in ("schedule", "bot") or (
        os.environ.get("LOG_FORMAT", "").lower() == "json"
    )
    setup_logging(
        level=log_level,
        enable_console=not is_doctor,
        enable_file=not is_doctor,
        json_logs=is_service,
    )
    if not is_doctor:
        init_sentry()

    logger.info("CLI started command=%s log_level=%s", args.command, log_level)

    # Handle commands
    try:
        if args.command == "poll":
            return await handle_poll_command(args)
        elif args.command == "status":
            return await handle_status_command(args)
        elif args.command == "report":
            return await handle_report_command(args)
        elif args.command == "run":
            return await handle_run_command(args)
        elif args.command == "schedule":
            return await handle_schedule_command(args)
        elif args.command == "bot":
            return await handle_bot_command(args)
        elif args.command == "deploy":
            return await handle_deploy_command(args)
        elif args.command == "doctor":
            return await handle_doctor_command(args)
        elif args.command == "analyze":
            return await handle_analyze_command(args)
        elif args.command == "export-parquet":
            return await handle_export_parquet_command(args)
        elif args.command == "db":
            return await handle_db_command(args)
        else:
            # No command provided, show help
            parser.print_help()
            return 1

    except KeyboardInterrupt:
        logger.info("Operation interrupted by user")
        return 130  # Standard exit code for Ctrl+C
    except Exception:
        logger.exception("Unexpected error in CLI command=%s", args.command)
        return 1


def cli_main() -> None:
    """Entry point for the CLI application."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        logger.info("Application interrupted")
        sys.exit(130)
    except Exception:
        logger.exception("Fatal error")
        sys.exit(1)


if __name__ == "__main__":
    cli_main()
