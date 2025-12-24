#!/usr/bin/env python3
"""
Registrar Monitor - Main CLI Entry Point

A modern command-line interface for monitoring university registrar data.
This application can poll for enrollment data, generate reports, and send
notifications via Telegram.

Usage:
    monitor poll [--file PATH] [--debug]
    monitor report [--debug] [--no-telegram]
    monitor run [--debug] [--no-telegram]
    monitor schedule [--debug]
    monitor db {stats,cleanup,migrate} [--debug] [--keep COUNT]
"""

import argparse
import asyncio
import sys

from .cli import (
    DatabaseCommands,
    PollCommand,
    ReportCommand,
    RunCommand,
    ScheduleCommand,
    StatusCommand,
)
from .core import get_logger, setup_logging


def create_parser() -> argparse.ArgumentParser:
    """Create the main argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="monitor",
        description="Registrar Monitor - Monitor university enrollment data",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  monitor fetch                         # Download latest enrollment data (alias for poll)
  monitor fetch --file data.xlsx        # Process specific file
  monitor status "CSCI 101"             # Check status of specific course
  monitor report                        # Generate and send reports
  monitor report --no-telegram          # Generate reports without sending
  monitor sync                          # Complete process (fetch + report) (alias for run)
  monitor schedule                      # Run the scheduler
  monitor db stats                      # Show database statistics
  monitor plot "CSCI 101"               # Plot course history

Debug Mode:
  Use --debug with any command to enable verbose output.

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
        aliases=["fetch"],
        help="Download and process enrollment data (alias: fetch)",
        description="Poll for new enrollment data from the registrar",
    )
    poll_parser.add_argument(
        "--file",
        type=str,
        metavar="PATH",
        help="Process a specific Excel file instead of downloading latest",
    )
    poll_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
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
    status_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )
    # Report command
    report_parser = subparsers.add_parser(
        "report",
        help="Generate and send reports from existing data",
        description="Generate PDF and text reports from stored enrollment data",
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
    report_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    # Run command
    run_parser = subparsers.add_parser(
        "run",
        aliases=["sync"],
        help="Run complete process (alias: sync)",
        description="Execute the complete workflow: download data and generate reports",
    )
    run_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Generate reports without sending to Telegram",
    )
    run_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    # Schedule command
    schedule_parser = subparsers.add_parser(
        "schedule",
        help="Run the hybrid scheduler",
        description="Start the scheduler that monitors for changes based on schedule.txt and activity patterns",
    )
    schedule_parser.add_argument(
        "--scheduler",
        type=str,
        choices=["hybrid", "two-phase"],
        default="hybrid",
        help="Scheduler type: 'hybrid' (default) or 'two-phase'",
    )
    schedule_parser.add_argument(
        "--no-telegram",
        action="store_true",
        help="Run scheduler without sending Telegram reports",
    )
    schedule_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
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
    stats_parser = db_subparsers.add_parser(
        "stats",
        help="Show database statistics",
        description="Display statistics about stored enrollment data",
    )
    stats_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    # Database cleanup
    cleanup_parser = db_subparsers.add_parser(
        "cleanup",
        help="Clean up old snapshots",
        description="Remove old snapshots from the database, keeping only the most recent ones",
    )
    cleanup_parser.add_argument(
        "--keep",
        type=int,
        default=50,
        metavar="COUNT",
        help="Number of snapshots to keep (default: 50)",
    )
    cleanup_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    # Database migrate
    migrate_parser = db_subparsers.add_parser(
        "migrate",
        help="Migrate JSON files to database",
        description="Migrate existing JSON enrollment files to the database format",
    )
    migrate_parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug mode with verbose output",
    )

    return parser


async def handle_poll_command(args) -> int:
    """Handle the poll command."""
    debug = getattr(args, "debug", False) or args.debug
    command = PollCommand(debug=debug)
    success = await command.run(file_path=getattr(args, "file", None))
    return 0 if success else 1


async def handle_report_command(args) -> int:
    """Handle the report command."""
    debug = getattr(args, "debug", False) or args.debug
    no_telegram = getattr(args, "no_telegram", False)
    stateful = getattr(args, "stateful", False)
    command = ReportCommand(debug=debug, no_telegram=no_telegram, stateful=stateful)
    success = await command.run()
    return 0 if success else 1


async def handle_run_command(args) -> int:
    """Handle the run command."""
    debug = getattr(args, "debug", False) or args.debug
    no_telegram = getattr(args, "no_telegram", False)
    command = RunCommand(debug=debug, no_telegram=no_telegram)
    success = await command.run()
    return 0 if success else 1


async def handle_schedule_command(args) -> int:
    """Handle the schedule command."""
    debug = getattr(args, "debug", False) or args.debug
    scheduler_type = getattr(args, "scheduler", "hybrid")
    no_telegram = getattr(args, "no_telegram", False)
    command = ScheduleCommand(
        debug=debug, scheduler_type=scheduler_type, no_telegram=no_telegram
    )
    try:
        await command.run()
        return 0
    except KeyboardInterrupt:
        return 0  # Normal exit for scheduler


async def handle_status_command(args) -> int:
    """Handle the status command."""
    debug = getattr(args, "debug", False) or args.debug
    command = StatusCommand(debug=debug)
    success = await command.run(
        courses=args.courses,
        semester=getattr(args, "semester", None),
    )
    return 0 if success else 1


async def handle_db_command(args) -> int:
    """Handle database commands."""
    debug = getattr(args, "debug", False) or args.debug
    command = DatabaseCommands(debug=debug)

    if args.db_command == "stats":
        success = await command.stats()
    elif args.db_command == "cleanup":
        keep_count = getattr(args, "keep", 50)
        success = await command.cleanup(keep_count=keep_count)
    elif args.db_command == "migrate":
        success = command.migrate()
    else:
        print("❌ Invalid database command")
        return 1

    return 0 if success else 1


async def async_main() -> int:
    """Main async entry point."""
    parser = create_parser()
    args = parser.parse_args()

    # Set up logging based on arguments
    log_level = "DEBUG" if args.debug else args.log_level
    setup_logging(level=log_level, enable_console=True, enable_file=True)

    logger = get_logger(__name__)
    logger.info(f"Starting Registrar Monitor CLI with command: {args.command}")

    if args.debug:
        print(f"🔍 DEBUG MODE ENABLED - Log level: {log_level}")

    # Handle commands
    try:
        if args.command in ["poll", "fetch"]:
            return await handle_poll_command(args)
        elif args.command == "status":
            return await handle_status_command(args)
        elif args.command == "report":
            return await handle_report_command(args)
        elif args.command in ["run", "sync"]:
            return await handle_run_command(args)
        elif args.command == "schedule":
            return await handle_schedule_command(args)

        elif args.command == "db":
            return await handle_db_command(args)
        else:
            # No command provided, show help
            parser.print_help()
            return 1

    except KeyboardInterrupt:
        print("\n\n👋 Operation interrupted by user")
        logger.info("Operation interrupted by user")
        return 130  # Standard exit code for Ctrl+C
    except Exception as e:
        print(f"\n❌ Unexpected error: {e}")
        logger.error(f"Unexpected error in CLI: {e}")
        if args.debug:
            import traceback

            print("\n🔍 DEBUG: Full traceback:")
            traceback.print_exc()
        return 1


def cli_main() -> None:
    """Entry point for the CLI application."""
    try:
        exit_code = asyncio.run(async_main())
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n👋 Application interrupted")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ Fatal error: {e}")
        sys.exit(1)


def main() -> None:
    """Backward compatibility entry point."""
    cli_main()


if __name__ == "__main__":
    cli_main()
