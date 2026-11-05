"""
Database manager for handling SQLite operations for enrollment data.

Enrollment history lives in the checkpointed v2 schema owned by
``CheckpointedStateStore``. This module opens one database per semester and
exposes the read and write operations the rest of the application needs.
"""

import re
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from ..config import get_settings
from ..core import get_logger
from ..models import EnrollmentSnapshot
from ..validation import validate_directory_exists
from .checkpointed_state import CHECKPOINTED_SCHEMA_VERSION, CheckpointedStateStore


class DatabaseManager:
    """Manages SQLite operations for one semester's enrollment data."""

    def __init__(
        self,
        db_path: str | None = None,
        semester: str | None = None,
        *,
        read_only: bool = False,
    ):
        """
        Initialize database manager.

        Args:
            db_path: Optional path to database file. If None, uses config default with semester.
            semester: Optional semester identifier for database naming.
            read_only: Open an existing database with SQLite write protection.
        """
        self._uses_configured_path = db_path is None
        self._read_only = read_only
        if db_path is None:
            data_dir = get_settings().directories.data_storage
            validate_directory_exists(data_dir, create_if_missing=not read_only)

            if semester:
                # Create semester-specific database filename
                safe_semester = self._sanitize_semester_name(semester)
                self.db_path = Path(data_dir) / f"enrollment_{safe_semester}.db"
            else:
                # Fallback to default database name
                self.db_path = Path(data_dir) / "enrollment.db"
        else:
            self.db_path = Path(db_path)

        # Store semester for reference
        self.semester = semester

        # Ensure parent directory exists
        if not read_only:
            self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Set up logging
        self.logger = get_logger(__name__)

        self._checkpointed_store = self._open_store()

    def _open_store(self) -> CheckpointedStateStore:
        """Open the v2 store, creating it for a new explicit database path."""
        if not self._read_only and self._schema_version() == 0:
            if self._uses_configured_path and self.semester:
                raise sqlite3.DatabaseError(
                    "checkpointed storage must be initialized explicitly; "
                    f"run `monitor db initialize --semester {self.semester!r}`"
                )
            return CheckpointedStateStore(self.db_path)
        version = self._schema_version()
        if version != CHECKPOINTED_SCHEMA_VERSION:
            raise sqlite3.DatabaseError(
                f"unsupported database schema version {version}"
            )
        return CheckpointedStateStore(
            self.db_path, initialize=False, read_only=self._read_only
        )

    def _schema_version(self) -> int:
        with self.get_connection() as conn:
            return int(conn.execute("PRAGMA user_version").fetchone()[0])

    def _sanitize_semester_name(self, semester: str) -> str:
        """
        Sanitize semester name for use in filename.

        Args:
            semester: Raw semester string

        Returns:
            str: Sanitized semester name safe for filename
        """
        # Remove invalid characters and replace spaces with underscores
        safe_name = re.sub(r"[^\w\s-]", "", semester.strip())
        safe_name = re.sub(r"[-\s]+", "_", safe_name)
        return safe_name.lower()

    @contextmanager
    def get_connection(self):
        """
        Context manager for database connections with proper error handling.

        Yields:
            sqlite3.Connection: Database connection
        """
        conn = None
        try:
            conn = sqlite3.connect(
                f"file:{self.db_path}?mode=ro" if self._read_only else self.db_path,
                timeout=5.0,
                uri=self._read_only,
            )
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            if self._read_only:
                conn.execute("PRAGMA query_only = ON")
            else:
                conn.execute("PRAGMA journal_mode = WAL")
                conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA cache_size = -16384")
            conn.row_factory = sqlite3.Row  # Enable column access by name
            yield conn
        except sqlite3.Error as e:
            self.logger.error(f"Database error: {e}")
            if conn:
                conn.rollback()
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error in database connection: {e}")
            if conn:
                conn.rollback()
            raise
        finally:
            if conn:
                conn.close()

    def store_enrollment_snapshot(self, snapshot: EnrollmentSnapshot) -> None:
        """
        Store a complete enrollment snapshot in the database.

        A poll identical to the latest stored state only advances its
        last-seen time.

        Args:
            snapshot: EnrollmentSnapshot object containing all data

        Raises:
            sqlite3.Error: If database operation fails
        """
        if self.semester and snapshot.semester != self.semester:
            self.logger.warning(
                f"Snapshot semester '{snapshot.semester}' differs from "
                f"database semester '{self.semester}'"
            )
        self._checkpointed_store.write_snapshot(snapshot)

    def _determine_status(self, fill_percentage: float) -> str:
        """
        Determine enrollment status based on fill percentage.

        Args:
            fill_percentage: Fill percentage (0.0 to 1.0+)

        Returns:
            str: Status ('OPEN', 'NEAR', 'FULL')
        """
        if fill_percentage >= 1.0:
            return "FULL"
        elif fill_percentage >= 0.75:
            return "NEAR"
        else:
            return "OPEN"

    @staticmethod
    def get_semester_databases(data_dir: str | None = None) -> dict[str, Path]:
        """
        Get all semester-specific database files.

        Args:
            data_dir: Optional data directory path. If None, uses config default.

        Returns:
            Dict[str, Path]: Dictionary mapping semester names to database paths
        """
        if data_dir is None:
            data_dir = get_settings().directories.data_storage

        data_path = Path(data_dir)
        if not data_path.exists():
            return {}

        semester_dbs = {}

        # Find all enrollment database files
        for db_file in data_path.glob("enrollment_*.db"):
            # Extract semester name from filename
            filename = db_file.stem  # Gets filename without extension
            if filename.startswith("enrollment_"):
                semester_part = filename[11:]  # Remove "enrollment_" prefix
                # Convert back from sanitized format
                semester_name = semester_part.replace("_", " ").title()
                semester_dbs[semester_name] = db_file

        return semester_dbs

    @staticmethod
    def create_for_semester(
        semester: str, data_dir: str | None = None
    ) -> "DatabaseManager":
        """
        Create a DatabaseManager instance for a specific semester.

        Args:
            semester: Semester identifier
            data_dir: Optional data directory path. If None, uses config default.

        Returns:
            DatabaseManager: Instance configured for the specified semester
        """
        if data_dir:
            # Create semester-specific path manually
            safe_semester = DatabaseManager._sanitize_semester_name_static(semester)
            db_path = Path(data_dir) / f"enrollment_{safe_semester}.db"
            return DatabaseManager(db_path=str(db_path), semester=semester)
        else:
            return DatabaseManager(semester=semester)

    @staticmethod
    def create_read_only_for_semester(
        semester: str, data_dir: str | None = None
    ) -> "DatabaseManager":
        """Open an existing semester database with SQLite write protection."""
        if data_dir:
            safe_semester = DatabaseManager._sanitize_semester_name_static(semester)
            db_path = Path(data_dir) / f"enrollment_{safe_semester}.db"
            return DatabaseManager(
                db_path=str(db_path), semester=semester, read_only=True
            )
        return DatabaseManager(semester=semester, read_only=True)

    @staticmethod
    def _sanitize_semester_name_static(semester: str) -> str:
        """
        Static version of semester name sanitization.

        Args:
            semester: Raw semester string

        Returns:
            str: Sanitized semester name safe for filename
        """
        # Remove invalid characters and replace spaces with underscores
        safe_name = re.sub(r"[^\w\s-]", "", semester.strip())
        safe_name = re.sub(r"[-\s]+", "_", safe_name)
        return safe_name.lower()

    def get_latest_snapshot_timestamp(self, semester: str | None = None) -> str | None:
        """
        Get timestamp of the most recent snapshot.

        Args:
            semester: Optional semester filter

        Returns:
            Optional[str]: Latest timestamp or None if no snapshots exist
        """
        try:
            with self.get_connection() as conn:
                if semester:
                    row = conn.execute(
                        """
                        SELECT observed_at FROM state_snapshot
                        WHERE semester = ?
                        ORDER BY sequence_no DESC LIMIT 1
                        """,
                        (semester,),
                    ).fetchone()
                else:
                    row = conn.execute(
                        """
                        SELECT observed_at FROM state_snapshot
                        ORDER BY sequence_no DESC LIMIT 1
                        """
                    ).fetchone()
                return str(row[0]) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting latest snapshot: {e}")
            raise

    def get_enrollment_summary(self, snapshot_id: int) -> dict[str, int]:
        """
        Get enrollment summary for a snapshot.

        Args:
            snapshot_id: Snapshot ID

        Returns:
            Dict[str, int]: Summary with counts by status
        """
        snapshot = self.get_snapshot_data(snapshot_id)
        summary = {"OPEN": 0, "NEAR": 0, "FULL": 0}
        if snapshot is None:
            return summary
        for course in snapshot.courses.values():
            for section in course.sections.values():
                summary[self._determine_status(section.fill)] += 1
        return summary

    def get_latest_snapshot_id(self) -> int | None:
        """Finds the ID of the most recent snapshot."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT snapshot_id FROM state_snapshot
                    ORDER BY sequence_no DESC LIMIT 1
                    """
                ).fetchone()
                return int(row[0]) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting latest snapshot ID: {e}")
            raise

    def get_first_snapshot_id(self) -> int | None:
        """Find the ID of the earliest retained snapshot."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT snapshot_id FROM state_snapshot
                    ORDER BY sequence_no ASC LIMIT 1
                    """
                ).fetchone()
                return int(row[0]) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting first snapshot ID: {e}")
            raise

    def get_previous_snapshot_id(self, snapshot_id: int) -> int | None:
        """Return the snapshot immediately preceding ``snapshot_id``."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT previous.snapshot_id
                    FROM state_snapshot current
                    JOIN state_snapshot previous
                      ON previous.sequence_no = current.sequence_no - 1
                    WHERE current.snapshot_id = ?
                    """,
                    (snapshot_id,),
                ).fetchone()
                return int(row[0]) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting previous snapshot ID: {e}")
            raise

    def get_latest_snapshot_last_seen_at(self) -> str | None:
        """Return freshness time for the latest stored state."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    "SELECT last_seen_at FROM state_snapshot "
                    "ORDER BY sequence_no DESC LIMIT 1"
                ).fetchone()
                return str(row[0]) if row else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting latest freshness time: {e}")
            raise

    def get_database_stats(self) -> dict[str, Any]:
        """Return database statistics."""
        try:
            with self.get_connection() as conn:
                snapshot_count = int(
                    conn.execute("SELECT count(*) FROM state_snapshot").fetchone()[0]
                )
                course_count = int(
                    conn.execute("SELECT count(*) FROM course_catalog").fetchone()[0]
                )
                section_count = int(
                    conn.execute("SELECT count(*) FROM section_catalog").fetchone()[0]
                )
                date_range = conn.execute(
                    "SELECT min(observed_at), max(observed_at) FROM state_snapshot"
                ).fetchone()
            return {
                "snapshots": snapshot_count,
                "courses": course_count,
                "sections": section_count,
                "earliest_snapshot": date_range[0],
                "latest_snapshot": date_range[1],
            }
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting database statistics: {e}")
            raise

    def get_last_reported_snapshot_id(self) -> int | None:
        """Finds the ID of the snapshot from the last report log."""
        try:
            with self.get_connection() as conn:
                result = conn.execute(
                    "SELECT reported_snapshot_id FROM reporting_log_v2 "
                    "ORDER BY report_timestamp DESC LIMIT 1"
                ).fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting last reported snapshot ID: {e}")
            raise

    def was_snapshot_reported(
        self, snapshot_id: int, *, changes_were_found: bool
    ) -> bool:
        """Return whether an exact snapshot/reportability pair was logged."""
        try:
            with self.get_connection() as conn:
                row = conn.execute(
                    """
                    SELECT 1 FROM reporting_log_v2
                    WHERE reported_snapshot_id = ? AND changes_found = ?
                    LIMIT 1
                    """,
                    (snapshot_id, int(changes_were_found)),
                ).fetchone()
                return row is not None
        except sqlite3.Error as error:
            self.logger.error(
                "Database error checking reporting log for snapshot %s: %s",
                snapshot_id,
                error,
            )
            raise

    def add_reporting_log(self, snapshot_id: int, changes_were_found: bool) -> None:
        """Adds a new entry to the reporting log."""
        try:
            with self.get_connection() as conn:
                conn.execute(
                    """
                    INSERT INTO reporting_log_v2(
                        reported_snapshot_id, report_timestamp, changes_found
                    ) VALUES (?, ?, ?)
                    """,
                    (snapshot_id, datetime.now().isoformat(), int(changes_were_found)),
                )
                conn.commit()
                self.logger.info(
                    f"Added reporting log entry for snapshot {snapshot_id}"
                )
        except sqlite3.Error as e:
            self.logger.error(f"Database error adding reporting log: {e}")
            raise

    def get_snapshot_data(self, snapshot_id: int) -> EnrollmentSnapshot | None:
        """Reconstructs an EnrollmentSnapshot object for a given snapshot ID."""
        try:
            return self._checkpointed_store.get_snapshot_data(snapshot_id)
        except sqlite3.Error as e:
            self.logger.error(
                f"Database error getting snapshot data for ID {snapshot_id}: {e}"
            )
            raise

    def get_course_history(
        self, course_code: str, semester: str | None = None
    ) -> list[dict[str, Any]]:
        """
        Get historical enrollment data for a specific course.

        Args:
            course_code: Course code to query (e.g. "CSCI 101")
            semester: Optional semester filter

        Returns:
            list[Dict]: List of dictionaries containing timestamp and section info
        """
        try:
            return self._checkpointed_store.get_course_history(course_code, semester)
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting course history: {e}")
            raise
