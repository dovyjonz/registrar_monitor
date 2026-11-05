"""Tests for the snapshot processor module."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.data.snapshot_processor import SnapshotProcessor


@pytest.fixture
def processor(tmp_path: Path) -> SnapshotProcessor:
    """Create a SnapshotProcessor with a temporary data directory."""
    return SnapshotProcessor(data_dir=str(tmp_path))


class TestProcessData:
    """Tests for process_data method."""

    def test_basic_processing(self, processor: SnapshotProcessor):
        """Should process list of dicts into EnrollmentSnapshot."""
        data = [
            {
                "Course Abbr": "CS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "Smith",
                "Level": "UG",
                "Course Title": "Intro CS",
            },
            {
                "Course Abbr": "CS 101",
                "S/T": "11L",
                "Enr": 28,
                "Cap": 30,
                "Fill": 0.93,
                "Instructor": "Jones",
                "Level": "UG",
                "Course Title": "Intro CS",
            },
        ]

        snapshot = processor.process_data(data, "Spring 2024", "2024-01-15 10:30:00")

        assert snapshot.semester == "Spring 2024"
        assert snapshot.timestamp == "2024-01-15 10:30:00"
        assert "CS 101" in snapshot.courses
        assert len(snapshot.courses["CS 101"].sections) == 2

    def test_calculates_average_fill(self, processor: SnapshotProcessor):
        """Should calculate average fill for courses."""
        data = [
            {
                "Course Abbr": "CS 101",
                "S/T": "10L",
                "Enr": 15,
                "Cap": 30,
                "Fill": 0.50,
                "Instructor": "Smith",
                "Level": "UG",
                "Course Title": "Intro CS",
            },
            {
                "Course Abbr": "CS 101",
                "S/T": "11L",
                "Enr": 30,
                "Cap": 30,
                "Fill": 1.0,
                "Instructor": "Jones",
                "Level": "UG",
                "Course Title": "Intro CS",
            },
        ]

        snapshot = processor.process_data(data, "Spring 2024", "2024-01-15 10:30:00")

        course = snapshot.courses["CS 101"]
        # Average of 0.50 and 1.0 = 0.75
        assert 0.74 <= course.average_fill <= 0.76

    def test_filters_non_ug_levels(self, processor: SnapshotProcessor):
        """Should filter out non-UG level courses."""
        data = [
            {
                "Course Abbr": "CS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "Smith",
                "Level": "UG",
                "Course Title": "Intro CS",
            },
            {
                "Course Abbr": "CS 501",
                "S/T": "10L",
                "Enr": 10,
                "Cap": 15,
                "Fill": 0.67,
                "Instructor": "Brown",
                "Level": "GR",  # Graduate should be filtered
                "Course Title": "Advanced CS",
            },
        ]

        snapshot = processor.process_data(data, "Spring 2024", "2024-01-15 10:30:00")

        assert len(snapshot.courses) == 1
        assert "CS 101" in snapshot.courses
        assert "CS 501" not in snapshot.courses

    def test_aggregates_multiple_instructor_rows_for_same_section(
        self, processor: SnapshotProcessor
    ):
        """Multiple raw instructor rows for one section should produce one stable string."""
        data = [
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "Smith",
                "Level": "UG",
                "Course Title": "Business",
            },
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "Jones",
                "Level": "UG",
                "Course Title": "Business",
            },
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "TBA",
                "Level": "UG",
                "Course Title": "Business",
            },
        ]

        snapshot = processor.process_data(data, "Spring 2024", "2024-01-15 10:30:00")

        course = snapshot.courses["BUS 101"]
        assert len(course.sections) == 1
        assert course.sections["10L"].instructor == "Smith, Jones"

    def test_tba_and_empty_instructors_normalize_to_empty(
        self, processor: SnapshotProcessor
    ):
        data = [
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "TBA TBA",
                "Level": "UG",
                "Course Title": "Business",
            },
            {
                "Course Abbr": "BUS 101",
                "S/T": "10L",
                "Enr": 25,
                "Cap": 30,
                "Fill": 0.83,
                "Instructor": "",
                "Level": "UG",
                "Course Title": "Business",
            },
        ]

        snapshot = processor.process_data(data, "Spring 2024", "2024-01-15 10:30:00")

        assert snapshot.courses["BUS 101"].sections["10L"].instructor == ""


@pytest.mark.parametrize(
    ("created", "expected"),
    [(True, "Stored snapshot"), (False, "Snapshot unchanged")],
)
def test_save_snapshot_reports_whether_anything_was_stored(
    tmp_path, capsys, created, expected
):
    from unittest.mock import MagicMock

    from registrarmonitor.models import EnrollmentSnapshot

    db = MagicMock()
    db.store_enrollment_snapshot.return_value.created = created
    snapshot = EnrollmentSnapshot(timestamp="t", semester="Fall 2026", overall_fill=0)

    result = SnapshotProcessor(data_dir=str(tmp_path)).save_snapshot(snapshot, db)

    assert result is db.store_enrollment_snapshot.return_value
    assert expected in capsys.readouterr().out
