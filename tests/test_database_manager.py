"""Tests for the database manager module (integration tests with temp SQLite)."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.instructor_populator import populate_instructors
from registrarmonitor.models import Course, EnrollmentSnapshot, Section


@pytest.fixture
def db_manager(tmp_path: Path) -> DatabaseManager:
    """Create a DatabaseManager with a temporary database."""
    db_path = str(tmp_path / "test_enrollment.db")
    return DatabaseManager(db_path=db_path, semester="Test 2024")


class TestDatabaseManagerInit:
    """Tests for DatabaseManager initialization."""

    def test_connections_enforce_foreign_keys(self, db_manager: DatabaseManager):
        """Every managed connection should reject orphaned child rows."""
        with db_manager.get_connection() as conn:
            foreign_keys_enabled = conn.execute("PRAGMA foreign_keys").fetchone()[0]

            assert foreign_keys_enabled == 1
            with pytest.raises(sqlite3.IntegrityError):
                conn.execute(
                    """
                    INSERT INTO reporting_log (
                        reported_snapshot_id,
                        report_timestamp,
                        changes_found
                    ) VALUES (?, ?, ?)
                    """,
                    (999_999, "2024-01-15 10:30:00", 1),
                )

    def test_creates_database_file(self, tmp_path: Path):
        """Database file should be created on initialization."""
        db_path = str(tmp_path / "new_db.db")
        _ = DatabaseManager(db_path=db_path)

        assert Path(db_path).exists()

    def test_read_only_manager_cannot_mutate_enrollment_database(self, tmp_path: Path):
        db_path = tmp_path / "read-only.db"
        DatabaseManager(db_path=str(db_path))

        reader = DatabaseManager(db_path=str(db_path), read_only=True)

        with reader.get_connection() as connection:
            assert connection.execute("PRAGMA query_only").fetchone()[0] == 1
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute("PRAGMA user_version = 2")

    def test_creates_tables(self, db_manager: DatabaseManager):
        """Required tables should be created."""
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table'")
            tables = {row[0] for row in cursor.fetchall()}

        assert "snapshots" in tables
        assert "courses" in tables
        assert "sections" in tables
        assert "enrollment_data" in tables

    def test_migration_adds_instructor_column(self, tmp_path: Path):
        """Database manager should migrate existing sections table to include instructor column."""
        db_path = str(tmp_path / "old_db.db")

        # 1. Create a database with the old schema (without instructor column in sections)
        conn = sqlite3.connect(db_path)
        cursor = conn.cursor()
        cursor.execute(
            "CREATE TABLE courses (course_id INTEGER PRIMARY KEY, course_code TEXT UNIQUE)"
        )
        cursor.execute("""
            CREATE TABLE sections (
                section_id INTEGER PRIMARY KEY AUTOINCREMENT,
                course_id INTEGER NOT NULL,
                section_code TEXT NOT NULL,
                section_type TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                FOREIGN KEY (course_id) REFERENCES courses (course_id),
                UNIQUE(course_id, section_code)
            )
        """)
        conn.commit()
        conn.close()

        # 2. Initialize DatabaseManager pointing to the old DB
        manager = DatabaseManager(db_path=db_path)

        # 3. Verify that the table was migrated and 'instructor' column was added
        with manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("PRAGMA table_info(sections)")
            columns = {row[1] for row in cursor.fetchall()}

        assert "instructor" in columns

    def test_migration_adds_and_backfills_last_seen_at(self, tmp_path: Path):
        """Existing snapshot event times should initialize freshness on migration."""
        import sqlite3

        db_path = str(tmp_path / "old_snapshots.db")
        with sqlite3.connect(db_path) as conn:
            conn.execute("""
                CREATE TABLE snapshots (
                    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL UNIQUE,
                    semester TEXT NOT NULL,
                    overall_fill REAL NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)
            conn.execute(
                "INSERT INTO snapshots (timestamp, semester, overall_fill) "
                "VALUES (?, ?, ?)",
                ("2024-01-15 10:00:00", "Spring 2024", 0.75),
            )

        manager = DatabaseManager(db_path=db_path)

        with manager.get_connection() as conn:
            row = conn.execute(
                "SELECT timestamp, last_seen_at FROM snapshots"
            ).fetchone()

        assert row["timestamp"] == "2024-01-15 10:00:00"
        assert row["last_seen_at"] == row["timestamp"]


class TestInsertCourse:
    """Tests for insert_course method."""

    def test_insert_new_course(self, db_manager: DatabaseManager):
        """Inserting a new course should return a valid ID."""
        course_id = db_manager.insert_course(
            course_code="CS 101",
            course_title="Intro to CS",
            department="CS",
        )

        assert course_id > 0

    def test_insert_same_course_returns_same_id(self, db_manager: DatabaseManager):
        """Inserting the same course should return the same ID."""
        id1 = db_manager.insert_course("CS 101", "Intro to CS", "CS")
        id2 = db_manager.insert_course("CS 101", "Intro to CS", "CS")

        assert id1 == id2

    def test_insert_different_courses(self, db_manager: DatabaseManager):
        """Different courses should get different IDs."""
        id1 = db_manager.insert_course("CS 101")
        id2 = db_manager.insert_course("MATH 201")

        assert id1 != id2


class TestInsertSection:
    """Tests for insert_section method."""

    def test_insert_new_section(self, db_manager: DatabaseManager):
        """Inserting a new section should return a valid ID."""
        course_id = db_manager.insert_course("CS 101")
        section_id = db_manager.insert_section(
            course_id=course_id,
            section_code="10L",
            section_type="L",
            instructor="Dr. Smith",
        )

        assert section_id > 0

    def test_insert_same_section_returns_same_id(self, db_manager: DatabaseManager):
        """Inserting the same section should return the same ID."""
        course_id = db_manager.insert_course("CS 101")
        id1 = db_manager.insert_section(course_id, "10L", "L")
        id2 = db_manager.insert_section(course_id, "10L", "L")

        assert id1 == id2

    def test_same_section_different_courses(self, db_manager: DatabaseManager):
        """Same section code for different courses should get different IDs."""
        course1_id = db_manager.insert_course("CS 101")
        course2_id = db_manager.insert_course("CS 102")

        section1_id = db_manager.insert_section(course1_id, "10L")
        section2_id = db_manager.insert_section(course2_id, "10L")

        assert section1_id != section2_id


class TestInsertSnapshot:
    """Tests for insert_snapshot method."""

    def test_insert_snapshot(self, db_manager: DatabaseManager):
        """Inserting a snapshot should return a valid ID."""
        snapshot_id = db_manager.insert_snapshot(
            timestamp="2024-01-15 10:30:00",
            semester="Spring 2024",
            overall_fill=0.75,
        )

        assert snapshot_id > 0

    def test_snapshots_get_unique_ids(self, db_manager: DatabaseManager):
        """Different snapshots should get different IDs."""
        id1 = db_manager.insert_snapshot("2024-01-15 10:00:00", "Spring 2024", 0.70)
        id2 = db_manager.insert_snapshot("2024-01-15 11:00:00", "Spring 2024", 0.75)

        assert id1 != id2


class TestInsertEnrollmentData:
    """Tests for insert_enrollment_data method."""

    def test_insert_enrollment_data(self, db_manager: DatabaseManager):
        """Should insert enrollment data successfully."""
        course_id = db_manager.insert_course("CS 101")
        section_id = db_manager.insert_section(course_id, "10L")
        snapshot_id = db_manager.insert_snapshot(
            "2024-01-15 10:30:00", "Spring 2024", 0.75
        )

        enrollment_id = db_manager.insert_enrollment_data(
            snapshot_id=snapshot_id,
            section_id=section_id,
            enrollment_count=25,
            capacity_count=30,
        )

        assert enrollment_id > 0


class TestStoreEnrollmentSnapshot:
    """Tests for store_enrollment_snapshot method."""

    def test_store_complete_snapshot(self, db_manager: DatabaseManager):
        """Should store a complete snapshot with courses and sections."""
        sections = {
            "10L": Section("10L", "L", 25, 30, 0.83),
            "11L": Section("11L", "L", 28, 30, 0.93),
        }
        course = Course("CS 101", "CS", sections, 0.88, "Intro to CS")
        snapshot = EnrollmentSnapshot(
            timestamp="2024-01-15 10:30:00",
            semester="Spring 2024",
            overall_fill=0.75,
            courses={"CS 101": course},
        )

        # Should not raise
        db_manager.store_enrollment_snapshot(snapshot)

        # Verify data was stored
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            assert cursor.fetchone()[0] == 1

            cursor.execute("SELECT COUNT(*) FROM courses")
            assert cursor.fetchone()[0] == 1

            cursor.execute("SELECT COUNT(*) FROM sections")
            assert cursor.fetchone()[0] == 2

    def test_store_empty_snapshot(self, db_manager: DatabaseManager):
        """Should handle empty snapshot gracefully."""
        snapshot = EnrollmentSnapshot(
            timestamp="2024-01-15 10:30:00",
            semester="Spring 2024",
            overall_fill=0.0,
            courses={},
        )

        # Should not raise
        db_manager.store_enrollment_snapshot(snapshot)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            assert cursor.fetchone()[0] == 1

    def test_store_multiple_snapshots(self, db_manager: DatabaseManager):
        """Should store multiple snapshots correctly."""
        snapshot1 = EnrollmentSnapshot(
            timestamp="2024-01-15 10:00:00",
            semester="Spring 2024",
            overall_fill=0.70,
            courses={},
        )
        snapshot2 = EnrollmentSnapshot(
            timestamp="2024-01-15 11:00:00",
            semester="Spring 2024",
            overall_fill=0.75,
            courses={},
        )

        db_manager.store_enrollment_snapshot(snapshot1)
        db_manager.store_enrollment_snapshot(snapshot2)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            assert cursor.fetchone()[0] == 2

    def test_identical_poll_preserves_event_time_and_updates_last_seen(
        self, db_manager: DatabaseManager
    ):
        """An identical poll should refresh observation time without moving history."""
        sections = {
            "10L": Section("10L", "L", 25, 30, 0.83, "Dr. Smith"),
        }
        course = Course("CS 101", "CS", sections, 0.83, "Intro to CS")
        snapshot1 = EnrollmentSnapshot(
            timestamp="2024-01-15 10:00:00",
            semester="Spring 2024",
            overall_fill=0.83,
            courses={"CS 101": course},
        )

        # Identical snapshot with a different timestamp
        snapshot2 = EnrollmentSnapshot(
            timestamp="2024-01-15 10:15:00",
            semester="Spring 2024",
            overall_fill=0.83,
            courses={
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {
                        "10L": Section("10L", "L", 25, 30, 0.83, "Dr. Smith"),
                    },
                    0.83,
                    "Intro to CS",
                )
            },
        )

        db_manager.store_enrollment_snapshot(snapshot1)
        with patch(
            "registrarmonitor.website.checksums.DatabaseManager",
            return_value=db_manager,
        ):
            from registrarmonitor.website.checksums import compute_semester_hash

            checksum_before = compute_semester_hash("Spring 2024")

        db_manager.store_enrollment_snapshot(snapshot2)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            assert cursor.fetchone()[0] == 1

            cursor.execute("SELECT timestamp, last_seen_at FROM snapshots LIMIT 1")
            row = cursor.fetchone()
            assert row["timestamp"] == "2024-01-15 10:00:00"
            assert row["last_seen_at"] == "2024-01-15 10:15:00"

        with patch(
            "registrarmonitor.website.checksums.DatabaseManager",
            return_value=db_manager,
        ):
            assert compute_semester_hash("Spring 2024") == checksum_before

    def test_store_non_duplicate_snapshots_not_deduplicated(
        self, db_manager: DatabaseManager
    ):
        """Snapshots that differ in instructor or other details should not be deduplicated."""
        sections = {
            "10L": Section("10L", "L", 25, 30, 0.83, "Dr. Smith"),
        }
        course = Course("CS 101", "CS", sections, 0.83, "Intro to CS")
        snapshot1 = EnrollmentSnapshot(
            timestamp="2024-01-15 10:00:00",
            semester="Spring 2024",
            overall_fill=0.83,
            courses={"CS 101": course},
        )

        # Snapshot with a different instructor
        snapshot_diff_instructor = EnrollmentSnapshot(
            timestamp="2024-01-15 10:15:00",
            semester="Spring 2024",
            overall_fill=0.83,
            courses={
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {
                        "10L": Section("10L", "L", 25, 30, 0.83, "Dr. Jones"),
                    },
                    0.83,
                    "Intro to CS",
                )
            },
        )

        # Snapshot with different enrollment
        snapshot_diff_enrollment = EnrollmentSnapshot(
            timestamp="2024-01-15 10:30:00",
            semester="Spring 2024",
            overall_fill=0.83,
            courses={
                "CS 101": Course(
                    "CS 101",
                    "CS",
                    {
                        "10L": Section("10L", "L", 26, 30, 0.87, "Dr. Smith"),
                    },
                    0.87,
                    "Intro to CS",
                )
            },
        )

        db_manager.store_enrollment_snapshot(snapshot1)
        db_manager.store_enrollment_snapshot(snapshot_diff_instructor)
        db_manager.store_enrollment_snapshot(snapshot_diff_enrollment)

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            assert cursor.fetchone()[0] == 3

    def test_populate_instructors_second_run_is_idempotent(
        self, db_manager: DatabaseManager, tmp_path: Path
    ):
        """Unchanged normalized instructor data should not create repeat changes."""
        snapshot = EnrollmentSnapshot(
            timestamp="2024-01-15 10:00:00",
            semester="Test 2024",
            overall_fill=0.83,
            courses={
                "BUS 101": Course(
                    "BUS 101",
                    "BUS",
                    {"10L": Section("10L", "L", 25, 30, 0.83, "")},
                    0.83,
                    "Business",
                )
            },
        )
        db_manager.store_enrollment_snapshot(snapshot)
        excel_path = tmp_path / "source.xlsx"
        excel_path.write_text("placeholder")
        rows = [
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Instructor": "Smith",
            },
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Instructor": "Jones",
            },
        ]

        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader"
        ) as reader_cls:
            reader_cls.return_value.read_excel_data.return_value = (None, None, rows)
            assert populate_instructors(str(db_manager.db_path), str(excel_path))
            assert populate_instructors(str(db_manager.db_path), str(excel_path))

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT instructor FROM sections")
            assert cursor.fetchone()[0] == "Smith, Jones"
            cursor.execute("SELECT COUNT(*) FROM instructor_changes")
            assert cursor.fetchone()[0] == 1

    def test_dedupe_instructor_changes_removes_only_consecutive_duplicates(
        self, db_manager: DatabaseManager
    ):
        course_id = db_manager.insert_course("BUS 101", "Business", "BUS")
        section_id = db_manager.insert_section(course_id, "10L", "L")

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.executemany(
                """
                INSERT INTO instructor_changes
                (section_id, old_instructor, new_instructor, timestamp)
                VALUES (?, ?, ?, ?)
                """,
                [
                    (section_id, "A", "B", "2024-01-15T10:00:00"),
                    (section_id, "A", "B", "2024-01-15T10:01:00"),
                    (section_id, "B", "A", "2024-01-15T10:02:00"),
                    (section_id, "A", "B", "2024-01-15T10:03:00"),
                ],
            )
            conn.commit()

        assert db_manager.dedupe_instructor_changes(dry_run=True) == 1
        assert db_manager.dedupe_instructor_changes(dry_run=False) == 1

        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT old_instructor, new_instructor
                FROM instructor_changes
                ORDER BY timestamp ASC, change_id ASC
                """
            )
            assert [tuple(row) for row in cursor.fetchall()] == [
                ("A", "B"),
                ("B", "A"),
                ("A", "B"),
            ]


class TestDetermineStatus:
    """Tests for _determine_status method."""

    @pytest.mark.parametrize(
        ("fill", "expected"),
        [
            (0.0, "OPEN"),
            (0.50, "OPEN"),
            (0.74, "OPEN"),
            (0.75, "NEAR"),
            (0.90, "NEAR"),
            (0.99, "NEAR"),
            (1.0, "FULL"),
            (1.15, "FULL"),
        ],
    )
    def test_fill_threshold_boundaries(self, db_manager, fill, expected):
        """Status should match fill thresholds: <0.75 OPEN, <1.0 NEAR, >=1.0 FULL."""
        assert db_manager._determine_status(fill) == expected


class TestSanitizeSemesterName:
    """Tests for _sanitize_semester_name method."""

    def test_basic_semester(self, db_manager: DatabaseManager):
        """Basic semester name should be sanitized."""
        result = db_manager._sanitize_semester_name("Spring 2024")
        assert " " not in result
        assert result.islower() or "_" in result

    def test_special_characters(self, db_manager: DatabaseManager):
        """Special characters should be removed or replaced."""
        result = db_manager._sanitize_semester_name("Fall/Winter 2024")
        # Should not contain problematic characters
        assert "/" not in result


class TestGetSemesterDatabases:
    """Tests for get_semester_databases static method."""

    def test_finds_semester_databases(self, tmp_path: Path):
        """Should find semester database files."""
        # Create some test database files matching the expected pattern
        (tmp_path / "enrollment_spring_2024.db").touch()
        (tmp_path / "enrollment_fall_2024.db").touch()
        (tmp_path / "other_file.txt").touch()  # Should be ignored

        result = DatabaseManager.get_semester_databases(str(tmp_path))

        assert len(result) == 2
        assert any("Spring" in k for k in result.keys())
        assert any("Fall" in k for k in result.keys())

    def test_empty_directory(self, tmp_path: Path):
        """Empty directory should return empty dict."""
        result = DatabaseManager.get_semester_databases(str(tmp_path))
        assert result == {}
