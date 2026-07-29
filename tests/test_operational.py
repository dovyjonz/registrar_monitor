"""Tests for doctor and machine-readable baseline diagnostics."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from unittest.mock import patch

from registrarmonitor.data.database_manager import (
    EXPECTED_SCHEMA_VERSION,
    DatabaseManager,
)
from registrarmonitor.operational import (
    _database_checks,
    _writable_check,
    build_baseline,
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
    assert checks[0]["schema_version"] == EXPECTED_SCHEMA_VERSION


def test_database_diagnostics_fail_for_corrupt_database(tmp_path: Path):
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    (data_dir / "enrollment_bad.db").write_bytes(b"not sqlite")

    checks = _database_checks(tmp_path, {"directories": {"data_storage": "data"}})

    assert checks[0]["status"] == "fail"


def test_database_diagnostics_support_absolute_directory_outside_root(
    tmp_path: Path,
):
    root = tmp_path / "checkout"
    data_dir = tmp_path / "runtime-data"
    root.mkdir()
    data_dir.mkdir()
    database = data_dir / "enrollment_absolute.db"
    DatabaseManager(db_path=str(database), semester="Fall 2026")

    checks = _database_checks(
        root,
        {"directories": {"data_storage": str(data_dir)}},
    )

    assert checks[0]["status"] == "pass"
    assert checks[0]["path"] == str(database)


def test_writable_check_rejects_regular_file(tmp_path: Path):
    configured_directory = tmp_path / "logs"
    configured_directory.write_text("not a directory", encoding="utf-8")

    result = _writable_check("path:logs", configured_directory)

    assert result["status"] == "fail"
    assert result["message"] == "exists but is not a directory"


def test_doctor_report_summarizes_failures(tmp_path: Path):
    _write_required_inputs(tmp_path)
    with patch(
        "registrarmonitor.operational._tool_checks",
        return_value=[{"name": "tool:test", "status": "fail", "message": "missing"}],
    ):
        report = build_doctor_report(tmp_path)

    assert report["ok"] is False
    assert report["summary"]["fail"] == 1


def test_baseline_embeds_input_hashes_and_doctor_report(tmp_path: Path):
    _write_required_inputs(tmp_path)
    with patch(
        "registrarmonitor.operational._tool_checks",
        return_value=[{"name": "tool:test", "status": "pass", "message": "available"}],
    ):
        baseline = build_baseline(tmp_path)

    assert len(baseline["inputs"]["uv.lock"]) == 64
    assert baseline["doctor"]["ok"] is True
    assert baseline["platform"]["python"]


def test_schema_version_is_stored_in_new_databases(tmp_path: Path):
    database = DatabaseManager(db_path=str(tmp_path / "schema.db"))

    with sqlite3.connect(database.db_path) as connection:
        version = connection.execute("PRAGMA user_version").fetchone()[0]

    assert version == EXPECTED_SCHEMA_VERSION
