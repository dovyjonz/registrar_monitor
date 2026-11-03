"""Tests for doctor diagnostics."""

from __future__ import annotations

import sqlite3
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import pytest

from registrarmonitor.data.checkpointed_state import CHECKPOINTED_SCHEMA_VERSION
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.operational import (
    _database_checks,
    build_doctor_report,
)


def _write_required_inputs(root: Path) -> None:
    (root / "assets/website/node_modules/.bin").mkdir(parents=True)
    (root / "data").mkdir()
    (root / ".node-version").write_text("24.18.0\n", encoding="utf-8")
    (root / ".python-version").write_text("3.13.5\n", encoding="utf-8")
    (root / "settings.toml").write_text(
        '[directories]\ndata_storage = "data"\nlogs = "logs"\n',
        encoding="utf-8",
    )
    for relative in (
        "pyproject.toml",
        "uv.lock",
        "assets/website/package.json",
        "assets/website/package-lock.json",
    ):
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    for tool in ("vite", "playwright"):
        (root / f"assets/website/node_modules/.bin/{tool}").touch()


def test_database_diagnostics_report_integrity_and_schema(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = DatabaseManager(
        db_path=str(data_dir / "enrollment_fall_2026.db"),
        semester="Fall 2026",
    )

    checks = _database_checks(tmp_path, {"directories": {"data_storage": "data"}})

    assert database.db_path.exists()
    assert checks[0]["status"] == "pass"
    assert checks[0]["integrity"] == "ok"
    assert checks[0]["schema_version"] == CHECKPOINTED_SCHEMA_VERSION


def test_database_diagnostics_fail_for_corrupt_database(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "enrollment_bad.db").write_bytes(b"not sqlite")

    checks = _database_checks(tmp_path, {"directories": {"data_storage": "data"}})

    assert checks[0]["status"] == "fail"


@pytest.mark.parametrize(
    ("schema_version", "status"), [(2, "pass"), (1, "warn"), (99, "warn")]
)
def test_database_diagnostics_recognize_expected_schema(
    tmp_path: Path, schema_version: int, status: str
):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    database = DatabaseManager(db_path=str(data_dir / "enrollment_fall_2026.db"))
    with closing(sqlite3.connect(database.db_path)) as connection:
        connection.execute(f"PRAGMA user_version = {schema_version}")

    check = _database_checks(tmp_path, {})[0]

    assert check["status"] == status
    assert check["expected_schema_version"] == 2


def test_doctor_report_summarizes_failures(tmp_path: Path):
    _write_required_inputs(tmp_path)
    with patch(
        "registrarmonitor.operational._tool_checks",
        return_value=[{"name": "tool:test", "status": "fail", "message": "missing"}],
    ):
        report = build_doctor_report(tmp_path)

    assert report["ok"] is False
    assert report["summary"]["fail"] == 1


def test_schema_version_is_stored_in_new_databases(tmp_path: Path):
    database = DatabaseManager(db_path=str(tmp_path / "schema.db"))

    with sqlite3.connect(database.db_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert version == CHECKPOINTED_SCHEMA_VERSION
