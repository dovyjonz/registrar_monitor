"""Tests for the checksum-based incremental update detection."""

from unittest.mock import patch

from registrarmonitor.website.checksums import (
    compute_semester_hash,
    get_semesters_needing_update,
    load_checksums,
    save_checksums,
    update_checksum,
)


class TestComputeSemesterHash:
    def test_returns_consistent_hash(self):
        with patch("registrarmonitor.website.checksums.DatabaseManager") as mock_db_cls:
            mock_db = mock_db_cls.return_value
            mock_conn = mock_db.get_connection.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = (
                10,
                "2024-01-15 10:00:00",
            )

            h1 = compute_semester_hash("Spring 2024")
            h2 = compute_semester_hash("Spring 2024")

        assert h1 == h2
        assert isinstance(h1, str)
        assert len(h1) == 12

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
            mock_db_cls.return_value = make_db()  # noqa
            h2 = compute_semester_hash("Fall 2024")

        assert h1 != h2


class TestLoadChecksums:
    def test_returns_empty_when_no_file(self, tmp_path):
        with patch(
            "registrarmonitor.website.checksums.CHECKSUMS_FILE",
            tmp_path / "nonexistent.json",
        ):
            assert load_checksums() == {}

    def test_loads_valid_json(self, tmp_path):
        f = tmp_path / ".checksums.json"
        f.write_text('{"Spring 2024": "abc123"}')
        with patch("registrarmonitor.website.checksums.CHECKSUMS_FILE", f):
            result = load_checksums()
        assert result == {"Spring 2024": "abc123"}

    def test_handles_corrupted_json(self, tmp_path):
        f = tmp_path / ".checksums.json"
        f.write_text("not json")
        with patch("registrarmonitor.website.checksums.CHECKSUMS_FILE", f):
            result = load_checksums()
        assert result == {}


class TestSaveChecksums:
    def test_writes_to_file(self, tmp_path):
        f = tmp_path / ".checksums.json"
        with patch("registrarmonitor.website.checksums.CHECKSUMS_FILE", f):
            save_checksums({"Spring 2024": "abc123"})

        assert f.exists()
        assert "Spring 2024" in f.read_text()

    def test_creates_parent_directories(self, tmp_path):
        f = tmp_path / "subdir" / ".checksums.json"
        with patch("registrarmonitor.website.checksums.CHECKSUMS_FILE", f):
            save_checksums({"test": "hash"})

        assert f.exists()


class TestGetSemestersNeedingUpdate:
    def test_force_returns_all(self):
        with (
            patch("registrarmonitor.website.checksums.compute_semester_hash"),
            patch(
                "registrarmonitor.website.checksums.ALL_SEMESTERS",
                ["Spring 2024", "Fall 2024"],
            ),
        ):
            result = get_semesters_needing_update(force=True)

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
            patch(
                "registrarmonitor.website.checksums.ALL_SEMESTERS",
                ["Spring 2024", "Fall 2024"],
            ),
        ):
            result = get_semesters_needing_update(force=False)

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
            patch("registrarmonitor.website.checksums.ALL_SEMESTERS", ["Spring 2024"]),
        ):
            result = get_semesters_needing_update(force=False)

        assert result == []


class TestUpdateChecksum:
    def test_updates_and_saves(self, tmp_path):
        f = tmp_path / ".checksums.json"
        with (
            patch("registrarmonitor.website.checksums.CHECKSUMS_FILE", f),
            patch(
                "registrarmonitor.website.checksums.compute_semester_hash",
                return_value="newhash123",
            ),
        ):
            update_checksum("Spring 2024")

        assert f.exists()
        assert "newhash123" in f.read_text()
