"""Tests for the database manager module (integration tests with temp SQLite)."""

import sqlite3
from pathlib import Path
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.integration

from registrarmonitor.data.database_manager import DatabaseManager
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
                    INSERT INTO reporting_log_v2 (
                        reported_snapshot_id,
                        report_timestamp,
                        changes_found
                    ) VALUES (?, ?, ?)
                    """,
                    (999_999, "2024-01-15 10:30:00", 1),
                )

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

        assert {
            "state_snapshot",
            "course_catalog",
            "section_catalog",
            "reporting_log_v2",
        } <= tables
        assert tables.isdisjoint({"snapshots", "courses", "sections"})

    def test_rejects_unsupported_schema_version(self, tmp_path: Path):
        db_path = tmp_path / "old.db"
        with sqlite3.connect(db_path) as conn:
            conn.execute("PRAGMA user_version = 1")

        with pytest.raises(sqlite3.DatabaseError, match="unsupported"):
            DatabaseManager(db_path=str(db_path))


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
            cursor.execute("SELECT COUNT(*) FROM state_snapshot")
            assert cursor.fetchone()[0] == 1

            cursor.execute("SELECT COUNT(*) FROM course_catalog")
            assert cursor.fetchone()[0] == 1

            cursor.execute("SELECT COUNT(*) FROM section_catalog")
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
            cursor.execute("SELECT COUNT(*) FROM state_snapshot")
            assert cursor.fetchone()[0] == 1

            cursor.execute(
                "SELECT observed_at, last_seen_at FROM state_snapshot LIMIT 1"
            )
            row = cursor.fetchone()
            assert row["observed_at"] == "2024-01-15 10:00:00"
            assert row["last_seen_at"] == "2024-01-15 10:15:00"

        with patch(
            "registrarmonitor.website.checksums.DatabaseManager",
            return_value=db_manager,
        ):
            assert compute_semester_hash("Spring 2024") == checksum_before
