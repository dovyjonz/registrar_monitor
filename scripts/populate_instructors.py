import argparse
import os
import sys

# Add project root to sys.path to allow imports from the src directory
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, project_root)

from src.registrarmonitor.data.instructor_populator import populate_instructors  # noqa: E402
from src.registrarmonitor.core import setup_logging  # noqa: E402


def main():
    """Main function to run the script from the command line."""
    parser = argparse.ArgumentParser(
        description="Populate instructor data from an Excel file into the Registrar Monitor database.",
        formatter_class=argparse.RawTextHelpFormatter,
        epilog="""
Example usage:
  # Perform a dry run to see what would be changed
  python scripts/populate_instructors.py data/Fall_2025.db "assets/downloads/latest.xls" --dry-run

  # Execute the update
  python scripts/populate_instructors.py data/Fall_2025.db "assets/downloads/latest.xls"
""",
    )
    parser.add_argument(
        "db_path", help="Path to the SQLite database file (e.g., data/Fall_2025.db)."
    )
    parser.add_argument(
        "excel_path",
        help="Path to the source Excel file with instructor data (e.g., assets/downloads/latest.xls).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Simulate the update process without committing any changes to the database.",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )

    args = parser.parse_args()

    # Setup simple console logging
    log_level = "DEBUG" if args.debug else "INFO"
    setup_logging(level=log_level, enable_console=True, enable_file=False)

    success = populate_instructors(args.db_path, args.excel_path, args.dry_run)
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
