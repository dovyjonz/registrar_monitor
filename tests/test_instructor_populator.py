"""Tests for the instructor populator module."""

from pathlib import Path
from unittest.mock import patch

import pytest

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.instructor_populator import populate_instructors
from registrarmonitor.models import Course, EnrollmentSnapshot, Section


@pytest.fixture
def db_manager(tmp_path: Path) -> DatabaseManager:
    db_path = str(tmp_path / "test_enrollment.db")
    db = DatabaseManager(db_path=db_path, semester="Spring 2024")
    # Seed with a snapshot that has no instructors
    sections = {
        "10L": Section("10L", "L", 25, 30, 0.83, instructor=""),
        "11L": Section("11L", "L", 28, 30, 0.93, instructor=""),
    }
    course = Course(
        course_code="CS 101",
        department="CS",
        sections=sections,
        average_fill=0.88,
        course_title="Intro CS",
    )
    snapshot = EnrollmentSnapshot(
        timestamp="2024-01-15 10:00:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={"CS 101": course},
    )
    db.store_enrollment_snapshot(snapshot)
    return db


@pytest.fixture
def db_path(db_manager: DatabaseManager) -> str:
    return str(db_manager.db_path)


@pytest.fixture
def excel_path(tmp_path: Path) -> str:
    return str(tmp_path / "instructors.xls")


@pytest.fixture
def mock_excel_data():
    """Return data matching what ExcelReader.read_excel_data would return."""
    return (
        "Spring 2024",
        "2024-01-15 10:00:00",
        [
            {
                "Course Abbr": "CS 101",
                "S/T": "10L",
                "Instructor": "Smith",
                "Enr": 25,
                "Cap": 30,
            },
            {
                "Course Abbr": "CS 101",
                "S/T": "11L",
                "Instructor": "Jones",
                "Enr": 28,
                "Cap": 30,
            },
        ],
    )


class TestPopulateInstructors:
    """Tests for populate_instructors."""

    def test_missing_excel_file_returns_false(self, db_path: str):
        result = populate_instructors(db_path, "nonexistent.xls")
        assert result is False

    def test_missing_db_file_returns_false(self, excel_path: str):
        result = populate_instructors("nonexistent.db", excel_path)
        assert result is False

    def test_empty_data_returns_true(self, db_path: str, tmp_path: Path):
        empty_xls = str(tmp_path / "empty.xls")
        Path(empty_xls).write_bytes(b"")
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = ("Spring 2024", "2024-01-15 10:00:00", [])
            result = populate_instructors(db_path, empty_xls)
        assert result is True

    def test_successful_update(self, db_path: str, excel_path: str, mock_excel_data):
        Path(excel_path).write_bytes(b"")
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = mock_excel_data
            result = populate_instructors(db_path, excel_path)

        assert result is True

        # Verify instructors were updated in the database
        db = DatabaseManager(db_path=db_path)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.section_code, s.instructor
                FROM sections s
                JOIN courses c ON s.course_id = c.course_id
                WHERE c.course_code = 'CS 101'
                ORDER BY s.section_code
                """
            )
            rows = cursor.fetchall()

        instructors = {row["section_code"]: row["instructor"] for row in rows}
        assert instructors.get("10L") == "Smith"
        assert instructors.get("11L") == "Jones"

    def test_dry_run_does_not_modify_db(
        self, db_path: str, excel_path: str, mock_excel_data
    ):
        Path(excel_path).write_bytes(b"")
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = mock_excel_data
            result = populate_instructors(db_path, excel_path, dry_run=True)

        assert result is True

        # Verify instructors were NOT updated
        db = DatabaseManager(db_path=db_path)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                """
                SELECT s.instructor
                FROM sections s
                JOIN courses c ON s.course_id = c.course_id
                WHERE c.course_code = 'CS 101' AND s.section_code = '10L'
                """
            )
            row = cursor.fetchone()

        assert row["instructor"] == ""  # Unchanged

    def test_creates_instructor_change_records(
        self, db_path: str, excel_path: str, mock_excel_data
    ):
        Path(excel_path).write_bytes(b"")
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = mock_excel_data
            populate_instructors(db_path, excel_path)

        db = DatabaseManager(db_path=db_path)
        with db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM instructor_changes")
            changes = cursor.fetchall()

        assert len(changes) == 2
        assert changes[0]["new_instructor"] == "Smith"
        assert changes[1]["new_instructor"] == "Jones"

    def test_no_updates_when_instructors_match(
        self, db_path: str, excel_path: str, mock_excel_data
    ):
        # First update
        Path(excel_path).write_bytes(b"")
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = mock_excel_data
            populate_instructors(db_path, excel_path)

        # Second update with same data
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = mock_excel_data
            result = populate_instructors(db_path, excel_path)

        assert result is True

    def test_handles_missing_columns_gracefully(self, db_path: str, excel_path: str):
        Path(excel_path).write_bytes(b"")
        bad_data = (
            "Spring 2024",
            "2024-01-15 10:00:00",
            [{"Subject": "CS", "Cat#": "101", "S/T": "10L", "Instructor": "Smith"}],
        )
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = bad_data
            result = populate_instructors(db_path, excel_path)

        assert result is False  # Missing "Course Abbr" column

    def test_stale_instructor_gets_cleared(
        self, db_manager: DatabaseManager, tmp_path: Path
    ):
        db_path = str(db_manager.db_path)
        excel_p = str(tmp_path / "stale.xls")
        Path(excel_p).write_bytes(b"")

        # Seed a section with an instructor but no matching excel row
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_id FROM sections WHERE section_code = '10L'")
            sid = cursor.fetchone()[0]
            cursor.execute(
                "UPDATE sections SET instructor = 'OldInstructor' WHERE section_id = ?",
                (sid,),
            )
            conn.commit()

        # Excel data only references a different section
        data = (
            "Spring 2024",
            "2024-01-15 10:00:00",
            [{"Course Abbr": "CS 999", "S/T": "99L", "Instructor": "NewGuy"}],
        )
        with patch(
            "registrarmonitor.data.instructor_populator.ExcelReader.read_excel_data"
        ) as mock_read:
            mock_read.return_value = data
            populate_instructors(db_path, excel_p)

        # The stale instructor on 10L should be cleared
        with db_manager.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT instructor FROM sections WHERE section_code = '10L'")
            row = cursor.fetchone()

        assert row["instructor"] == ""
