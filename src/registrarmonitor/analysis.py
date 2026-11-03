"""Read-only cross-semester analysis with DuckDB.

SQLite stays the write path. DuckDB attaches each semester's enrollment
database read-only through its ``sqlite`` extension and exposes one union view
per v2 table with a ``semester`` column, so a single query can span semesters.
Saved queries live in ``analysis/<name>.sql`` at the repository root.
"""

from __future__ import annotations

import csv
import os
import re
import sqlite3
from collections.abc import Mapping, Sequence
from contextlib import closing
from datetime import datetime
from pathlib import Path
from typing import Any

import duckdb

from .config import get_config, get_timezone

ANALYSIS_DIR = Path(__file__).resolve().parent.parent.parent / "analysis"
DEFAULT_EXPORT_DIR = Path("output") / "open-data"

# v2 tables exposed as cross-semester views (see DATABASE.md).
UNION_TABLES = (
    "course_catalog",
    "section_catalog",
    "state_snapshot",
    "course_latest_state",
    "section_latest_state",
    "course_change_event",
    "section_change_event",
)


class AnalysisError(Exception):
    """A user-facing analysis failure."""


class ExtensionUnavailableError(AnalysisError):
    """DuckDB's sqlite extension could not be loaded (for example offline)."""


def semester_file_stem(semester: str) -> str:
    """``"Fall 2026"`` -> ``"fall_2026"``, matching ``enrollment_<stem>.db``."""
    name = re.sub(r"[^\w\s-]", "", semester.strip())
    return re.sub(r"[-\s]+", "_", name).lower()


def configured_databases(
    config: Mapping[str, Any] | None = None,
) -> dict[str, Path]:
    """Existing ``enrollment_*.db`` files for the semesters in settings.toml."""
    cfg = config if config is not None else get_config()
    data_dir = Path(cfg["directories"]["data_storage"])
    names = list(cfg.get("storage", {}).get("semesters", {}))
    names += [name for name in cfg.get("semesters", {}) if name not in names]
    databases: dict[str, Path] = {}
    for name in names:
        path = data_dir / f"enrollment_{semester_file_stem(name)}.db"
        if path.is_file():
            databases[name] = path
    return databases


def registration_rows(
    config: Mapping[str, Any], semesters: Sequence[str]
) -> tuple[
    list[tuple[str, int, str, datetime]], list[tuple[str, datetime, datetime | None]]
]:
    """Registration gates and windows from settings.toml, as naive local times."""
    zone = get_timezone(dict(config))

    def naive(text: str) -> datetime:
        value = datetime.fromisoformat(text)
        if value.tzinfo is not None:
            value = value.astimezone(zone).replace(tzinfo=None)
        return value

    gates: list[tuple[str, int, str, datetime]] = []
    windows: list[tuple[str, datetime, datetime | None]] = []
    for semester in semesters:
        semester_config = config.get("semesters", {}).get(semester, {})
        semester_gates = [
            (semester, int(priority), str(label), naive(text))
            for priority, items in semester_config.get("priorities", {}).items()
            for text, label in items
        ]
        gates.extend(semester_gates)
        if not semester_gates:
            continue
        closes = [
            naive(text)
            for text, label in semester_config.get("deadlines", [])
            if str(label).lower() == "close"
        ]
        windows.append(
            (semester, min(g[3] for g in semester_gates), max(closes, default=None))
        )
    return gates, windows


def _quote(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _check_v2(path: Path) -> None:
    try:
        with closing(sqlite3.connect(f"file:{path}?mode=ro", uri=True)) as connection:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
    except sqlite3.Error as error:
        raise AnalysisError(f"cannot read {path}: {error}") from error
    missing = [table for table in UNION_TABLES if table not in tables]
    if missing:
        raise AnalysisError(
            f"{path} is not a v2 enrollment database: missing {missing}"
        )


def _load_sqlite_extension(connection: duckdb.DuckDBPyConnection) -> None:
    try:
        connection.execute("LOAD sqlite")
        return
    except duckdb.Error:
        pass
    try:
        connection.execute("INSTALL sqlite")
        connection.execute("LOAD sqlite")
    except duckdb.Error as error:
        raise ExtensionUnavailableError(
            "DuckDB's sqlite extension is unavailable (it is downloaded on first "
            f"use and needs network access): {str(error).splitlines()[0]}"
        ) from error


def _attach_database(
    connection: duckdb.DuckDBPyConnection, alias: str, path: Path
) -> None:
    connection.execute(
        f"ATTACH {_quote(str(path))} AS {alias} (TYPE sqlite, READ_ONLY)"
    )


def open_analysis(
    databases: Mapping[str, Path],
    config: Mapping[str, Any] | None = None,
) -> duckdb.DuckDBPyConnection:
    """Open an in-memory DuckDB spanning ``databases`` (semester name -> path).

    The connection exposes the union views in ``UNION_TABLES`` plus the derived
    views in ``analysis/_base.sql`` and the ``registration_gate`` and
    ``registration_window`` tables from settings.toml. Nothing is written to
    the SQLite files.
    """
    if not databases:
        raise AnalysisError("no enrollment databases found")
    cfg = config if config is not None else get_config()
    for path in databases.values():
        _check_v2(Path(path))

    connection = duckdb.connect(":memory:")
    try:
        _load_sqlite_extension(connection)
        aliases: dict[str, str] = {}
        for index, (semester, path) in enumerate(databases.items()):
            aliases[semester] = f"sem_{index}"
            _attach_database(connection, aliases[semester], Path(path))
        for table in UNION_TABLES:
            exclude = " EXCLUDE (semester)" if table == "state_snapshot" else ""
            parts = [
                f"SELECT {_quote(semester)} AS semester, *{exclude} "
                f"FROM {alias}.{table}"
                for semester, alias in aliases.items()
            ]
            connection.execute(
                f"CREATE VIEW {table} AS " + " UNION ALL BY NAME ".join(parts)
            )
        gates, windows = registration_rows(cfg, list(databases))
        connection.execute(
            "CREATE TABLE registration_gate (semester VARCHAR, priority INTEGER, "
            "label VARCHAR, gate_at TIMESTAMP)"
        )
        connection.execute(
            "CREATE TABLE registration_window (semester VARCHAR, "
            "opens_at TIMESTAMP, closes_at TIMESTAMP)"
        )
        if gates:
            connection.executemany(
                "INSERT INTO registration_gate VALUES (?,?,?,?)", gates
            )
        if windows:
            connection.executemany(
                "INSERT INTO registration_window VALUES (?,?,?)", windows
            )
        connection.execute((ANALYSIS_DIR / "_base.sql").read_text())
    except BaseException:
        connection.close()
        raise
    return connection


def available_queries() -> list[str]:
    return sorted(
        path.stem
        for path in ANALYSIS_DIR.glob("*.sql")
        if not path.name.startswith("_")
    )


def run_query(
    connection: duckdb.DuckDBPyConnection, name: str
) -> tuple[list[str], list[tuple[Any, ...]]]:
    """Run ``analysis/<name>.sql`` and return its column names and rows."""
    if name not in available_queries():
        raise AnalysisError(
            f"unknown analysis {name!r}; available: {', '.join(available_queries())}"
        )
    result = connection.execute((ANALYSIS_DIR / f"{name}.sql").read_text())
    columns = [column[0] for column in result.description]
    return columns, result.fetchall()


def format_table(columns: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    text = [["" if value is None else str(value) for value in row] for row in rows]
    widths = [
        max([len(column)] + [len(row[i]) for row in text])
        for i, column in enumerate(columns)
    ]
    lines = [
        "  ".join(
            column.ljust(width) for column, width in zip(columns, widths, strict=True)
        ),
        "  ".join("-" * width for width in widths),
    ]
    lines += [
        "  ".join(v.ljust(w) for v, w in zip(row, widths, strict=True)) for row in text
    ]
    lines.append(f"({len(rows)} rows)")
    return "\n".join(lines)


def write_csv(
    path: Path, columns: Sequence[str], rows: Sequence[Sequence[Any]]
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


EXPORT_QUERY = """
    SELECT semester,
           snapshot_id,
           sequence_no,
           observed_at,
           course_code,
           department,
           course_title,
           section_code,
           section_type,
           instructor,
           enrollment_count,
           capacity_count,
           round(fill, 6) AS fill
    FROM section_history
    ORDER BY sequence_no, course_code, section_code
"""


def export_parquet(
    semester: str,
    out_dir: Path | str = DEFAULT_EXPORT_DIR,
    config: Mapping[str, Any] | None = None,
) -> Path:
    """Write ``<out_dir>/<semester stem>.parquet``: one row per section per snapshot."""
    cfg = config if config is not None else get_config()
    databases = configured_databases(cfg)
    if semester not in databases:
        known = ", ".join(databases) or "none"
        raise AnalysisError(f"no database for semester {semester!r}; found: {known}")
    target_dir = Path(out_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    target = target_dir / f"{semester_file_stem(semester)}.parquet"
    partial = target.with_name(target.name + ".partial")
    connection = open_analysis({semester: databases[semester]}, cfg)
    try:
        connection.execute(
            f"COPY ({EXPORT_QUERY}) TO {_quote(str(partial))} (FORMAT parquet)"
        )
    except BaseException:
        partial.unlink(missing_ok=True)
        raise
    finally:
        connection.close()
    os.replace(partial, target)
    return target
