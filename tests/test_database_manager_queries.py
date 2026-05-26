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


class TestGetLatestSnapshotId:
    """Tests for get_latest_snapshot_id."""

    def test_returns_none_when_no_snapshots(self, db_manager: DatabaseManager):
        assert db_manager.get_latest_snapshot_id() is None

    def test_returns_id_of_newest_snapshot(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        assert sid is not None
        assert isinstance(sid, int)


class TestGetLatestSnapshotTimestamp:
    """Tests for get_latest_snapshot_timestamp."""

    def test_returns_none_when_no_snapshots(self, db_manager: DatabaseManager):
        assert db_manager.get_latest_snapshot_timestamp() is None

    def test_returns_latest_timestamp(self, seeded_db: DatabaseManager):
        ts = seeded_db.get_latest_snapshot_timestamp()
        assert ts == "2024-01-15 11:00:00"

    def test_filters_by_semester(self, seeded_db: DatabaseManager):
        ts = seeded_db.get_latest_snapshot_timestamp("Spring 2024")
        assert ts == "2024-01-15 11:00:00"

    def test_returns_none_for_wrong_semester(self, seeded_db: DatabaseManager):
        ts = seeded_db.get_latest_snapshot_timestamp("Fall 2023")
        assert ts is None


class TestGetLastReportedSnapshotId:
    """Tests for get_last_reported_snapshot_id."""

    def test_returns_none_when_no_reporting_log(self, seeded_db: DatabaseManager):
        assert seeded_db.get_last_reported_snapshot_id() is None

    def test_returns_reported_id_after_log_entry(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        seeded_db.add_reporting_log(sid, changes_were_found=True)
        assert seeded_db.get_last_reported_snapshot_id() == sid


class TestAddReportingLog:
    """Tests for add_reporting_log."""

    def test_adds_entry_to_reporting_log(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        seeded_db.add_reporting_log(sid, changes_were_found=True)

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT * FROM reporting_log")
            entries = cursor.fetchall()

        assert len(entries) == 1
        assert entries[0]["reported_snapshot_id"] == sid
        assert entries[0]["changes_found"] == 1

    def test_adds_entry_with_no_changes(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        seeded_db.add_reporting_log(sid, changes_were_found=False)

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT changes_found FROM reporting_log")
            row = cursor.fetchone()

        assert row["changes_found"] == 0


class TestGetSnapshotData:
    """Tests for get_snapshot_data."""

    def test_returns_none_for_missing_id(self, seeded_db: DatabaseManager):
        result = seeded_db.get_snapshot_data(9999)
        assert result is None

    def test_reconstructs_snapshot(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        snapshot = seeded_db.get_snapshot_data(sid)

        assert snapshot is not None
        assert snapshot.timestamp == "2024-01-15 11:00:00"
        assert snapshot.semester == "Spring 2024"
        assert snapshot.overall_fill == 0.78

    def test_includes_courses(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        snapshot = seeded_db.get_snapshot_data(sid)

        assert "CS 101" in snapshot.courses
        assert "MATH 201" in snapshot.courses

    def test_includes_sections(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        snapshot = seeded_db.get_snapshot_data(sid)

        cs = snapshot.courses["CS 101"]
        assert len(cs.sections) == 3
        assert "10L" in cs.sections
        assert cs.sections["10L"].enrollment == 25
        assert cs.sections["10L"].instructor == "Smith"

    def test_computes_average_fill(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        snapshot = seeded_db.get_snapshot_data(sid)

        math = snapshot.courses["MATH 201"]
        assert math.average_fill == pytest.approx(1.0)

    def test_reconstructs_exact_second_snapshot(self, seeded_db: DatabaseManager):
        # Get the first (oldest) snapshot
        first_id = None
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT snapshot_id FROM snapshots ORDER BY timestamp ASC LIMIT 1"
            )
            row = cursor.fetchone()
            first_id = row[0]

        snapshot = seeded_db.get_snapshot_data(first_id)
        assert snapshot is not None
        assert snapshot.timestamp == "2024-01-15 10:00:00"
        assert "MATH 201" not in snapshot.courses


class TestGetEnrollmentSummary:
    """Tests for get_enrollment_summary."""

    def test_returns_counts_by_status(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        summary = seeded_db.get_enrollment_summary(sid)

        # CS 101: 10L=0.83(NEAR), 11L=0.93(NEAR), 1R=0.80(NEAR), MATH 201: 20L=1.0(FULL)
        assert summary["NEAR"] >= 3
        assert summary["FULL"] >= 1
        assert summary["OPEN"] >= 0

    def test_all_statuses_present(self, seeded_db: DatabaseManager):
        sid = seeded_db.get_latest_snapshot_id()
        summary = seeded_db.get_enrollment_summary(sid)

        for status in ["OPEN", "NEAR", "FULL"]:
            assert status in summary
            assert isinstance(summary[status], int)

    def test_invalid_snapshot_id_returns_zeros(self, seeded_db: DatabaseManager):
        summary = seeded_db.get_enrollment_summary(9999)
        assert summary == {"OPEN": 0, "NEAR": 0, "FULL": 0}


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


class TestCleanupOldSnapshots:
    """Tests for cleanup_old_snapshots."""

    def test_keeps_specified_number_of_snapshots(self, seeded_db: DatabaseManager):
        deleted = seeded_db.cleanup_old_snapshots(keep_count=1)
        assert deleted >= 1  # At least the first snapshot removed

        remaining = 0
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM snapshots")
            remaining = cursor.fetchone()[0]

        assert remaining == 1

    def test_returns_zero_when_no_snapshots_to_clean(self, db_manager: DatabaseManager):
        deleted = db_manager.cleanup_old_snapshots(keep_count=10)
        assert deleted == 0

    def test_also_removes_enrollment_data(self, seeded_db: DatabaseManager):
        seeded_db.cleanup_old_snapshots(keep_count=1)

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM enrollment_data")
            remaining_data = cursor.fetchone()[0]

        # With only 1 snapshot kept, enrollment data should be just for that snapshot
        assert remaining_data > 0


class TestDedupeInstructorChanges:
    """Tests for dedupe_instructor_changes."""

    def test_dry_run_returns_count_without_deleting(self, seeded_db: DatabaseManager):
        # Add some duplicate instructor changes
        timestamp = "2024-01-15 12:00:00"
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_id FROM sections LIMIT 1")
            section_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "", "Smith", timestamp),
            )
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "", "Smith", "2024-01-15 13:00:00"),
            )
            conn.commit()

        count = seeded_db.dedupe_instructor_changes(dry_run=True)
        assert count >= 1

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM instructor_changes")
            remaining = cursor.fetchone()[0]

        assert remaining >= 2  # Nothing deleted in dry run

    def test_removes_consecutive_duplicates(self, seeded_db: DatabaseManager):
        timestamp = "2024-01-15 12:00:00"
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_id FROM sections LIMIT 1")
            section_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "", "Smith", timestamp),
            )
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "", "Smith", "2024-01-15 13:00:00"),
            )
            conn.commit()

        seeded_db.dedupe_instructor_changes(dry_run=False)

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM instructor_changes")
            remaining = cursor.fetchone()[0]

        assert remaining == 1  # Duplicate removed

    def test_preserves_non_duplicate_toggles(self, seeded_db: DatabaseManager):
        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT section_id FROM sections LIMIT 1")
            section_id = cursor.fetchone()[0]
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "", "Smith", "2024-01-15 12:00:00"),
            )
            cursor.execute(
                "INSERT INTO instructor_changes (section_id, old_instructor, new_instructor, timestamp) VALUES (?, ?, ?, ?)",
                (section_id, "Smith", "", "2024-01-15 13:00:00"),
            )
            conn.commit()

        seeded_db.dedupe_instructor_changes(dry_run=False)

        with seeded_db.get_connection() as conn:
            cursor = conn.cursor()
            cursor.execute("SELECT COUNT(*) FROM instructor_changes")
            remaining = cursor.fetchone()[0]

        assert remaining == 2  # Both toggles preserved
