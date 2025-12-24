"""Tests for the database manager module (integration tests with temp SQLite)."""

from pathlib import Path

import pytest

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section


@pytest.fixture
def db_manager(tmp_path: Path) -> DatabaseManager:
    """Create a DatabaseManager with a temporary database."""
    db_path = str(tmp_path / "test_enrollment.db")
    return DatabaseManager(db_path=db_path, semester="Test 2024")


class TestDatabaseManagerInit:
    """Tests for DatabaseManager initialization."""

    def test_creates_database_file(self, tmp_path: Path):
        """Database file should be created on initialization."""
        db_path = str(tmp_path / "new_db.db")
        _ = DatabaseManager(db_path=db_path)

        assert Path(db_path).exists()

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


class TestDetermineStatus:
    """Tests for _determine_status method."""

    def test_open_status(self, db_manager: DatabaseManager):
        """Fill below 75% should be OPEN."""
        assert db_manager._determine_status(0.50) == "OPEN"
        assert db_manager._determine_status(0.74) == "OPEN"

    def test_near_status(self, db_manager: DatabaseManager):
        """Fill between 75% and 100% should be NEAR."""
        assert db_manager._determine_status(0.75) == "NEAR"
        assert db_manager._determine_status(0.90) == "NEAR"
        assert db_manager._determine_status(0.99) == "NEAR"

    def test_full_status(self, db_manager: DatabaseManager):
        """Fill at 100% or above should be FULL."""
        assert db_manager._determine_status(1.0) == "FULL"
        assert db_manager._determine_status(1.15) == "FULL"


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
