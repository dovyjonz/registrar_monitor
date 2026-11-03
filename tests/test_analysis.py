"""Tests for DuckDB cross-semester analysis and Parquet export.

DuckDB's ``sqlite`` extension is downloaded on first use, so it may be missing
offline. Most tests replace only the attach step with a copy of the SQLite
tables into DuckDB schemas, which exercises every view and saved query. The
extension test skips cleanly when it cannot be loaded.
"""

import sqlite3
from contextlib import closing
from pathlib import Path
from typing import Any

import duckdb
import pytest

from registrarmonitor import analysis
from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.main import create_parser
from registrarmonitor.models import Course, EnrollmentSnapshot, Section

pytestmark = pytest.mark.integration

_TYPES = {"INTEGER": "BIGINT", "TEXT": "VARCHAR", "REAL": "DOUBLE", "BLOB": "BLOB"}


def _copy_attach(connection: Any, alias: str, path: Path) -> None:
    """Stand-in for ATTACH ... (TYPE sqlite): copy tables into a schema."""
    connection.execute(f"CREATE SCHEMA {alias}")
    with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as source:
        for table in analysis.UNION_TABLES:
            info = source.execute(f"PRAGMA table_info({table})").fetchall()
            columns = ", ".join(
                f"{row[1]} {_TYPES.get(row[2].upper(), 'VARCHAR')}" for row in info
            )
            connection.execute(f"CREATE TABLE {alias}.{table} ({columns})")
            rows = source.execute(f"SELECT * FROM {table}").fetchall()
            if rows:
                marks = ",".join("?" * len(info))
                connection.executemany(
                    f"INSERT INTO {alias}.{table} VALUES ({marks})", rows
                )


@pytest.fixture
def copy_attach(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(analysis, "_load_sqlite_extension", lambda connection: None)
    monkeypatch.setattr(analysis, "_attach_database", _copy_attach)


def _snapshot(
    semester: str,
    timestamp: str,
    sections: dict[str, tuple[int, int]],
    course: str = "CSCI 101",
    department: str = "CSCI",
) -> EnrollmentSnapshot:
    built = {
        code: Section(
            section_id=code,
            section_type="L",
            enrollment=enrolled,
            capacity=capacity,
            fill=enrolled / capacity,
            instructor="Ada",
        )
        for code, (enrolled, capacity) in sections.items()
    }
    total_enrolled = sum(e for e, _ in sections.values())
    total_capacity = sum(c for _, c in sections.values())
    return EnrollmentSnapshot(
        timestamp=timestamp,
        semester=semester,
        overall_fill=total_enrolled / total_capacity,
        courses={
            course: Course(
                course_code=course,
                department=department,
                course_title="Intro",
                sections=built,
                average_fill=total_enrolled / total_capacity,
            )
        },
    )


@pytest.fixture
def config(tmp_path: Path) -> dict[str, Any]:
    data = tmp_path / "data"
    data.mkdir()

    fall25 = CheckpointedStateStore(data / "enrollment_fall_2025.db")
    for stamp, sections in [
        ("2025-08-01 08:00:00", {"1L": (0, 20)}),
        ("2025-08-06 10:00:00", {"1L": (5, 20)}),
        ("2025-08-07 10:00:00", {"1L": (12, 20)}),
    ]:
        fall25.write_snapshot(_snapshot("Fall 2025", stamp, sections))

    fall26 = CheckpointedStateStore(data / "enrollment_fall_2026.db")
    for stamp, sections in [
        ("2026-08-01 08:00:00", {"1L": (0, 20)}),
        ("2026-08-05 08:59:00", {"1L": (0, 20)}),
        ("2026-08-05 09:30:00", {"1L": (10, 20)}),
        ("2026-08-05 15:00:00", {"1L": (16, 30)}),
        ("2026-08-06 12:00:00", {"1L": (20, 30), "2L": (1, 10)}),
        ("2026-08-30 12:00:00", {"1L": (30, 30)}),
    ]:
        fall26.write_snapshot(_snapshot("Fall 2026", stamp, sections))

    return {
        "timezone": "Asia/Almaty",
        "directories": {"data_storage": str(data)},
        "storage": {"semesters": {"Fall 2025": {}, "Fall 2026": {}, "Spring 2027": {}}},
        "semesters": {
            "Fall 2026": {
                "deadlines": [["2026-08-28T17:30:00", "Close"]],
                "priorities": {
                    "1": [["2026-08-05T09:00:00", "Y4+"]],
                    "2": [["2026-08-13T09:00:00", "ALL"]],
                },
            }
        },
    }


def test_configured_databases_skips_semesters_without_a_file(
    config: dict[str, Any],
) -> None:
    assert list(analysis.configured_databases(config)) == ["Fall 2025", "Fall 2026"]


def test_union_views_span_semesters(config: dict[str, Any], copy_attach: None) -> None:
    connection = analysis.open_analysis(analysis.configured_databases(config), config)
    rows = connection.execute(
        "SELECT semester, count(*) FROM snapshots GROUP BY semester ORDER BY 1"
    ).fetchall()
    assert rows == [("Fall 2025", 3), ("Fall 2026", 5)]
    last = connection.execute(
        "SELECT enrollment_count FROM section_history WHERE semester = 'Fall 2026' "
        "AND section_code = '2L' ORDER BY sequence_no"
    ).fetchall()
    assert last == [(1,)]
    connection.close()


@pytest.mark.parametrize("name", analysis.available_queries())
def test_every_saved_query_runs_across_two_semesters(
    config: dict[str, Any], copy_attach: None, name: str
) -> None:
    connection = analysis.open_analysis(analysis.configured_databases(config), config)
    columns, rows = analysis.run_query(connection, name)
    assert columns
    assert rows, f"{name} returned no rows"
    connection.close()


def test_saved_query_results(config: dict[str, Any], copy_attach: None) -> None:
    connection = analysis.open_analysis(analysis.configured_databases(config), config)

    columns, rows = analysis.run_query(connection, "fill_speed_after_gates")
    gate = dict(zip(columns, rows[0], strict=True))
    assert gate["label"] == "Y4+"
    assert gate["fill_at_gate"] == 0.0
    assert gate["fill_plus_1h"] == 0.5
    assert gate["fill_plus_6h"] == pytest.approx(0.5333, abs=1e-4)
    assert gate["seats_taken_24h"] == 16

    columns, rows = analysis.run_query(connection, "capacity_changes_mid_registration")
    changes = [dict(zip(columns, row, strict=True)) for row in rows]
    assert [c["change"] for c in changes] == ["capacity_changed", "section_added"]
    assert changes[0]["capacity_delta"] == 10
    assert changes[0]["gate_in_force"] == "Y4+"

    columns, rows = analysis.run_query(connection, "year_over_year_courses")
    yoy = dict(zip(columns, rows[0], strict=True))
    assert (yoy["term"], yoy["year"], yoy["course_code"]) == ("Fall", 2026, "CSCI 101")
    assert yoy["prev_final_enrolled"] == 12
    assert yoy["final_enrolled"] == 30

    columns, rows = analysis.run_query(connection, "fill_curves_by_department")
    assert {row[0] for row in rows} == {"Fall 2025", "Fall 2026"}
    connection.close()


def test_unknown_query_is_rejected(config: dict[str, Any], copy_attach: None) -> None:
    connection = analysis.open_analysis(analysis.configured_databases(config), config)
    with pytest.raises(analysis.AnalysisError, match="unknown analysis"):
        analysis.run_query(connection, "nope")
    connection.close()


def test_non_v2_database_is_rejected(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy.db"
    with closing(sqlite3.connect(legacy)) as connection:
        connection.execute("CREATE TABLE snapshots (id INTEGER)")
    with pytest.raises(analysis.AnalysisError, match="not a v2"):
        analysis.open_analysis({"Old": legacy}, {})


def test_export_parquet_writes_flat_per_snapshot_rows(
    config: dict[str, Any], copy_attach: None, tmp_path: Path
) -> None:
    out = tmp_path / "open"
    target = analysis.export_parquet("Fall 2026", out, config)

    assert target == out / "fall_2026.parquet"
    assert not list(out.glob("*.partial"))
    reader = duckdb.connect()
    columns = [
        row[0]
        for row in reader.execute(f"DESCRIBE SELECT * FROM '{target}'").fetchall()
    ]
    assert columns[:5] == [
        "semester",
        "snapshot_id",
        "sequence_no",
        "observed_at",
        "course_code",
    ]
    found = reader.execute(
        f"SELECT count(*), list(DISTINCT semester) FROM '{target}'"
    ).fetchone()
    assert found is not None
    count, semesters = found
    assert count == 6  # 5 distinct states, 2L present in one
    assert semesters == ["Fall 2026"]


def test_export_unknown_semester(config: dict[str, Any], tmp_path: Path) -> None:
    with pytest.raises(analysis.AnalysisError, match="no database"):
        analysis.export_parquet("Spring 2027", tmp_path, config)


def test_real_sqlite_extension_attaches_read_only(
    config: dict[str, Any], tmp_path: Path
) -> None:
    probe = duckdb.connect()
    try:
        analysis._load_sqlite_extension(probe)
    except analysis.ExtensionUnavailableError as error:
        pytest.skip(str(error))
    finally:
        probe.close()
    databases = analysis.configured_databases(config)
    connection = analysis.open_analysis(databases, config)
    assert connection.execute("SELECT count(*) FROM snapshots").fetchone() == (8,)
    with pytest.raises(duckdb.Error):
        connection.execute("DELETE FROM sem_0.state_snapshot")
    connection.close()


def test_cli_parser_accepts_analysis_commands() -> None:
    parser = create_parser()
    args = parser.parse_args(["analyze", "fill_curves_by_department", "--csv", "x.csv"])
    assert (args.command, args.name, str(args.csv)) == (
        "analyze",
        "fill_curves_by_department",
        "x.csv",
    )
    args = parser.parse_args(["export-parquet", "Fall 2026", "--out", "o"])
    assert (args.command, args.semester, str(args.out)) == (
        "export-parquet",
        "Fall 2026",
        "o",
    )


def test_cli_handlers_print_table_write_csv_and_export(
    config: dict[str, Any],
    copy_attach: None,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse
    import asyncio

    from registrarmonitor import main

    monkeypatch.setattr(analysis, "get_config", lambda: config)
    ask = argparse.Namespace(name=None, csv=None)
    assert asyncio.run(main.handle_analyze_command(ask)) == 0
    assert "year_over_year_courses" in capsys.readouterr().out

    ask = argparse.Namespace(name="year_over_year_courses", csv=None)
    assert asyncio.run(main.handle_analyze_command(ask)) == 0
    assert "CSCI 101" in capsys.readouterr().out

    csv_path = tmp_path / "out" / "yoy.csv"
    ask = argparse.Namespace(name="year_over_year_courses", csv=csv_path)
    assert asyncio.run(main.handle_analyze_command(ask)) == 0
    assert csv_path.read_text().splitlines()[0].startswith("term,year,course_code")

    ask = argparse.Namespace(name="missing", csv=None)
    assert asyncio.run(main.handle_analyze_command(ask)) == 1

    export = argparse.Namespace(semester="Fall 2026", out=tmp_path / "pq")
    assert asyncio.run(main.handle_export_parquet_command(export)) == 0
    assert (tmp_path / "pq" / "fall_2026.parquet").is_file()
    export = argparse.Namespace(semester="Nope", out=tmp_path / "pq")
    assert asyncio.run(main.handle_export_parquet_command(export)) == 1


def test_missing_extension_is_reported(
    config: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(connection: Any) -> None:
        raise analysis.ExtensionUnavailableError("offline")

    monkeypatch.setattr(analysis, "_load_sqlite_extension", fail)
    with pytest.raises(analysis.ExtensionUnavailableError):
        analysis.open_analysis(analysis.configured_databases(config), config)
