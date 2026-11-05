"""Opening storage never creates files; the poll picks the semester explicitly."""

import sqlite3
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.cli.commands import DatabaseCommands, PollCommand
from registrarmonitor.config import get_settings
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.excel_reader import ExcelReader


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    path = tmp_path / "data"
    monkeypatch.setenv("DIRECTORIES__DATA_STORAGE", str(path))
    get_settings.cache_clear()
    yield path
    get_settings.cache_clear()


def _rows():
    return [
        {
            "Course Abbr": "CS 101",
            "S/T": "1L",
            "Enr": 5,
            "Cap": 10,
            "Fill": 0.5,
            "Instructor": "Smith",
            "Level": "UG",
            "Course Title": "Intro",
        }
    ]


def _files(path):
    return sorted(p.name for p in path.rglob("*")) if path.exists() else []


@pytest.mark.parametrize("read_only", [False, True])
def test_opening_uninitialized_semester_creates_nothing(data_dir, read_only):
    with pytest.raises(sqlite3.DatabaseError, match="monitor db initialize"):
        DatabaseManager(semester="Fall 2026", read_only=read_only)

    assert not data_dir.exists()


def test_opening_leaves_an_empty_database_file_untouched(data_dir):
    data_dir.mkdir()
    stray = data_dir / "enrollment_fall_2026.db"
    stray.touch()

    with pytest.raises(sqlite3.DatabaseError, match="monitor db initialize"):
        DatabaseManager(semester="Fall 2026")

    assert _files(data_dir) == ["enrollment_fall_2026.db"]
    assert stray.stat().st_size == 0


def test_configured_storage_requires_a_semester(data_dir):
    with pytest.raises(ValueError, match="semester"):
        DatabaseManager()

    assert not data_dir.exists()


@pytest.mark.asyncio
async def test_failed_poll_in_uninitialized_directory_creates_no_database(data_dir):
    with (
        patch.object(ExcelReader, "read_semester", return_value="Fall 2026"),
        patch.object(
            ExcelReader, "read_excel_data", return_value=("Fall 2026", "t", _rows())
        ),
    ):
        result = await PollCommand().run_with_result(file_path="unused.xls")

    assert result.success is False
    assert not data_dir.exists()


@pytest.mark.asyncio
async def test_poll_without_any_semester_fails_without_files(data_dir):
    with patch.object(ExcelReader, "read_semester", side_effect=OSError("bad file")):
        result = await PollCommand().run_with_result(file_path="unused.xls")

    assert result.success is False
    assert not data_dir.exists()


@pytest.mark.asyncio
async def test_file_poll_uses_its_configured_semester_over_the_freshest(
    data_dir, tmp_path
):
    upload = tmp_path / "x.xls"
    upload.touch()
    commands = DatabaseCommands()
    assert await commands.initialize_fresh_storage(semester="Fall 2025")
    assert await commands.initialize_fresh_storage(semester="Fall 2026")

    for semester, stamp in (
        ("Fall 2025", "2025-08-13 09:30:20"),
        ("Fall 2026", "2026-08-01 09:00:00"),
        ("Fall 2025", "2025-08-13 09:34:42"),
    ):
        with (
            patch.object(ExcelReader, "read_semester", return_value=semester),
            patch.object(
                ExcelReader,
                "read_excel_data",
                return_value=(semester, stamp, _rows()),
            ),
        ):
            result = await PollCommand().run_with_result(file_path=str(upload))
        assert result.success is True
        assert result.semester == semester

    for semester in ("Fall 2025", "Fall 2026"):
        assert DatabaseManager(semester=semester).get_latest_snapshot_id() is not None
    assert {name for name in _files(data_dir) if name.endswith(".db")} == {
        "enrollment_fall_2025.db",
        "enrollment_fall_2026.db",
    }


@pytest.mark.asyncio
async def test_explicit_semester_wins_over_the_file(data_dir):
    with (
        patch.object(ExcelReader, "read_semester", return_value="Fall 2026"),
        patch("registrarmonitor.cli.commands.MonitoringService") as service,
    ):
        service.side_effect = RuntimeError("stop")
        await PollCommand().run_with_result(file_path="x.xls", semester="Fall 2025")

    service.assert_called_once_with(semester="Fall 2025")


@pytest.mark.asyncio
async def test_unconfigured_file_semester_is_not_used(data_dir):
    with (
        patch.object(ExcelReader, "read_semester", return_value="Winter 1999"),
        patch(
            "registrarmonitor.cli.commands.detect_active_semester",
            return_value=None,
        ),
    ):
        assert await PollCommand()._resolve_semester("x.xls") is None
