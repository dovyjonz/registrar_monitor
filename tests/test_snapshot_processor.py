"""Tests for the snapshot processor module."""

from pathlib import Path
from unittest.mock import patch

import pandas as pd
import pytest

from registrarmonitor.data.snapshot_processor import SnapshotProcessor


@pytest.fixture
def processor(tmp_path: Path) -> SnapshotProcessor:
    """Create a SnapshotProcessor with a temporary data directory."""
    with patch("registrarmonitor.data.snapshot_processor.get_config") as mock_config:
        mock_config.return_value = {
            "paths": {"data_dir": str(tmp_path)},
        }
        return SnapshotProcessor(data_dir=str(tmp_path))


class TestProcessData:
    """Tests for process_data method."""

    def test_basic_processing(self, processor: SnapshotProcessor):
        """Should process DataFrame into EnrollmentSnapshot."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101", "CS 101"],
                "S/T": ["10L", "11L"],
                "Enr": [25, 28],
                "Cap": [30, 30],
                "Fill": [0.83, 0.93],
                "Instructor": ["Smith", "Jones"],
                "Level": ["UG", "UG"],
                "Course Title": ["Intro CS", "Intro CS"],
            }
        )

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        assert snapshot.semester == "Spring 2024"
        assert snapshot.timestamp == "2024-01-15 10:30:00"
        assert "CS 101" in snapshot.courses
        assert len(snapshot.courses["CS 101"].sections) == 2

    def test_multiple_courses(self, processor: SnapshotProcessor):
        """Should process multiple courses correctly."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101", "MATH 201", "MATH 201"],
                "S/T": ["10L", "20L", "21L"],
                "Enr": [25, 30, 28],
                "Cap": [30, 30, 30],
                "Fill": [0.83, 1.0, 0.93],
                "Instructor": ["Smith", "Brown", "Brown"],
                "Level": ["UG", "UG", "UG"],
                "Course Title": ["Intro CS", "Calculus", "Calculus"],
            }
        )

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        assert len(snapshot.courses) == 2
        assert "CS 101" in snapshot.courses
        assert "MATH 201" in snapshot.courses

    def test_empty_dataframe(self, processor: SnapshotProcessor):
        """Should handle empty DataFrame gracefully."""
        df = pd.DataFrame(columns=["Course Abbr", "S/T", "Enr", "Cap", "Fill", "Level"])

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        assert snapshot.semester == "Spring 2024"
        assert len(snapshot.courses) == 0

    def test_calculates_average_fill(self, processor: SnapshotProcessor):
        """Should calculate average fill for courses."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101", "CS 101"],
                "S/T": ["10L", "11L"],
                "Enr": [15, 30],
                "Cap": [30, 30],
                "Fill": [0.50, 1.0],
                "Instructor": ["Smith", "Jones"],
                "Level": ["UG", "UG"],
                "Course Title": ["Intro CS", "Intro CS"],
            }
        )

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        course = snapshot.courses["CS 101"]
        # Average of 0.50 and 1.0 = 0.75
        assert 0.74 <= course.average_fill <= 0.76

    def test_section_type_extraction(self, processor: SnapshotProcessor):
        """Should extract section types correctly."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101", "CS 101"],
                "S/T": ["10L", "1R"],
                "Enr": [25, 20],
                "Cap": [30, 25],
                "Fill": [0.83, 0.80],
                "Instructor": ["Smith", "Jones"],
                "Level": ["UG", "UG"],
                "Course Title": ["Intro CS", "Intro CS"],
            }
        )

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        course = snapshot.courses["CS 101"]
        assert course.sections["10L"].section_type == "L"
        assert course.sections["1R"].section_type == "R"

    def test_filters_non_ug_levels(self, processor: SnapshotProcessor):
        """Should filter out non-UG level courses."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101", "CS 501"],
                "S/T": ["10L", "10L"],
                "Enr": [25, 10],
                "Cap": [30, 15],
                "Fill": [0.83, 0.67],
                "Instructor": ["Smith", "Brown"],
                "Level": ["UG", "GR"],  # Graduate should be filtered
                "Course Title": ["Intro CS", "Advanced CS"],
            }
        )

        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        assert len(snapshot.courses) == 1
        assert "CS 101" in snapshot.courses
        assert "CS 501" not in snapshot.courses


class TestSaveAndLoadSnapshot:
    """Tests for save_snapshot and load_latest_snapshot methods."""

    def test_save_snapshot(self, processor: SnapshotProcessor, tmp_path: Path):
        """Should save snapshot to database."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101"],
                "S/T": ["10L"],
                "Enr": [25],
                "Cap": [30],
                "Fill": [0.83],
                "Instructor": ["Smith"],
                "Level": ["UG"],
                "Course Title": ["Intro CS"],
            }
        )
        snapshot = processor.process_data(df, "Spring 2024", "2024-01-15 10:30:00")

        # Should not raise
        processor.save_snapshot(snapshot)

    def test_load_latest_snapshot_empty(self, processor: SnapshotProcessor):
        """Should return None when no snapshots exist."""
        result = processor.load_latest_snapshot("Spring 2024")
        assert result is None

    def test_save_and_load_roundtrip(
        self, processor: SnapshotProcessor, tmp_path: Path
    ):
        """Saved snapshot should be loadable."""
        df = pd.DataFrame(
            {
                "Course Abbr": ["CS 101"],
                "S/T": ["10L"],
                "Enr": [25],
                "Cap": [30],
                "Fill": [0.83],
                "Instructor": ["Smith"],
                "Level": ["UG"],
                "Course Title": ["Intro CS"],
            }
        )
        original = processor.process_data(df, "Spring 2024", "2024-01-15 11:30:00")
        processor.save_snapshot(original)

        loaded = processor.load_latest_snapshot("Spring 2024")

        assert loaded is not None
        assert loaded.semester == original.semester
        assert loaded.timestamp == original.timestamp
        assert "CS 101" in loaded.courses


class TestGetLatestSnapshot:
    """Tests for get_latest_snapshot method."""

    def test_no_snapshots(self, processor: SnapshotProcessor):
        """Should return None when no snapshots exist."""
        result = processor.get_latest_snapshot()
        assert result is None

    def test_returns_most_recent(self, processor: SnapshotProcessor, tmp_path: Path):
        """Should return the most recent snapshot."""
        df1 = pd.DataFrame(
            {
                "Course Abbr": ["CS 101"],
                "S/T": ["10L"],
                "Enr": [20],
                "Cap": [30],
                "Fill": [0.67],
                "Instructor": ["Smith"],
                "Level": ["UG"],
                "Course Title": ["Intro CS"],
            }
        )
        df2 = pd.DataFrame(
            {
                "Course Abbr": ["CS 101"],
                "S/T": ["10L"],
                "Enr": [25],
                "Cap": [30],
                "Fill": [0.83],
                "Instructor": ["Smith"],
                "Level": ["UG"],
                "Course Title": ["Intro CS"],
            }
        )

        snapshot1 = processor.process_data(df1, "Spring 2024", "2024-01-15 09:00:00")
        snapshot2 = processor.process_data(df2, "Spring 2024", "2024-01-15 10:00:00")

        processor.save_snapshot(snapshot1)
        processor.save_snapshot(snapshot2)

        latest = processor.get_latest_snapshot()

        assert latest is not None
        # Should be the later one
        assert latest.timestamp == "2024-01-15 10:00:00"
