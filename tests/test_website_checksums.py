"""Tests for the checksum-based incremental update detection."""

import pytest

pytestmark = pytest.mark.unit


from unittest.mock import patch

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.checksums import (
    compute_semester_hash,
    get_semesters_needing_update,
    load_checksums,
    update_checksum,
)


class TestComputeSemesterHash:
    def test_different_semesters_different_hashes(self):
        with patch("registrarmonitor.website.checksums.DatabaseManager") as mock_db_cls:

            def make_db():
                db = mock_db_cls.return_value
                conn = db.get_connection.return_value.__enter__.return_value
                return conn

            conn1 = make_db()
            conn1.execute.return_value.fetchone.return_value = (5, "2024-01-15")
            h1 = compute_semester_hash("Spring 2024")

            conn2 = make_db()
            conn2.execute.return_value.fetchone.return_value = (10, "2024-09-01")
            mock_db_cls.return_value = make_db()
            h2 = compute_semester_hash("Fall 2024")

        assert h1 != h2


def _snapshot(timestamp: str, enrollment: int = 10) -> EnrollmentSnapshot:
    section = Section("1L", "Lecture", enrollment, 20, enrollment / 20, "Prof")
    course = Course("CS 101", "CS", {"1L": section}, enrollment / 20, "Intro")
    return EnrollmentSnapshot(
        timestamp, "Spring 2024", enrollment / 20, {"CS 101": course}
    )


class TestSemesterHashInputs:
    MILESTONES = [{"time": "2024-01-15T10:00:00+05:00", "label": "Y3", "color": "#fff"}]

    @pytest.fixture
    def database(self, tmp_path):
        database = DatabaseManager(
            db_path=str(tmp_path / "enrollment.db"), semester="Spring 2024"
        )
        database.store_enrollment_snapshot(_snapshot("2024-01-15 10:00:00"))
        return database

    def hash(self, database, **kwargs):
        kwargs.setdefault("milestones", self.MILESTONES)
        return compute_semester_hash("Spring 2024", database=database, **kwargs)

    def test_new_snapshot_changes_hash(self, database):
        before = self.hash(database)
        database.store_enrollment_snapshot(_snapshot("2024-01-15 10:30:00", 12))
        assert self.hash(database) != before

    def test_milestone_edit_changes_hash(self, database):
        edited = [{**self.MILESTONES[0], "time": "2024-01-16T10:00:00+05:00"}]
        assert self.hash(database) != self.hash(database, milestones=edited)

    def test_archiving_changes_hash(self, database):
        assert self.hash(database) != self.hash(database, archived=True)

    def test_unchanged_inputs_keep_hash(self, database):
        assert self.hash(database) == self.hash(database)


class TestUpdateChecksum:
    def test_records_the_given_checksum_per_semester(self, tmp_path):
        checksums_file = tmp_path / ".checksums.json"
        update_checksum("Spring 2024", "newhash123", checksums_file)
        update_checksum("Fall 2024", "otherhash", checksums_file)

        assert load_checksums(checksums_file) == {
            "Spring 2024": "newhash123",
            "Fall 2024": "otherhash",
        }


class TestGetSemestersNeedingUpdate:
    def test_force_returns_all(self):
        with patch("registrarmonitor.website.checksums.compute_semester_hash"):
            result = get_semesters_needing_update(
                force=True, semesters=["Spring 2024", "Fall 2024"]
            )

        assert result == ["Spring 2024", "Fall 2024"]

    def test_returns_changed_semesters(self):
        with (
            patch(
                "registrarmonitor.website.checksums.compute_semester_hash",
                side_effect=lambda s, **_: (
                    "newhash" if s == "Spring 2024" else "samehash"
                ),
            ),
            patch(
                "registrarmonitor.website.checksums.load_checksums",
                return_value={"Spring 2024": "oldhash", "Fall 2024": "samehash"},
            ),
        ):
            result = get_semesters_needing_update(
                force=False, semesters=["Spring 2024", "Fall 2024"]
            )

        assert result == ["Spring 2024"]

    def test_only_the_newest_semester_is_hashed_as_current(self):
        seen: dict[str, bool] = {}

        def record(semester, *, archived=False, **_):
            seen[semester] = archived
            return "hash"

        with (
            patch(
                "registrarmonitor.website.checksums.compute_semester_hash",
                side_effect=record,
            ),
            patch("registrarmonitor.website.checksums.load_checksums", return_value={}),
        ):
            get_semesters_needing_update(semesters=["Fall 2024", "Spring 2024"])

        assert seen == {"Fall 2024": False, "Spring 2024": True}

    def test_returns_empty_when_all_current(self):
        with (
            patch(
                "registrarmonitor.website.checksums.compute_semester_hash",
                return_value="samehash",
            ),
            patch(
                "registrarmonitor.website.checksums.load_checksums",
                return_value={"Spring 2024": "samehash"},
            ),
        ):
            result = get_semesters_needing_update(
                force=False, semesters=["Spring 2024"]
            )

        assert result == []
