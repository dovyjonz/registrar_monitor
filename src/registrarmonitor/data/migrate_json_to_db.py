"""
Migration script to import existing JSON snapshot files into the SQLite database.

This script scans the data directory for JSON files and imports them into the
database using the DatabaseManager.
"""

import json
import logging
from pathlib import Path
from typing import List

from ..config import get_config
from ..core import get_logger
from ..models import Course, EnrollmentSnapshot, Section
from .database_manager import DatabaseManager


class JSONMigrator:
    """Handles migration of JSON snapshot files to database."""

    def __init__(self):
        config = get_config()
        self.data_dir = Path(config["directories"]["data_storage"])

        # Database managers will be created per semester
        self.db_managers = {}

        # Set up logging
        self.logger = get_logger(__name__)

    def find_json_files(self) -> List[Path]:
        """
        Find all JSON snapshot files in the data directory.

        Returns:
            List[Path]: List of JSON file paths
        """
        json_files: list[Path] = []

        if not self.data_dir.exists():
            self.logger.warning(f"Data directory {self.data_dir} does not exist")
            return json_files

        for file_path in self.data_dir.glob("*.json"):
            json_files.append(file_path)

        # Sort by filename to process in chronological order
        json_files.sort()

        self.logger.info(f"Found {len(json_files)} JSON files to migrate")
        return json_files

    def load_json_snapshot(self, file_path: Path) -> EnrollmentSnapshot:
        """
        Load a JSON file and convert it to an EnrollmentSnapshot object.

        Args:
            file_path: Path to JSON file

        Returns:
            EnrollmentSnapshot: Loaded snapshot

        Raises:
            Exception: If file cannot be loaded or parsed
        """
        try:
            with open(file_path, "r") as f:
                data = json.load(f)

            snapshot = EnrollmentSnapshot(
                timestamp=data["timestamp"],
                semester=data["semester"],
                overall_fill=data["overall_fill"],
            )

            # Load courses and sections
            for course_code, course_data in data["courses"].items():
                course = Course(
                    course_code=course_code,
                    department=course_data["department"],
                    average_fill=course_data["average_fill"],
                )

                # Load sections
                for section_id, section_data in course_data["sections"].items():
                    section = Section(
                        section_id=section_id,
                        section_type=section_data["section_type"],
                        enrollment=section_data["enrollment"],
                        capacity=section_data["capacity"],
                        fill=section_data["fill"],
                    )
                    course.sections[section_id] = section

                snapshot.courses[course_code] = course

            return snapshot

        except json.JSONDecodeError as e:
            self.logger.error(f"Invalid JSON in file {file_path}: {e}")
            raise
        except KeyError as e:
            self.logger.error(f"Missing required field in {file_path}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Error loading {file_path}: {e}")
            raise

    def check_snapshot_exists(self, timestamp: str, semester: str) -> bool:
        """
        Check if a snapshot with the given timestamp already exists in the database.

        Args:
            timestamp: Snapshot timestamp
            semester: Semester identifier

        Returns:
            bool: True if snapshot exists
        """
        try:
            db_manager = self._get_db_manager(semester)
            with db_manager.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "SELECT COUNT(*) FROM snapshots WHERE timestamp = ?", (timestamp,)
                )
                result = cursor.fetchone()
                if result is None:
                    return False
                count: int = result[0]
                return count > 0
        except Exception as e:
            self.logger.error(f"Error checking if snapshot exists: {e}")
            return False

    def migrate_file(self, file_path: Path, force: bool = False) -> bool:
        """
        Migrate a single JSON file to the database.

        Args:
            file_path: Path to JSON file
            force: If True, skip existence check

        Returns:
            bool: True if migration was successful
        """
        try:
            self.logger.info(f"Migrating {file_path.name}")

            # Load snapshot from JSON
            snapshot = self.load_json_snapshot(file_path)

            # Check if already exists (unless forcing)
            if not force and self.check_snapshot_exists(
                snapshot.timestamp, snapshot.semester
            ):
                self.logger.info(
                    f"Snapshot {snapshot.timestamp} already exists, skipping"
                )
                return True

            # Store in database using semester-specific manager
            db_manager = self._get_db_manager(snapshot.semester)
            db_manager.store_enrollment_snapshot(snapshot)
            self.logger.info(
                f"Successfully migrated {file_path.name} to {snapshot.semester} database"
            )
            return True

        except Exception as e:
            self.logger.error(f"Failed to migrate {file_path.name}: {e}")
            return False

    def migrate_all(self, force: bool = False, dry_run: bool = False) -> dict:
        """
        Migrate all JSON files to the database.

        Args:
            force: If True, migrate even if snapshot already exists
            dry_run: If True, don't actually migrate, just report what would be done

        Returns:
            dict: Migration results summary
        """
        json_files = self.find_json_files()

        if not json_files:
            self.logger.info("No JSON files found to migrate")
            return {"total": 0, "success": 0, "skipped": 0, "failed": 0}

        results = {"total": len(json_files), "success": 0, "skipped": 0, "failed": 0}

        self.logger.info(f"Starting migration of {len(json_files)} files")
        if dry_run:
            self.logger.info("DRY RUN MODE - No actual migration will occur")

        for file_path in json_files:
            try:
                if dry_run:
                    # Just load and validate the file
                    snapshot = self.load_json_snapshot(file_path)
                    exists = self.check_snapshot_exists(
                        snapshot.timestamp, snapshot.semester
                    )

                    if exists and not force:
                        self.logger.info(
                            f"WOULD SKIP: {file_path.name} (already exists)"
                        )
                        results["skipped"] += 1
                    else:
                        self.logger.info(f"WOULD MIGRATE: {file_path.name}")
                        results["success"] += 1
                else:
                    # Actually migrate
                    if self.migrate_file(file_path, force):
                        results["success"] += 1
                    else:
                        results["failed"] += 1

            except Exception as e:
                self.logger.error(f"Error processing {file_path.name}: {e}")
                results["failed"] += 1

        # Print summary
        self.logger.info(
            f"Migration complete: {results['success']} successful, "
            f"{results['skipped']} skipped, {results['failed']} failed"
        )

        return results

    def _get_db_manager(self, semester: str) -> DatabaseManager:
        """
        Get or create a database manager for the specified semester.

        Args:
            semester: Semester identifier

        Returns:
            DatabaseManager: Database manager for the semester
        """
        if semester not in self.db_managers:
            self.db_managers[semester] = DatabaseManager.create_for_semester(semester)
        db_manager: DatabaseManager = self.db_managers[semester]
        return db_manager

    def validate_migration(self) -> bool:
        """
        Validate that migration was successful by comparing counts.

        Returns:
            bool: True if validation passes
        """
        try:
            json_files = self.find_json_files()

            # Group files by semester
            semester_file_counts: dict[str, int] = {}
            for file_path in json_files:
                try:
                    snapshot = self.load_json_snapshot(file_path)
                    semester = snapshot.semester
                    semester_file_counts[semester] = (
                        semester_file_counts.get(semester, 0) + 1
                    )
                except Exception as e:
                    self.logger.error(
                        f"Error loading {file_path.name} for validation: {e}"
                    )
                    continue

            total_db_snapshots = 0
            validation_passed = True

            for semester, file_count in semester_file_counts.items():
                try:
                    db_manager = self._get_db_manager(semester)
                    with db_manager.get_connection() as conn:
                        cursor = conn.cursor()
                        cursor.execute("SELECT COUNT(*) FROM snapshots")
                        db_snapshot_count = cursor.fetchone()[0]

                    total_db_snapshots += db_snapshot_count

                    self.logger.info(
                        f"{semester}: JSON files: {file_count}, DB snapshots: {db_snapshot_count}"
                    )

                    if file_count != db_snapshot_count:
                        validation_passed = False
                        self.logger.warning(f"⚠️ {semester}: Count mismatch")

                except Exception as e:
                    self.logger.error(f"Error validating {semester}: {e}")
                    validation_passed = False

            if validation_passed:
                self.logger.info("✅ Migration validation passed for all semesters")
                return True
            else:
                self.logger.warning("⚠️ Migration validation failed")
                return False

        except Exception as e:
            self.logger.error(f"Error during validation: {e}")
            return False


def main():
    """Main function for running migration."""
    import argparse

    parser = argparse.ArgumentParser(description="Migrate JSON snapshots to database")
    parser.add_argument(
        "--force",
        action="store_true",
        help="Force migration even if snapshot already exists",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be migrated without actually doing it",
    )
    parser.add_argument(
        "--validate", action="store_true", help="Validate migration results"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    # Set up logging
    level = logging.DEBUG if args.verbose else logging.INFO
    logging.basicConfig(
        level=level, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
    )

    try:
        migrator = JSONMigrator()

        if args.validate:
            migrator.validate_migration()
        else:
            results = migrator.migrate_all(force=args.force, dry_run=args.dry_run)

            if not args.dry_run and results["success"] > 0:
                print("\n🔍 Running validation...")
                migrator.validate_migration()

    except Exception as e:
        print(f"❌ Migration failed: {e}")
        exit(1)


if __name__ == "__main__":
    main()
