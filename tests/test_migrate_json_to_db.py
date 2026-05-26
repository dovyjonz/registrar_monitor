"""Tests for JSON migration module."""

import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from registrarmonitor.data.migrate_json_to_db import JSONMigrator


@pytest.fixture
def mock_config(tmp_path):
    """Return a config pointing to a temp data directory."""
    return {"directories": {"data_storage": str(tmp_path)}}


@pytest.fixture
def migrator(mock_config):
    with patch(
        "registrarmonitor.data.migrate_json_to_db.get_config", return_value=mock_config
    ):
        yield JSONMigrator()


def _write_json_snapshot(data_dir: Path, filename: str, data: dict) -> Path:
    f = data_dir / filename
    f.write_text(json.dumps(data))
    return f


class TestFindJsonFiles:
    def test_returns_empty_when_no_files(self, migrator, tmp_path):
        files = migrator.find_json_files()
        assert files == []

    def test_finds_json_files(self, migrator, tmp_path):
        _write_json_snapshot(tmp_path, "snap1.json", {"a": 1})
        _write_json_snapshot(tmp_path, "snap2.json", {"a": 2})
        files = migrator.find_json_files()
        assert len(files) == 2

    def test_ignores_non_json_files(self, migrator, tmp_path):
        (tmp_path / "data.txt").write_text("hello")
        files = migrator.find_json_files()
        assert files == []

    def test_sorts_by_filename(self, migrator, tmp_path):
        _write_json_snapshot(tmp_path, "b.json", {"a": 1})
        _write_json_snapshot(tmp_path, "a.json", {"a": 2})
        files = migrator.find_json_files()
        assert files[0].name == "a.json"
        assert files[1].name == "b.json"


class TestLoadJsonSnapshot:
    def test_loads_valid_snapshot(self, migrator, tmp_path):
        data = {
            "timestamp": "2024-01-15 10:00:00",
            "semester": "Spring 2024",
            "overall_fill": 0.75,
            "courses": {
                "CS 101": {
                    "department": "CS",
                    "average_fill": 0.83,
                    "sections": {
                        "10L": {
                            "section_type": "L",
                            "enrollment": 25,
                            "capacity": 30,
                            "fill": 0.83,
                        }
                    },
                }
            },
        }
        fp = _write_json_snapshot(tmp_path, "snap.json", data)
        snapshot = migrator.load_json_snapshot(fp)
        assert snapshot.timestamp == "2024-01-15 10:00:00"
        assert snapshot.semester == "Spring 2024"
        assert "CS 101" in snapshot.courses
        assert len(snapshot.courses["CS 101"].sections) == 1

    def test_raises_on_missing_field(self, migrator, tmp_path):
        fp = _write_json_snapshot(tmp_path, "bad.json", {"timestamp": "2024-01-15"})
        with pytest.raises(KeyError):
            migrator.load_json_snapshot(fp)

    def test_raises_on_invalid_json(self, migrator, tmp_path):
        fp = tmp_path / "bad.json"
        fp.write_text("not json")
        with pytest.raises(json.JSONDecodeError):
            migrator.load_json_snapshot(fp)


class TestCheckSnapshotExists:
    def _make_db(self):
        db = MagicMock()
        conn_ctx = MagicMock()
        db.get_connection.return_value = conn_ctx
        conn_inner = MagicMock()
        conn_ctx.__enter__.return_value = conn_inner
        cursor = MagicMock()
        conn_inner.cursor.return_value = cursor
        return db, cursor, conn_inner

    def test_returns_false_when_not_exists(self, migrator):
        with patch.object(migrator, "_get_db_manager") as mock_get_db:
            db, cursor, _ = self._make_db()
            cursor.fetchall.return_value = []
            mock_get_db.return_value = db
            assert migrator.check_snapshot_exists("2024-01-15", "Spring 2024") is False

    def test_returns_true_when_exists(self, migrator):
        with patch.object(migrator, "_get_db_manager") as mock_get_db:
            db, cursor, _ = self._make_db()
            cursor.fetchall.return_value = [("2024-01-15",)]
            mock_get_db.return_value = db
            assert migrator.check_snapshot_exists("2024-01-15", "Spring 2024") is True


class TestMigrateFile:
    def test_migrates_successfully(self, migrator, tmp_path):
        data = {
            "timestamp": "2024-01-15 10:00:00",
            "semester": "Spring 2024",
            "overall_fill": 0.75,
            "courses": {},
        }
        fp = _write_json_snapshot(tmp_path, "snap.json", data)

        with (
            patch.object(
                migrator, "load_json_snapshot", wraps=migrator.load_json_snapshot
            ),
            patch.object(migrator, "check_snapshot_exists", return_value=False),
            patch.object(migrator, "_get_db_manager") as mock_get_db,
        ):
            mock_db = mock_get_db.return_value
            result = migrator.migrate_file(fp)

        assert result is True
        mock_db.store_enrollment_snapshot.assert_called_once()

    def test_skips_if_already_exists(self, migrator, tmp_path):
        fp = _write_json_snapshot(
            tmp_path,
            "snap.json",
            {
                "timestamp": "2024-01-15",
                "semester": "Spring 2024",
                "overall_fill": 0.5,
                "courses": {},
            },
        )

        with (
            patch.object(migrator, "check_snapshot_exists", return_value=True),
            patch.object(migrator, "_get_db_manager") as mock_get_db,
        ):
            result = migrator.migrate_file(fp)

        assert result is True
        mock_get_db.return_value.store_enrollment_snapshot.assert_not_called()


class TestMigrateAll:
    def test_migrates_all_files(self, migrator, tmp_path):
        for i in range(3):
            _write_json_snapshot(
                tmp_path,
                f"snap{i}.json",
                {
                    "timestamp": f"2024-01-1{i} 10:00:00",
                    "semester": "Spring 2024",
                    "overall_fill": 0.5,
                    "courses": {},
                },
            )

        with (
            patch.object(migrator, "migrate_file", return_value=True),
        ):
            results = migrator.migrate_all()

        assert results["total"] == 3
        assert results["success"] == 3

    def test_dry_run_does_not_migrate(self, migrator, tmp_path):
        _write_json_snapshot(
            tmp_path,
            "snap.json",
            {
                "timestamp": "2024-01-15",
                "semester": "Spring 2024",
                "overall_fill": 0.5,
                "courses": {},
            },
        )

        with (
            patch.object(migrator, "migrate_file") as mock_migrate,
            patch.object(migrator, "load_json_snapshot") as mock_load,
            patch.object(migrator, "check_snapshot_exists") as mock_check,
        ):
            mock_load.return_value = MagicMock(
                timestamp="2024-01-15", semester="Spring 2024"
            )
            mock_check.return_value = False
            results = migrator.migrate_all(dry_run=True)

        assert results["total"] == 1
        assert results["success"] == 1
        mock_migrate.assert_not_called()

    def test_no_files(self, migrator):
        results = migrator.migrate_all()
        assert results["total"] == 0


class TestValidateMigration:
    def _make_db(self):
        db = MagicMock()
        conn_ctx = MagicMock()
        db.get_connection.return_value = conn_ctx
        conn_inner = MagicMock()
        conn_ctx.__enter__.return_value = conn_inner
        cursor = MagicMock()
        conn_inner.cursor.return_value = cursor
        return db, cursor, conn_inner

    def test_validates_successfully(self, migrator, tmp_path):
        _write_json_snapshot(
            tmp_path,
            "snap.json",
            {
                "timestamp": "2024-01-15",
                "semester": "Spring 2024",
                "overall_fill": 0.5,
                "courses": {},
            },
        )

        with patch.object(migrator, "_get_db_manager") as mock_get_db:
            db, cursor, _ = self._make_db()
            cursor.fetchone.return_value = (1,)
            mock_get_db.return_value = db
            result = migrator.validate_migration()

        assert result is True

    def test_fails_on_count_mismatch(self, migrator, tmp_path):
        _write_json_snapshot(
            tmp_path,
            "snap.json",
            {
                "timestamp": "2024-01-15",
                "semester": "Spring 2024",
                "overall_fill": 0.5,
                "courses": {},
            },
        )

        with patch.object(migrator, "_get_db_manager") as mock_get_db:
            mock_db = mock_get_db.return_value
            mock_conn = mock_db.get_connection.return_value.__enter__.return_value
            mock_conn.execute.return_value.fetchone.return_value = (0,)

            result = migrator.validate_migration()

        assert result is False


class TestGetDbManager:
    def test_creates_and_caches(self, migrator):
        with patch(
            "registrarmonitor.data.migrate_json_to_db.DatabaseManager.create_for_semester"
        ) as mock_create:
            mock_create.return_value = MagicMock()

            db1 = migrator._get_db_manager("Spring 2024")
            db2 = migrator._get_db_manager("Spring 2024")

        assert db1 is db2
        mock_create.assert_called_once_with("Spring 2024")
