"""Tests for the checksum-based incremental update detection."""

import pytest

pytestmark = pytest.mark.unit


from unittest.mock import patch

from registrarmonitor.website.checksums import (
    compute_semester_hash,
    get_semesters_needing_update,
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
                side_effect=lambda s: "newhash" if s == "Spring 2024" else "samehash",
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
