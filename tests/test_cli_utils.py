"""Tests for CLI utility functions."""

from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.cli.utils import detect_active_semester


def _make_db(return_id, ts=None):
    """Build a simplified mock DatabaseManager."""

    class FakeDB:
        def __init__(self, return_id, ts):
            self.return_id = return_id
            self.ts = ts

        def get_latest_snapshot_id(self):
            return self.return_id

        def get_connection(self):
            return _FakeConn(self.ts)

    class _FakeCursor:
        def __init__(self, ts):
            self.ts = ts

        def execute(self, query, params):
            return self

        def fetchone(self):
            if self.ts is not None:
                return (self.ts,)
            return None

    class _FakeConn:
        def __init__(self, ts):
            self.ts = ts

        def __enter__(self):
            return _FakeConnCursor(self.ts)

        def __exit__(self, *args):
            pass

    class _FakeConnCursor:
        def __init__(self, ts):
            self.ts = ts

        def cursor(self):
            return _FakeCursor(self.ts)

    return FakeDB(return_id, ts)


@pytest.mark.asyncio
async def test_no_databases_returns_none():
    with patch(
        "registrarmonitor.cli.utils.DatabaseManager.get_semester_databases",
        return_value={},
    ):
        result = await detect_active_semester()
    assert result is None


@pytest.mark.asyncio
async def test_single_semester_with_snapshot():
    fake_db = _make_db(1, "2024-01-15 10:00:00")
    with patch(
        "registrarmonitor.cli.utils.DatabaseManager.get_semester_databases",
        return_value={"Spring 2024": "/tmp/enrollment_spring_2024.db"},
    ):
        with patch(
            "registrarmonitor.cli.utils.DatabaseManager.create_for_semester",
            return_value=fake_db,
        ):
            result = await detect_active_semester()
    assert result == "Spring 2024"


@pytest.mark.asyncio
async def test_multiple_semesters_picks_latest():
    fake_db_spring = _make_db(1, "2024-01-15 10:00:00")
    fake_db_fall = _make_db(2, "2024-09-01 10:00:00")

    def create_for_semester(semester):
        return fake_db_spring if "Spring" in semester else fake_db_fall

    with patch(
        "registrarmonitor.cli.utils.DatabaseManager.get_semester_databases",
        return_value={"Spring 2024": "/tmp/spring.db", "Fall 2024": "/tmp/fall.db"},
    ):
        with patch(
            "registrarmonitor.cli.utils.DatabaseManager.create_for_semester",
            side_effect=create_for_semester,
        ):
            result = await detect_active_semester()
    assert result == "Fall 2024"


@pytest.mark.asyncio
async def test_all_empty_databases_returns_none():
    fake_db = _make_db(None)

    with patch(
        "registrarmonitor.cli.utils.DatabaseManager.get_semester_databases",
        return_value={"Spring 2024": "/tmp/spring.db"},
    ):
        with patch(
            "registrarmonitor.cli.utils.DatabaseManager.create_for_semester",
            return_value=fake_db,
        ):
            result = await detect_active_semester()
    assert result is None


@pytest.mark.asyncio
async def test_exception_in_semester_skipped():
    def create_for_semester(semester):
        raise Exception("corrupt DB")

    with patch(
        "registrarmonitor.cli.utils.DatabaseManager.get_semester_databases",
        return_value={"Spring 2024": "/tmp/spring.db"},
    ):
        with patch(
            "registrarmonitor.cli.utils.DatabaseManager.create_for_semester",
            side_effect=create_for_semester,
        ):
            result = await detect_active_semester()
    assert result is None
