#!/usr/bin/env python3
"""
Reorders snapshots in a semester database chronologically.

This script addresses the issue of snapshots being imported in a non-chronological
order. It works by reading all snapshots from the specified semester database,
sorting them by their timestamp, and then inserting them into a new database file.
This process naturally re-assigns `snapshot_id` values in the correct
chronological sequence. The original database is then replaced with the newly
ordered one.
"""

import argparse
import logging
import shutil
import sqlite3
import sys
from pathlib import Path

# Add the src directory to the Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from registrarmonitor.data.database_manager import DatabaseManager


def setup_logging(verbose: bool = False):
    """Set up basic logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s - %(levelname)s - %(message)s")


def reorder_snapshots(semester: str, dry_run: bool = False) -> int:
    """
    Reorders all snapshots in a given semester's database chronologically.

    Args:
        semester: The semester identifier for the database (e.g., "Fall 2024").
        dry_run: If True, shows the reordering plan without modifying the database.

    Returns:
        The exit code (0 for success, 1 for failure).
    """
    logger = logging.getLogger(__name__)
    logger.info(f"Starting snapshot reordering for semester: '{semester}'")

    try:
        source_db_manager = DatabaseManager.create_for_semester(semester)
        source_db_path = Path(source_db_manager.db_path)

        if not source_db_path.exists():
            logger.error(
                f"❌ Database for semester '{semester}' not found at {source_db_path}"
            )
            return 1

        # 1. Fetch all snapshot metadata and sort chronologically
        with source_db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT snapshot_id, timestamp FROM snapshots ORDER BY timestamp ASC"
            )
            sorted_snapshots_meta = cursor.fetchall()

        if not sorted_snapshots_meta:
            logger.warning("⚠️ No snapshots found in the database. Nothing to do.")
            return 0

        logger.info(f"Found {len(sorted_snapshots_meta)} snapshots to reorder.")

        # 2. Handle dry run
        if dry_run:
            logger.info("--- DRY RUN MODE ---")
            logger.info("Snapshots will be reordered as follows (by new ID):")
            for i, (old_id, ts) in enumerate(sorted_snapshots_meta):
                print(f"  New ID {i + 1: >3}: Timestamp: {ts} (from old ID {old_id})")
            logger.info("Database file will NOT be modified.")
            logger.info("--- END DRY RUN ---")
            return 0

        # 3. Create a temporary database and manager
        temp_db_path = source_db_path.with_suffix(".reordered.db")
        if temp_db_path.exists():
            temp_db_path.unlink()  # Clean up from previous failed runs

        temp_db_manager = DatabaseManager(db_path=str(temp_db_path))
        logger.info(f"Created temporary database at: {temp_db_path}")

        # 4. Read full snapshots from source and write to temp in order
        id_map = {}  # Maps old snapshot_id to new snapshot_id
        total = len(sorted_snapshots_meta)
        for i, (old_snapshot_id, timestamp) in enumerate(sorted_snapshots_meta):
            logger.debug(
                f"Processing snapshot {i + 1}/{total} (Old ID: {old_snapshot_id}, Timestamp: {timestamp})"
            )

            # Fetch the full snapshot object using the existing method
            snapshot_data = source_db_manager.get_snapshot_data(old_snapshot_id)

            if snapshot_data:
                # Store it in the new database. This assigns a new, sequential ID.
                temp_db_manager.store_enrollment_snapshot(snapshot_data)
                new_snapshot_id = temp_db_manager.get_latest_snapshot_id()
                if new_snapshot_id is not None:
                    id_map[old_snapshot_id] = new_snapshot_id
            else:
                logger.warning(
                    f"Could not retrieve data for snapshot ID {old_snapshot_id}. Skipping."
                )

        logger.info("Successfully transferred all snapshots to the new database.")

        # 5. Transfer reporting_log entries, updating foreign keys
        logger.info("Re-linking reporting log...")
        with source_db_manager.get_connection() as source_conn:
            source_conn.row_factory = sqlite3.Row
            source_cursor = source_conn.cursor()
            source_cursor.execute("SELECT * FROM reporting_log")
            reporting_logs = source_cursor.fetchall()

            if reporting_logs:
                with temp_db_manager.get_connection() as temp_conn:
                    temp_cursor = temp_conn.cursor()
                    for log in reporting_logs:
                        old_id = log["reported_snapshot_id"]
                        new_id = id_map.get(old_id)
                        if new_id:
                            temp_cursor.execute(
                                """
                                INSERT INTO reporting_log (reported_snapshot_id, report_timestamp, changes_found, created_at)
                                VALUES (?, ?, ?, ?)
                                """,
                                (
                                    new_id,
                                    log["report_timestamp"],
                                    log["changes_found"],
                                    log["created_at"],
                                ),
                            )
                        else:
                            logger.warning(
                                f"Could not find new snapshot ID for old reporting log entry (snapshot_id={old_id}). Skipping."
                            )
                    temp_conn.commit()
                logger.info(
                    f"Successfully transferred {len(reporting_logs)} reporting log entries."
                )
            else:
                logger.info("No reporting log entries to transfer.")

        # 6. Replace the original database with the new, reordered one
        # It's critical to close connections before moving the file
        del source_db_manager
        del temp_db_manager

        shutil.move(str(temp_db_path), source_db_path)
        logger.info(
            "✅ Successfully replaced original database with the reordered version."
        )

        return 0

    except Exception as e:
        logger.error(f"❌ An unexpected error occurred: {e}", exc_info=True)
        # Clean up temp file if it exists
        if "temp_db_path" in locals() and temp_db_path.exists():
            temp_db_path.unlink()
        return 1


def main():
    """Main function to parse arguments and run the reordering script."""
    parser = argparse.ArgumentParser(
        description="Reorder snapshots in a database chronologically.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Example usage:
  # Reorder snapshots for the 'Spring 2025' semester
  uv run ./scripts/reorder_snapshots.py "Spring 2025"

  # Perform a dry run to see the new order without changing the database
  uv run ./scripts/reorder_snapshots.py "Spring 2025" --dry-run
""",
    )

    parser.add_argument(
        "semester",
        help='The semester identifier for the database (e.g., "Spring 2025").',
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show the new order of snapshots without modifying the database file.",
    )
    parser.add_argument(
        "-v", "--verbose", action="store_true", help="Enable verbose (DEBUG) logging."
    )

    args = parser.parse_args()

    setup_logging(args.verbose)

    return reorder_snapshots(args.semester, args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
