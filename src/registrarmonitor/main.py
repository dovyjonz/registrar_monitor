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
    monitor [--debug] db {stats,cleanup,dedupe-instructor-changes} [--keep COUNT]
"""

import argparse
import asyncio
import json
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
from .core import get_logger, setup_logging
from .operational import build_doctor_report, write_json


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
        help="Deploy the generated website to Cloudflare Pages",
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

    # Deploy command
    deploy_parser = subparsers.add_parser(
        "deploy",
        help="Generate and deploy the website",
        description="Generate the enrollment website and optionally deploy to Cloudflare Pages",
    )
    deploy_parser.add_argument(
        "--deploy",
        action="store_true",
        help="Deploy to Cloudflare Pages after generation",
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
        "--prototype",
        action="store_true",
        help="Generate the local-only dashboard redesign prototype",
    )
    deploy_parser.add_argument(
        "--no-minify",
        action="store_true",
        help="Disable generated asset minification",
    )
    deploy_parser.add_argument(
        "--project",
        type=str,
        default="registrar-monitor",
        help="Cloudflare Pages project name (default: registrar-monitor)",
    )
    deploy_parser.add_argument(
        "--branch",
        type=str,
        help="Branch name for deployment",
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

    dedupe_parser = db_subparsers.add_parser(
        "dedupe-instructor-changes",
        help="Remove repeated instructor change artifacts",
        description="Delete consecutive duplicate instructor_changes rows while preserving real toggles",
    )
    dedupe_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report duplicate count without deleting rows",
    )

    migrate_parser = db_subparsers.add_parser(
        "migrate",
        help="Build or apply the checkpointed-state schema for one semester",
        description=(
            "Run an explicit, resumable migration for exactly one semester. "
            "Dry runs use a separate candidate database."
        ),
    )
    migrate_parser.add_argument("--semester", required=True)
    migrate_parser.add_argument(
        "--target-version",
        type=int,
        choices=[2],
        required=True,
    )
    migrate_parser.add_argument(
        "--metadata-mode",
        choices=["raw-enriched", "legacy-preserving"],
        required=True,
    )
    migrate_parser.add_argument("--raw-dir", type=Path)
    migrate_parser.add_argument("--database", type=Path)
    migrate_parser.add_argument("--candidate", type=Path)
    migrate_parser.add_argument("--backup-dir", type=Path)
    migrate_parser.add_argument("--report", type=Path, required=True)
    execution_mode = migrate_parser.add_mutually_exclusive_group(required=True)
    execution_mode.add_argument("--dry-run", action="store_true")
    execution_mode.add_argument("--apply", action="store_true")

    mode_parser = db_subparsers.add_parser(
        "mode",
        help="Audit and change one semester's storage compatibility mode",
        description=(
            "Transition a completed v2 database through legacy, shadow, and v2 "
            "modes after validating its migration gates."
        ),
    )
    mode_parser.add_argument("--semester", required=True)
    mode_parser.add_argument(
        "--target-mode",
        choices=["legacy", "shadow", "v2"],
        required=True,
    )
    mode_parser.add_argument("--database", type=Path)
    mode_parser.add_argument("--report", type=Path, required=True)

    manifest_rollback_parser = db_subparsers.add_parser(
        "rollback-manifest",
        help="Restore a semester's previous static manifest pointer",
    )
    manifest_rollback_parser.add_argument("--semester", required=True)
    manifest_rollback_parser.add_argument("--output-dir", type=Path)
    manifest_rollback_parser.add_argument("--report", type=Path, required=True)
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


async def handle_deploy_command(args) -> int:
    """Handle the deploy command."""
    command = DeployCommand(debug=args.debug)
    success = command.run(
        deploy=getattr(args, "deploy", False),
        semester=getattr(args, "semester", None),
        force=getattr(args, "force", False),
        minify=not getattr(args, "no_minify", False),
        project_name=getattr(args, "project", "registrar-monitor"),
        branch=getattr(args, "branch", None),
        prototype=getattr(args, "prototype", False),
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


async def handle_db_command(args) -> int:
    """Handle database commands."""
    command = DatabaseCommands(debug=args.debug)

    if args.db_command == "stats":
        success = await command.stats()
    elif args.db_command == "cleanup":
        keep_count = getattr(args, "keep", 50)
        success = await command.cleanup(keep_count=keep_count)
    elif args.db_command == "dedupe-instructor-changes":
        success = await command.dedupe_instructor_changes(
            dry_run=getattr(args, "dry_run", False)
        )
    elif args.db_command == "migrate":
        success = await command.migrate(
            semester=args.semester,
            target_version=args.target_version,
            metadata_mode=args.metadata_mode,
            report_path=args.report,
            dry_run=args.dry_run,
            database=getattr(args, "database", None),
            candidate=getattr(args, "candidate", None),
            backup_dir=getattr(args, "backup_dir", None),
            raw_dir=getattr(args, "raw_dir", None),
        )
    elif args.db_command == "mode":
        success = await command.transition_mode(
            semester=args.semester,
            target_mode=args.target_mode,
            report_path=args.report,
            database=getattr(args, "database", None),
        )
    elif args.db_command == "rollback-manifest":
        success = await command.rollback_manifest(
            semester=args.semester,
            report_path=args.report,
            output_dir=getattr(args, "output_dir", None),
        )
    else:
        print("❌ Invalid database command")
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
    setup_logging(
        level=log_level,
        enable_console=not is_doctor,
        enable_file=not is_doctor,
    )

    logger = get_logger(__name__)
    logger.info(f"Starting Registrar Monitor CLI with command: {args.command}")

    if args.debug:
        print(f"🔍 DEBUG MODE ENABLED - Log level: {log_level}")

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
        elif args.command == "deploy":
            return await handle_deploy_command(args)
        elif args.command == "doctor":
            return await handle_doctor_command(args)
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
