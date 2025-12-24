#!/usr/bin/env python3
"""
Consolidated database management script for Registrar Monitor.

This script provides essential database operations that may be needed
for automation or advanced maintenance tasks not covered by the main CLI.
"""

import argparse
import logging
import sys
from pathlib import Path

# Add the src directory to Python path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from registrarmonitor.data.database_manager import DatabaseManager


def setup_logging(verbose: bool = False):
    """Setup logging configuration."""
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )


def backup_database(db_manager: DatabaseManager, backup_path: str) -> int:
    """Create a backup of the database."""
    try:
        import shutil

        backup_file = Path(backup_path)
        backup_file.parent.mkdir(parents=True, exist_ok=True)

        shutil.copy2(db_manager.db_path, backup_file)

        print(f"✅ Database backed up to: {backup_file}")
        return 0

    except Exception as e:
        print(f"❌ Backup failed: {e}")
        return 1


def query_database(db_manager: DatabaseManager, query: str, limit: int = 100) -> int:
    """Execute a custom query on the database."""
    try:
        print("🔍 Executing query...")

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()

            # Add limit to SELECT queries for safety
            if (
                query.strip().upper().startswith("SELECT")
                and "LIMIT" not in query.upper()
            ):
                query = f"{query.rstrip(';')} LIMIT {limit}"

            cursor.execute(query)

            if query.strip().upper().startswith("SELECT"):
                results = cursor.fetchall()

                if results:
                    # Print column names if available
                    if cursor.description:
                        columns = [desc[0] for desc in cursor.description]
                        print(" | ".join(columns))
                        print("-" * (len(" | ".join(columns))))

                    # Print results
                    for row in results:
                        print(" | ".join(str(value) for value in row))

                    print(f"\n({len(results)} rows)")

                    if len(results) == limit:
                        print(
                            f"⚠️  Results limited to {limit} rows. Use --limit to change."
                        )
                else:
                    print("No results found.")
            else:
                conn.commit()
                print("✅ Query executed successfully")

        return 0

    except Exception as e:
        print(f"❌ Query failed: {e}")
        return 1


def vacuum_database(db_manager: DatabaseManager) -> int:
    """Vacuum the database to reclaim space and optimize performance."""
    try:
        print("🔧 Vacuuming database...")

        with db_manager.get_connection() as conn:
            conn.execute("VACUUM")

        print("✅ Database vacuumed successfully")
        return 0

    except Exception as e:
        print(f"❌ Vacuum failed: {e}")
        return 1


def export_csv(db_manager: DatabaseManager, table: str, output_path: str) -> int:
    """Export a table to CSV format."""
    try:
        import csv

        print(f"📊 Exporting {table} to CSV...")

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(f"SELECT * FROM {table}")

            results = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            output_file = Path(output_path)
            output_file.parent.mkdir(parents=True, exist_ok=True)

            with open(output_file, "w", newline="", encoding="utf-8") as csvfile:
                writer = csv.writer(csvfile)
                writer.writerow(columns)  # Header
                writer.writerows(results)

        print(f"✅ Exported {len(results)} rows to {output_file}")
        return 0

    except Exception as e:
        print(f"❌ Export failed: {e}")
        return 1


def main():
    """Main function."""
    parser = argparse.ArgumentParser(
        description="Advanced database management for Registrar Monitor",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s backup data/backup.db           # Create database backup
  %(prog)s query "SELECT COUNT(*) FROM courses"  # Run query
  %(prog)s vacuum                          # Optimize database
  %(prog)s export courses courses.csv      # Export table to CSV

Note: For basic operations like stats, cleanup, and migration, use:
  monitor db stats
  monitor db cleanup
  monitor db migrate
        """,
    )

    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose logging"
    )

    parser.add_argument(
        "--semester", "-s", help="Semester identifier for database operations"
    )

    subparsers = parser.add_subparsers(dest="command", help="Available commands")

    # Backup command
    backup_parser = subparsers.add_parser("backup", help="Create database backup")
    backup_parser.add_argument("path", help="Backup file path")

    # Query command
    query_parser = subparsers.add_parser("query", help="Execute custom SQL query")
    query_parser.add_argument("sql", help="SQL query to execute")
    query_parser.add_argument(
        "--limit", type=int, default=100, help="Limit for SELECT queries (default: 100)"
    )

    # Vacuum command
    subparsers.add_parser("vacuum", help="Vacuum database to reclaim space")

    # Export command
    export_parser = subparsers.add_parser("export", help="Export table to CSV")
    export_parser.add_argument("table", help="Table name to export")
    export_parser.add_argument("output", help="Output CSV file path")

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return 1

    # Setup logging
    setup_logging(args.verbose)

    # Initialize database manager
    try:
        if args.semester:
            db_manager = DatabaseManager.create_for_semester(args.semester)
        else:
            db_manager = DatabaseManager()
    except Exception as e:
        print(f"❌ Failed to initialize database: {e}")
        return 1

    # Execute command
    if args.command == "backup":
        return backup_database(db_manager, args.path)

    elif args.command == "query":
        return query_database(db_manager, args.sql, args.limit)

    elif args.command == "vacuum":
        return vacuum_database(db_manager)

    elif args.command == "export":
        return export_csv(db_manager, args.table, args.output)

    else:
        print(f"❌ Unknown command: {args.command}")
        return 1


if __name__ == "__main__":
    sys.exit(main())
