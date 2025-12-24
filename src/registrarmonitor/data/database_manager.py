"""
Database manager for handling SQLite operations for enrollment data.

This module provides database operations for storing enrollment snapshots,
courses, sections, and enrollment data in a normalized SQLite database.
"""

import logging
import re
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Optional

from ..config import get_config
from ..core import get_logger
from ..models import EnrollmentSnapshot
from ..validation import validate_directory_exists


class DatabaseManager:
    """Manages SQLite database operations for enrollment data."""

    def __init__(self, db_path: Optional[str] = None, semester: Optional[str] = None):
        """
        Initialize database manager.

        Args:
            db_path: Optional path to database file. If None, uses config default with semester.
            semester: Optional semester identifier for database naming.
        """
        if db_path is None:
            config = get_config()
            data_dir = config["directories"]["data_storage"]
            validate_directory_exists(data_dir, create_if_missing=True)

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
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

        # Set up logging
        self.logger = get_logger(__name__)

        # Initialize database
        self._init_database()

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
            conn = sqlite3.connect(self.db_path)
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

    def _init_database(self):
        """Initialize database schema if it doesn't exist."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Create courses table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS courses (
                        course_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_code TEXT NOT NULL UNIQUE,
                        course_title TEXT,
                        department TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create sections table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS sections (
                        section_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        course_id INTEGER NOT NULL,
                        section_code TEXT NOT NULL,
                        section_type TEXT,
                        instructor TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (course_id) REFERENCES courses (course_id),
                        UNIQUE(course_id, section_code)
                    )
                """)

                # Create snapshots table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS snapshots (
                        snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        timestamp TEXT NOT NULL UNIQUE,
                        semester TEXT NOT NULL,
                        overall_fill REAL NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    )
                """)

                # Create enrollment_data table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS enrollment_data (
                        enrollment_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        snapshot_id INTEGER NOT NULL,
                        section_id INTEGER NOT NULL,
                        status TEXT NOT NULL CHECK (status IN ('OPEN', 'NEAR', 'FULL')),
                        enrollment_count INTEGER NOT NULL,
                        capacity_count INTEGER NOT NULL,
                        fill_percentage REAL NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (snapshot_id) REFERENCES snapshots (snapshot_id),
                        FOREIGN KEY (section_id) REFERENCES sections (section_id),
                        UNIQUE(snapshot_id, section_id)
                    )
                """)

                # Create reporting_log table
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS reporting_log (
                        report_id INTEGER PRIMARY KEY AUTOINCREMENT,
                        reported_snapshot_id INTEGER NOT NULL,
                        report_timestamp TEXT NOT NULL,
                        changes_found INTEGER NOT NULL,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (reported_snapshot_id) REFERENCES snapshots (snapshot_id)
                    )
                """)

                # Create indexes for better performance
                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_courses_code
                    ON courses (course_code)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_sections_course_id
                    ON sections (course_id)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp
                    ON snapshots (timestamp)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_enrollment_snapshot
                    ON enrollment_data (snapshot_id)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_enrollment_section
                    ON enrollment_data (section_id)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reporting_log_timestamp
                    ON reporting_log (report_timestamp)
                """)

                cursor.execute("""
                    CREATE INDEX IF NOT EXISTS idx_reporting_log_snapshot
                    ON reporting_log (reported_snapshot_id)
                """)

                conn.commit()
                self.logger.debug("Database schema initialized successfully")

        except sqlite3.Error as e:
            self.logger.error(f"Failed to initialize database schema: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during database initialization: {e}")
            raise

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

    def insert_course(
        self,
        course_code: str,
        course_title: Optional[str] = None,
        department: Optional[str] = None,
    ) -> int:
        """
        Insert or get existing course.

        Args:
            course_code: Unique course code
            course_title: Optional course title
            department: Optional department code

        Returns:
            int: Course ID

        Raises:
            sqlite3.Error: Database operation failed
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Try to get existing course
                cursor.execute(
                    "SELECT course_id FROM courses WHERE course_code = ?",
                    (course_code,),
                )
                result = cursor.fetchone()

                if result:
                    course_id = result[0]
                    # Update existing course if new information provided
                    if course_title or department:
                        cursor.execute(
                            """
                            UPDATE courses
                            SET course_title = COALESCE(?, course_title),
                                department = COALESCE(?, department),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE course_id = ?
                        """,
                            (
                                course_title.strip() if course_title else None,
                                department,
                                course_id,
                            ),
                        )
                        conn.commit()
                else:
                    # Insert new course
                    cursor.execute(
                        """
                        INSERT INTO courses (course_code, course_title, department)
                        VALUES (?, ?, ?)
                    """,
                        (
                            course_code,
                            course_title.strip() if course_title else None,
                            department,
                        ),
                    )
                    course_id_raw = cursor.lastrowid
                    if course_id_raw is None:
                        raise sqlite3.Error("Failed to get course ID after insert")
                    course_id = course_id_raw
                    conn.commit()

                self.logger.debug(f"Course {course_code} processed with ID {course_id}")
                return int(course_id)

        except sqlite3.IntegrityError as e:
            self.logger.error(f"Integrity error inserting course {course_code}: {e}")
            raise
        except sqlite3.Error as e:
            self.logger.error(f"Database error inserting course {course_code}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error inserting course {course_code}: {e}")
            raise

    def insert_section(
        self,
        course_id: int,
        section_code: str,
        section_type: Optional[str] = None,
        instructor: Optional[str] = None,
    ) -> int:
        """
        Insert or get existing section.

        Args:
            course_id: Course ID
            section_code: Section code
            section_type: Optional section type
            instructor: Optional instructor name

        Returns:
            int: Section ID

        Raises:
            sqlite3.Error: Database operation failed
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Try to get existing section
                cursor.execute(
                    """
                    SELECT section_id FROM sections
                    WHERE course_id = ? AND section_code = ?
                """,
                    (course_id, section_code),
                )
                result = cursor.fetchone()

                if result:
                    section_id = result[0]
                    # Update existing section if new information provided
                    if section_type or instructor:
                        cursor.execute(
                            """
                            UPDATE sections
                            SET section_type = COALESCE(?, section_type),
                                instructor = COALESCE(?, instructor),
                                updated_at = CURRENT_TIMESTAMP
                            WHERE section_id = ?
                        """,
                            (section_type, instructor, section_id),
                        )
                        conn.commit()
                else:
                    # Insert new section
                    cursor.execute(
                        """
                        INSERT INTO sections (course_id, section_code, section_type, instructor)
                        VALUES (?, ?, ?, ?)
                    """,
                        (course_id, section_code, section_type, instructor),
                    )
                    section_id_raw = cursor.lastrowid
                    if section_id_raw is None:
                        raise sqlite3.Error("Failed to get section ID after insert")
                    section_id = section_id_raw
                    conn.commit()

                self.logger.debug(
                    f"Section {section_code} processed with ID {section_id}"
                )
                return int(section_id)

        except sqlite3.IntegrityError as e:
            self.logger.error(f"Integrity error inserting section {section_code}: {e}")
            raise
        except sqlite3.Error as e:
            self.logger.error(f"Database error inserting section {section_code}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error inserting section {section_code}: {e}")
            raise

    def insert_snapshot(
        self, timestamp: str, semester: str, overall_fill: float
    ) -> int:
        """
        Insert enrollment snapshot.

        Args:
            timestamp: Snapshot timestamp
            semester: Semester name
            overall_fill: Overall system fill percentage

        Returns:
            int: Snapshot ID

        Raises:
            sqlite3.Error: Database operation failed
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO snapshots (timestamp, semester, overall_fill)
                    VALUES (?, ?, ?)
                """,
                    (timestamp, semester, overall_fill),
                )

                snapshot_id_raw = cursor.lastrowid
                if snapshot_id_raw is None:
                    raise sqlite3.Error("Failed to get snapshot ID after insert")
                snapshot_id = snapshot_id_raw
                conn.commit()

                self.logger.info(
                    f"Snapshot inserted with ID {snapshot_id} for {timestamp}"
                )
                return int(snapshot_id)

        except sqlite3.IntegrityError as e:
            self.logger.error(f"Snapshot already exists for timestamp {timestamp}: {e}")
            raise
        except sqlite3.Error as e:
            self.logger.error(f"Database error inserting snapshot {timestamp}: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error inserting snapshot {timestamp}: {e}")
            raise

    def insert_enrollment_data(
        self,
        snapshot_id: int,
        section_id: int,
        enrollment_count: int,
        capacity_count: int,
    ) -> int:
        """
        Insert enrollment data for a section in a snapshot.

        Args:
            snapshot_id: Snapshot ID
            section_id: Section ID
            enrollment_count: Current enrollment
            capacity_count: Section capacity

        Returns:
            int: Enrollment data ID

        Raises:
            sqlite3.Error: Database operation failed
        """
        try:
            fill_percentage = (
                enrollment_count / capacity_count if capacity_count > 0 else 0.0
            )
            status = self._determine_status(fill_percentage)

            with self.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    INSERT INTO enrollment_data
                    (snapshot_id, section_id, status, enrollment_count, capacity_count, fill_percentage)
                    VALUES (?, ?, ?, ?, ?, ?)
                """,
                    (
                        snapshot_id,
                        section_id,
                        status,
                        enrollment_count,
                        capacity_count,
                        fill_percentage,
                    ),
                )

                enrollment_id_raw = cursor.lastrowid
                if enrollment_id_raw is None:
                    raise sqlite3.Error("Failed to get enrollment ID after insert")
                enrollment_id = enrollment_id_raw
                conn.commit()

                self.logger.debug(f"Enrollment data inserted with ID {enrollment_id}")
                return int(enrollment_id)

        except sqlite3.IntegrityError as e:
            self.logger.error(
                f"Enrollment data already exists for snapshot {snapshot_id}, section {section_id}: {e}"
            )
            raise
        except sqlite3.Error as e:
            self.logger.error(f"Database error inserting enrollment data: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error inserting enrollment data: {e}")
            raise

    def store_enrollment_snapshot(self, snapshot: EnrollmentSnapshot) -> None:
        """
        Store a complete enrollment snapshot in the database.

        This method is optimized for bulk inserts within a single transaction.

        Args:
            snapshot: EnrollmentSnapshot object containing all data

        Raises:
            sqlite3.Error: If database operation fails
        """
        self.logger.info(f"Storing snapshot for {snapshot.timestamp}")

        # Verify semester matches if specified during initialization
        if self.semester and snapshot.semester != self.semester:
            self.logger.warning(
                f"Snapshot semester '{snapshot.semester}' differs from "
                f"database semester '{self.semester}'"
            )

        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # --- Step 1: Insert the snapshot record ---
                cursor.execute(
                    """
                    INSERT INTO snapshots (timestamp, semester, overall_fill)
                    VALUES (?, ?, ?)
                    """,
                    (snapshot.timestamp, snapshot.semester, snapshot.overall_fill),
                )
                snapshot_id_raw = cursor.lastrowid
                if snapshot_id_raw is None:
                    raise sqlite3.Error("Failed to get snapshot ID after insert")
                snapshot_id = int(snapshot_id_raw)

                # --- Step 2: Bulk upsert courses ---
                courses_data = [
                    (
                        code,
                        course.course_title.strip() if course.course_title else None,
                        course.department,
                    )
                    for code, course in snapshot.courses.items()
                ]
                cursor.executemany(
                    """
                    INSERT INTO courses (course_code, course_title, department)
                    VALUES (?, ?, ?)
                    ON CONFLICT(course_code) DO UPDATE SET
                        course_title = COALESCE(excluded.course_title, course_title),
                        department = COALESCE(excluded.department, department),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    courses_data,
                )

                # --- Step 3: Fetch course IDs ---
                course_codes = [code for code in snapshot.courses.keys()]
                placeholders = ",".join(["?"] * len(course_codes))
                cursor.execute(
                    f"SELECT course_code, course_id FROM courses WHERE course_code IN ({placeholders})",
                    course_codes,
                )
                course_id_map = {row[0]: row[1] for row in cursor.fetchall()}

                # --- Step 4: Bulk upsert sections ---
                sections_data = []
                for course_code, course in snapshot.courses.items():
                    course_id = course_id_map.get(course_code)
                    if course_id is None:
                        self.logger.warning(
                            f"Could not find course_id for {course_code}"
                        )
                        continue
                    for section_code, section in course.sections.items():
                        sections_data.append(
                            (course_id, section_code, section.section_type)
                        )

                cursor.executemany(
                    """
                    INSERT INTO sections (course_id, section_code, section_type)
                    VALUES (?, ?, ?)
                    ON CONFLICT(course_id, section_code) DO UPDATE SET
                        section_type = COALESCE(excluded.section_type, section_type),
                        updated_at = CURRENT_TIMESTAMP
                    """,
                    sections_data,
                )

                # --- Step 5: Fetch section IDs ---
                # We need to build a map of (course_id, section_code) -> section_id
                cursor.execute(
                    "SELECT section_id, course_id, section_code FROM sections"
                )
                section_id_map = {(row[1], row[2]): row[0] for row in cursor.fetchall()}

                # --- Step 6: Bulk insert enrollment data ---
                enrollment_data_list = []
                for course_code, course in snapshot.courses.items():
                    course_id = course_id_map.get(course_code)
                    if course_id is None:
                        continue
                    for section_code, section in course.sections.items():
                        section_id = section_id_map.get((course_id, section_code))
                        if section_id is None:
                            self.logger.warning(
                                f"Could not find section_id for {course_code}/{section_code}"
                            )
                            continue
                        fill_percentage = (
                            section.enrollment / section.capacity
                            if section.capacity > 0
                            else 0.0
                        )
                        status = self._determine_status(fill_percentage)
                        enrollment_data_list.append(
                            (
                                snapshot_id,
                                section_id,
                                status,
                                section.enrollment,
                                section.capacity,
                                fill_percentage,
                            )
                        )

                cursor.executemany(
                    """
                    INSERT INTO enrollment_data
                    (snapshot_id, section_id, status, enrollment_count, capacity_count, fill_percentage)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    enrollment_data_list,
                )

                conn.commit()
                self.logger.info(
                    f"Successfully stored enrollment snapshot {snapshot_id} "
                    f"({len(courses_data)} courses, {len(enrollment_data_list)} sections)"
                )

        except sqlite3.IntegrityError as e:
            self.logger.error(f"Integrity error storing snapshot: {e}")
            raise
        except sqlite3.Error as e:
            self.logger.error(f"Database error storing snapshot: {e}")
            raise

    @staticmethod
    def get_semester_databases(data_dir: Optional[str] = None) -> Dict[str, Path]:
        """
        Get all semester-specific database files.

        Args:
            data_dir: Optional data directory path. If None, uses config default.

        Returns:
            Dict[str, Path]: Dictionary mapping semester names to database paths
        """
        if data_dir is None:
            config = get_config()
            data_dir = config["directories"]["data_storage"]

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
        semester: str, data_dir: Optional[str] = None
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

    def get_latest_snapshot_timestamp(
        self, semester: Optional[str] = None
    ) -> Optional[str]:
        """
        Get timestamp of the most recent snapshot.

        Args:
            semester: Optional semester filter

        Returns:
            Optional[str]: Latest timestamp or None if no snapshots exist
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                if semester:
                    cursor.execute(
                        """
                        SELECT timestamp FROM snapshots
                        WHERE semester = ?
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """,
                        (semester,),
                    )
                else:
                    cursor.execute("""
                        SELECT timestamp FROM snapshots
                        ORDER BY timestamp DESC
                        LIMIT 1
                    """)

                result = cursor.fetchone()
                return result[0] if result else None

        except sqlite3.Error as e:
            self.logger.error(f"Database error getting latest snapshot: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error getting latest snapshot: {e}")
            raise

    def get_enrollment_summary(self, snapshot_id: int) -> Dict[str, int]:
        """
        Get enrollment summary for a snapshot.

        Args:
            snapshot_id: Snapshot ID

        Returns:
            Dict[str, int]: Summary with counts by status
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                cursor.execute(
                    """
                    SELECT status, COUNT(*) as count
                    FROM enrollment_data
                    WHERE snapshot_id = ?
                    GROUP BY status
                """,
                    (snapshot_id,),
                )

                results = cursor.fetchall()
                summary = {row[0]: row[1] for row in results}

                # Ensure all statuses are present
                for status in ["OPEN", "NEAR", "FULL"]:
                    summary.setdefault(status, 0)

                return summary

        except sqlite3.Error as e:
            self.logger.error(f"Database error getting enrollment summary: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error getting enrollment summary: {e}")
            raise

    def cleanup_old_snapshots(self, keep_count: int = 50) -> int:
        """
        Remove old snapshots, keeping only the most recent ones.

        Args:
            keep_count: Number of snapshots to keep

        Returns:
            int: Number of snapshots deleted
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Get snapshot IDs to delete
                cursor.execute(
                    """
                    SELECT snapshot_id FROM snapshots
                    ORDER BY timestamp DESC
                    LIMIT -1 OFFSET ?
                """,
                    (keep_count,),
                )

                old_snapshot_ids = [row[0] for row in cursor.fetchall()]

                if not old_snapshot_ids:
                    return 0

                # Delete enrollment data for old snapshots
                placeholders = ",".join(["?"] * len(old_snapshot_ids))
                cursor.execute(
                    f"""
                    DELETE FROM enrollment_data
                    WHERE snapshot_id IN ({placeholders})
                """,
                    old_snapshot_ids,
                )

                # Delete old snapshots
                cursor.execute(
                    f"""
                    DELETE FROM snapshots
                    WHERE snapshot_id IN ({placeholders})
                """,
                    old_snapshot_ids,
                )

                deleted_count = len(old_snapshot_ids)
                conn.commit()

                self.logger.info(f"Cleaned up {deleted_count} old snapshots")
                return deleted_count

        except sqlite3.Error as e:
            self.logger.error(f"Database error during cleanup: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error during cleanup: {e}")
            raise

    def get_latest_snapshot_id(self) -> Optional[int]:
        """Finds the ID of the most recent snapshot."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                result = cursor.execute(
                    "SELECT snapshot_id FROM snapshots ORDER BY timestamp DESC LIMIT 1"
                ).fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting latest snapshot ID: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error getting latest snapshot ID: {e}")
            raise

    def get_last_reported_snapshot_id(self) -> Optional[int]:
        """Finds the ID of the snapshot from the last report log."""
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()
                result = cursor.execute(
                    "SELECT reported_snapshot_id FROM reporting_log ORDER BY report_timestamp DESC LIMIT 1"
                ).fetchone()
                return result[0] if result else None
        except sqlite3.Error as e:
            self.logger.error(f"Database error getting last reported snapshot ID: {e}")
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error getting last reported snapshot ID: {e}"
            )
            raise

    def add_reporting_log(self, snapshot_id: int, changes_were_found: bool) -> None:
        """Adds a new entry to the reporting log."""
        try:
            from datetime import datetime

            timestamp = datetime.now().isoformat()
            changes_found_int = 1 if changes_were_found else 0

            with self.get_connection() as conn:
                cursor = conn.cursor()
                cursor.execute(
                    "INSERT INTO reporting_log (reported_snapshot_id, report_timestamp, changes_found) VALUES (?, ?, ?)",
                    (snapshot_id, timestamp, changes_found_int),
                )
                conn.commit()
                self.logger.info(
                    f"Added reporting log entry for snapshot {snapshot_id}"
                )
        except sqlite3.Error as e:
            self.logger.error(f"Database error adding reporting log: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error adding reporting log: {e}")
            raise

    def get_snapshot_data(self, snapshot_id: int) -> Optional[EnrollmentSnapshot]:
        """
        Reconstructs an EnrollmentSnapshot object for a given snapshot ID.
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                # Get snapshot metadata
                snapshot_result = cursor.execute(
                    "SELECT timestamp, semester, overall_fill FROM snapshots WHERE snapshot_id = ?",
                    (snapshot_id,),
                ).fetchone()

                if not snapshot_result:
                    self.logger.warning(f"No snapshot found with ID {snapshot_id}")
                    return None

                timestamp, semester, overall_fill = snapshot_result

                # Create the snapshot object
                snapshot = EnrollmentSnapshot(
                    timestamp=timestamp, semester=semester, overall_fill=overall_fill
                )

                # Get all enrollment data for this snapshot with course and section info
                enrollment_query = """
                    SELECT
                        c.course_code,
                        c.course_title,
                        c.department,
                        s.section_code,
                        s.section_type,
                        s.instructor,
                        ed.status,
                        ed.enrollment_count,
                        ed.capacity_count,
                        ed.fill_percentage
                    FROM enrollment_data ed
                    JOIN sections s ON ed.section_id = s.section_id
                    JOIN courses c ON s.course_id = c.course_id
                    WHERE ed.snapshot_id = ?
                    ORDER BY c.course_code, s.section_code
                """

                enrollment_data = cursor.execute(
                    enrollment_query, (snapshot_id,)
                ).fetchall()

                # Reconstruct courses and sections
                from ..models import Course, Section

                for row in enrollment_data:
                    (
                        course_code,
                        course_title,
                        department,
                        section_code,
                        section_type,
                        instructor,
                        status,
                        enrollment,
                        capacity,
                        fill,
                    ) = row

                    # Create or get course
                    if course_code not in snapshot.courses:
                        snapshot.courses[course_code] = Course(
                            course_code=course_code,
                            department=department,
                            course_title=course_title.strip() if course_title else None,
                        )

                    course = snapshot.courses[course_code]

                    # Create section
                    section = Section(
                        section_id=section_code,
                        section_type=section_type,
                        enrollment=enrollment,
                        capacity=capacity,
                        fill=fill,
                    )

                    course.sections[section_code] = section

                # Calculate average fills for courses
                for course in snapshot.courses.values():
                    if course.sections:
                        course.average_fill = sum(
                            s.fill for s in course.sections.values()
                        ) / len(course.sections)

                self.logger.info(
                    f"Reconstructed snapshot {snapshot_id} with {len(snapshot.courses)} courses"
                )
                return snapshot

        except sqlite3.Error as e:
            self.logger.error(
                f"Database error getting snapshot data for ID {snapshot_id}: {e}"
            )
            raise
        except Exception as e:
            self.logger.error(
                f"Unexpected error getting snapshot data for ID {snapshot_id}: {e}"
            )
            raise

    def get_course_history(
        self, course_code: str, semester: Optional[str] = None
    ) -> list[Dict[str, Any]]:
        """
        Get historical enrollment data for a specific course.

        Args:
            course_code: Course code to query (e.g. "CSCI 101")
            semester: Optional semester filter

        Returns:
            list[Dict]: List of dictionaries containing timestamp and section info
        """
        try:
            with self.get_connection() as conn:
                cursor = conn.cursor()

                query = """
                    SELECT 
                        s.timestamp,
                        sec.section_code,
                        e.fill_percentage,
                        e.enrollment_count,
                        e.capacity_count
                    FROM courses c
                    JOIN sections sec ON c.course_id = sec.course_id
                    JOIN enrollment_data e ON sec.section_id = e.section_id
                    JOIN snapshots s ON e.snapshot_id = s.snapshot_id
                    WHERE c.course_code = ?
                """
                params = [course_code]

                if semester:
                    query += " AND s.semester = ?"
                    params.append(semester)

                query += " ORDER BY s.timestamp ASC"

                cursor.execute(query, params)

                results = []
                for row in cursor.fetchall():
                    results.append(
                        {
                            "timestamp": row["timestamp"],
                            "section_code": row["section_code"],
                            "fill_percentage": row["fill_percentage"],
                            "enrollment_count": row["enrollment_count"],
                            "capacity_count": row["capacity_count"],
                        }
                    )

                return results

        except sqlite3.Error as e:
            self.logger.error(f"Database error getting course history: {e}")
            raise
        except Exception as e:
            self.logger.error(f"Unexpected error getting course history: {e}")
            raise


# Example usage and testing functions
if __name__ == "__main__":
    # Set up logging for testing
    logging.basicConfig(level=logging.INFO)

    # Create test database manager
    db_manager = DatabaseManager(":memory:")  # Use in-memory database for testing

    # Test basic operations
    try:
        # Test course insertion
        course_id = db_manager.insert_course(
            "CSCI 101", "Intro to Computer Science", "CSCI"
        )
        print(f"Inserted course with ID: {course_id}")

        # Test section insertion
        section_id = db_manager.insert_section(course_id, "1L", "L", "Dr. Smith")
        print(f"Inserted section with ID: {section_id}")

        # Test snapshot insertion
        snapshot_id = db_manager.insert_snapshot(
            "2024-01-15 10:00:00", "Spring 2024", 0.85
        )
        print(f"Inserted snapshot with ID: {snapshot_id}")

        # Test enrollment data insertion
        enrollment_id = db_manager.insert_enrollment_data(
            snapshot_id, section_id, 25, 30
        )
        print(f"Inserted enrollment data with ID: {enrollment_id}")

        # Test summary
        summary = db_manager.get_enrollment_summary(snapshot_id)
        print(f"Enrollment summary: {summary}")

        print("All tests passed!")

    except Exception as e:
        print(f"Test failed: {e}")
