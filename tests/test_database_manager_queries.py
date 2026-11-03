"""Tests for DatabaseManager query and analytics methods."""

from pathlib import Path

import pytest

pytestmark = pytest.mark.integration

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section

# ── fixtures ─────────────────────────────────────────────────────


@pytest.fixture
def db_manager(tmp_path: Path) -> DatabaseManager:
    db_path = str(tmp_path / "test_enrollment.db")
    return DatabaseManager(db_path=db_path, semester="Spring 2024")


@pytest.fixture
def seeded_db(db_manager: DatabaseManager) -> DatabaseManager:
    """Populate the database with two snapshots for query testing."""
    sections_1 = {
        "10L": Section("10L", "L", 25, 30, 0.83, instructor="Smith"),
        "11L": Section("11L", "L", 28, 30, 0.93, instructor="Jones"),
        "1R": Section("1R", "R", 20, 25, 0.80, instructor="Brown"),
    }
    cs_101 = Course(
        course_code="CS 101",
        department="CS",
        sections=sections_1,
        average_fill=0.85,
        course_title="Intro CS",
    )
    sections_2 = {
        "20L": Section("20L", "L", 30, 30, 1.0, instructor="Lee"),
    }
    math_201 = Course(
        course_code="MATH 201",
        department="MATH",
        sections=sections_2,
        average_fill=1.0,
        course_title="Calculus",
    )

    snapshot_1 = EnrollmentSnapshot(
        timestamp="2024-01-15 10:00:00",
        semester="Spring 2024",
        overall_fill=0.75,
        courses={"CS 101": cs_101},
    )
    snapshot_2 = EnrollmentSnapshot(
        timestamp="2024-01-15 11:00:00",
        semester="Spring 2024",
        overall_fill=0.78,
        courses={"CS 101": cs_101, "MATH 201": math_201},
    )

    db_manager.store_enrollment_snapshot(snapshot_1)
    db_manager.store_enrollment_snapshot(snapshot_2)
    return db_manager


# ── Tests ─────────────────────────────────────────────────────────


class TestGetLastReportedSnapshotId:
    """Tests for get_last_reported_snapshot_id."""

    def test_returns_none_when_no_reporting_log(self, seeded_db: DatabaseManager):
        assert seeded_db.get_last_reported_snapshot_id() is None

    def test_returns_reported_id_after_log_entry(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        assert sid is not None
        seeded_db.add_reporting_log(sid, changes_were_found=True)
        assert seeded_db.get_last_reported_snapshot_id() == sid


class TestGetSnapshotData:
    """Tests for get_snapshot_data."""

    def test_returns_none_for_missing_id(self, seeded_db: DatabaseManager):
        result = seeded_db.get_snapshot_data(9999)
        assert result is None

    def test_reconstructs_snapshot(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        assert sid is not None
        snapshot = seeded_db.get_snapshot_data(sid)

        assert snapshot is not None
        assert snapshot.timestamp == "2024-01-15 11:00:00"
        assert snapshot.semester == "Spring 2024"
        assert snapshot.overall_fill == 0.78

    def test_reconstructs_exact_second_snapshot(self, seeded_db: DatabaseManager):
        # Get the first (oldest) snapshot
        first_id = None
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT snapshot_id FROM state_snapshot ORDER BY sequence_no ASC LIMIT 1"
            )
            row = cursor.fetchone()
            first_id = row[0]

        snapshot = seeded_db.get_snapshot_data(first_id)
        assert snapshot is not None
        assert snapshot.timestamp == "2024-01-15 10:00:00"
        assert "MATH 201" not in snapshot.courses


class TestGetCourseHistory:
    """Tests for get_course_history."""

    def test_returns_history_for_course(self, seeded_db: DatabaseManager):
        history = seeded_db.get_course_history("CS 101")
        assert len(history) >= 2  # Present in both snapshots
        timestamps = {h["timestamp"] for h in history}
        assert "2024-01-15 10:00:00" in timestamps
        assert "2024-01-15 11:00:00" in timestamps

    def test_snapshots_ordered_ascending(self, seeded_db: DatabaseManager):
        history = seeded_db.get_course_history("CS 101")
        timestamps = [h["timestamp"] for h in history]
        # Filter unique timestamps
        unique_timestamps = list(dict.fromkeys(timestamps))
        assert unique_timestamps == sorted(unique_timestamps)

    def test_returns_empty_for_nonexistent_course(self, seeded_db: DatabaseManager):
        history = seeded_db.get_course_history("PHYS 999")
        assert history == []

    def test_includes_section_details(self, seeded_db: DatabaseManager):
        history = seeded_db.get_course_history("CS 101")
        entry = history[0]
        assert "section_code" in entry
        assert "fill_percentage" in entry
        assert "enrollment_count" in entry
        assert "capacity_count" in entry
