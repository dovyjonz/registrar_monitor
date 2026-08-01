"""Run repeatable database, website, and browser performance measurements."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import platform
import shutil
import sqlite3
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from statistics import median
from threading import Thread
from typing import Any
from unittest.mock import patch

from dotenv import load_dotenv

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import EnrollmentSnapshot
from registrarmonitor.services.website_service import WebsiteService
from registrarmonitor.website.config import semester_to_filename, semester_to_slug
from registrarmonitor.website.templates import build_redirect_index

ROOT = Path(__file__).resolve().parent.parent
WEBSITE = ROOT / "assets" / "website"
SEMESTER = "Summer 2026"
FORMAT_VERSION = 1
DEFAULT_SEED = 20260729
PREVIEW_BRANCH = "performance-baseline-2026-07-29"


def percentile_nearest_rank(samples: Sequence[int], percentile: float) -> int:
    """Return a nearest-rank percentile for non-empty integer samples."""
    if not samples:
        raise ValueError("samples must not be empty")
    if not 0 < percentile <= 100:
        raise ValueError("percentile must be in (0, 100]")
    ordered = sorted(samples)
    return ordered[math.ceil(percentile / 100 * len(ordered)) - 1]


def summarize(samples: Sequence[int], unit: str = "ns") -> dict[str, Any]:
    """Retain raw samples and calculate stable summary statistics."""
    if not samples:
        raise ValueError("samples must not be empty")
    values = [int(value) for value in samples]
    return {
        "unit": unit,
        "samples": values,
        "median": median(values),
        "p95": percentile_nearest_rank(values, 95),
    }


def timed(operation: Callable[[], Any]) -> int:
    start = time.perf_counter_ns()
    operation()
    return time.perf_counter_ns() - start


@contextmanager
def _database_connection(database: str | Path, **kwargs: Any):
    """Yield a SQLite connection and always close it after use."""
    connection = sqlite3.connect(database, **kwargs)
    try:
        yield connection
    finally:
        connection.close()


def _integrity_check(path: Path) -> tuple[Any, ...]:
    """Run an integrity check without leaking its SQLite connection."""
    with _database_connection(path) as connection:
        return connection.execute("PRAGMA integrity_check").fetchone()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def database_metadata(path: Path) -> dict[str, Any]:
    """Return safe aggregate metadata and validate a SQLite input."""
    with _database_connection(f"file:{path}?mode=ro", uri=True) as connection:
        integrity = connection.execute("PRAGMA integrity_check").fetchone()[0]
        foreign_keys = connection.execute("PRAGMA foreign_key_check").fetchall()
        schema_version = connection.execute("PRAGMA user_version").fetchone()[0]
        counts = {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("snapshots", "courses", "sections", "enrollment_data")
        }
        allocation_rows = connection.execute(
            """
            SELECT d.name, sum(d.pgsize), count(*),
                   CASE
                     WHEN m.type = 'index' OR d.name LIKE 'sqlite_autoindex_%'
                     THEN 'index' ELSE 'table'
                   END
            FROM dbstat d
            LEFT JOIN sqlite_master m ON m.name = d.name
            GROUP BY d.name
            ORDER BY sum(d.pgsize) DESC, d.name
            """
        ).fetchall()
        page_size = connection.execute("PRAGMA page_size").fetchone()[0]
    if integrity != "ok" or foreign_keys:
        raise ValueError("database input failed SQLite integrity validation")
    return {
        "sha256": sha256_file(path),
        "bytes": path.stat().st_size,
        "schema_version": schema_version,
        "integrity": integrity,
        "foreign_key_violations": len(foreign_keys),
        "counts": counts,
        "allocation": {
            "page_size_bytes": page_size,
            "objects": [
                {
                    "name": name,
                    "kind": kind,
                    "bytes": allocated_bytes,
                    "pages": pages,
                }
                for name, allocated_bytes, pages, kind in allocation_rows
            ],
            "table_bytes": sum(row[1] for row in allocation_rows if row[3] == "table"),
            "index_bytes": sum(row[1] for row in allocation_rows if row[3] == "index"),
        },
    }


def create_synthetic_database(
    path: Path,
    *,
    seed: int = DEFAULT_SEED,
    courses: int = 75,
    sections: int = 114,
    snapshots: int = 200,
    semester: str = SEMESTER,
) -> None:
    """Create deterministic, non-secret enrollment data at a representative scale."""
    if min(courses, sections, snapshots) <= 0 or sections < courses:
        raise ValueError(
            "synthetic dimensions must be positive and sections >= courses"
        )
    DatabaseManager(db_path=str(path), semester=semester)
    with _database_connection(path) as connection:
        course_rows = [
            (f"PERF {index + 100:03d}", f"Synthetic course {index + 1}", "PERF")
            for index in range(courses)
        ]
        connection.executemany(
            "INSERT INTO courses (course_code, course_title, department) VALUES (?, ?, ?)",
            course_rows,
        )
        course_ids = [
            row[0] for row in connection.execute("SELECT course_id FROM courses")
        ]
        section_rows = []
        for index in range(sections):
            course_id = course_ids[index % courses]
            section_rows.append(
                (
                    course_id,
                    f"{index // courses + 1:03d}",
                    "Lecture" if index % 3 else "Lab",
                    f"Synthetic Instructor {index % 17 + 1}",
                )
            )
        connection.executemany(
            "INSERT INTO sections "
            "(course_id, section_code, section_type, instructor) VALUES (?, ?, ?, ?)",
            section_rows,
        )
        section_ids = [
            row[0] for row in connection.execute("SELECT section_id FROM sections")
        ]
        base = datetime(2026, 4, 1, tzinfo=UTC)
        for snapshot_index in range(snapshots):
            timestamp = (base + timedelta(minutes=5 * snapshot_index)).isoformat()
            overall = 0.35 + ((snapshot_index + seed) % 60) / 100
            cursor = connection.execute(
                "INSERT INTO snapshots "
                "(timestamp, last_seen_at, semester, overall_fill) VALUES (?, ?, ?, ?)",
                (timestamp, timestamp, semester, overall),
            )
            if cursor.lastrowid is None:
                raise sqlite3.Error("synthetic snapshot insert returned no row ID")
            snapshot_id = cursor.lastrowid
            rows = []
            for section_index, section_id in enumerate(section_ids):
                capacity = 20 + (section_index + seed) % 31
                enrollment = min(
                    capacity,
                    (snapshot_index + section_index * 3 + seed) % (capacity + 1),
                )
                fill = enrollment / capacity
                status = "FULL" if fill >= 1 else "NEAR" if fill >= 0.75 else "OPEN"
                rows.append(
                    (
                        snapshot_id,
                        section_id,
                        status,
                        enrollment,
                        capacity,
                        fill,
                    )
                )
            connection.executemany(
                "INSERT INTO enrollment_data "
                "(snapshot_id, section_id, status, enrollment_count, "
                "capacity_count, fill_percentage) VALUES (?, ?, ?, ?, ?, ?)",
                rows,
            )
        connection.commit()
        fixed_time = "2026-07-29 00:00:00"
        for table in ("courses", "sections"):
            connection.execute(
                f"UPDATE {table} SET created_at = ?, updated_at = ?",
                (fixed_time, fixed_time),
            )
        for table in ("snapshots", "enrollment_data"):
            connection.execute(
                f"UPDATE {table} SET created_at = ?",
                (fixed_time,),
            )
        connection.commit()


def _latest_course_code(path: Path) -> str:
    with _database_connection(path) as connection:
        return str(
            connection.execute(
                "SELECT course_code FROM courses ORDER BY course_code LIMIT 1"
            ).fetchone()[0]
        )


def _change_first_section(snapshot: EnrollmentSnapshot) -> None:
    """Make one guaranteed-valid enrollment change in a benchmark snapshot."""
    first_course = next(iter(snapshot.courses.values()))
    first_section = next(iter(first_course.sections.values()))
    original = first_section.enrollment
    first_section.enrollment = original - 1 if original > 0 else 1
    if first_section.enrollment > first_section.capacity:
        raise ValueError(
            "benchmark section cannot accept a synthetic enrollment change"
        )
    first_section.fill = first_section.enrollment / first_section.capacity
    if first_section.enrollment == original:
        raise ValueError("benchmark failed to construct a changed snapshot")


def _write_changed_snapshot(
    path: Path, sequence: int, semester: str = SEMESTER
) -> None:
    manager = DatabaseManager(db_path=str(path), semester=semester)
    latest_id = manager.get_latest_snapshot_id()
    if latest_id is None:
        raise ValueError("benchmark database has no snapshots")
    snapshot = manager.get_snapshot_data(latest_id)
    if snapshot is None or not snapshot.courses:
        raise ValueError("benchmark database latest snapshot has no course data")
    snapshot.timestamp = f"2099-12-31T23:{sequence // 60:02d}:{sequence % 60:02d}+00:00"
    _change_first_section(snapshot)
    manager.store_enrollment_snapshot(snapshot)


def _row_counts(path: Path) -> dict[str, int]:
    with _database_connection(path) as connection:
        return {
            table: connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            for table in ("snapshots", "courses", "sections", "enrollment_data")
        }


def _poll_delta(
    source: Path, *, changed: bool, semester: str = SEMESTER
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="registrar-poll-delta-") as directory:
        database = Path(directory) / "poll.db"
        shutil.copy2(source, database)
        before_rows = _row_counts(database)
        before_bytes = database.stat().st_size
        manager = DatabaseManager(db_path=str(database), semester=semester)
        latest_id = manager.get_latest_snapshot_id()
        if latest_id is None:
            raise ValueError("benchmark database has no snapshots")
        snapshot = manager.get_snapshot_data(latest_id)
        if snapshot is None:
            raise ValueError("benchmark database latest snapshot is unavailable")
        snapshot.timestamp = (
            "2099-12-30T23:59:59+00:00" if changed else "2099-12-29T23:59:59+00:00"
        )
        if changed:
            _change_first_section(snapshot)
        manager.store_enrollment_snapshot(snapshot)
        after_rows = _row_counts(database)
        return {
            "rows_added": {
                table: after_rows[table] - before_rows[table] for table in before_rows
            },
            "bytes_added": database.stat().st_size - before_bytes,
        }


def benchmark_database(
    source: Path,
    cold_iterations: int,
    warm_iterations: int,
    semester: str = SEMESTER,
) -> dict[str, Any]:
    """Measure representative SQLite reads, validation, and disposable writes."""
    if min(cold_iterations, warm_iterations) <= 0:
        raise ValueError("iteration counts must be positive")
    cold: dict[str, list[int]] = {
        "connection_schema_validation": [],
        "latest_snapshot_read": [],
        "course_history_read": [],
        "integrity_check": [],
        "snapshot_write": [],
    }
    warm = {name: [] for name in cold}
    with tempfile.TemporaryDirectory(prefix="registrar-db-benchmark-") as directory:
        root = Path(directory)
        warm_path = root / "warm.db"
        shutil.copy2(source, warm_path)
        warm_manager = DatabaseManager(db_path=str(warm_path), semester=semester)
        course_code = _latest_course_code(warm_path)
        latest_id = warm_manager.get_latest_snapshot_id()
        if latest_id is None:
            raise ValueError("benchmark database has no snapshots")

        for index in range(cold_iterations):
            sample = root / f"cold-{index}.db"
            shutil.copy2(source, sample)
            cold["connection_schema_validation"].append(
                timed(lambda sample=sample: DatabaseManager(db_path=str(sample)))
            )
            manager = DatabaseManager(db_path=str(sample), semester=semester)
            sample_latest = manager.get_latest_snapshot_id()
            if sample_latest is None:
                raise ValueError("benchmark database has no snapshots")
            cold["latest_snapshot_read"].append(
                timed(
                    lambda manager=manager, sample_latest=sample_latest: (
                        manager.get_snapshot_data(sample_latest)
                    )
                )
            )
            cold["course_history_read"].append(
                timed(
                    lambda manager=manager: manager.get_course_history(
                        course_code, semester
                    )
                )
            )
            cold["integrity_check"].append(
                timed(lambda sample=sample: _integrity_check(sample))
            )
            cold["snapshot_write"].append(
                timed(
                    lambda index=index, sample=sample: _write_changed_snapshot(
                        sample, index, semester
                    )
                )
            )

        # Warm-up establishes connections and Python/SQLite code paths.
        warm_manager.get_snapshot_data(latest_id)
        warm_manager.get_course_history(course_code, semester)
        for index in range(warm_iterations):
            warm["connection_schema_validation"].append(
                timed(
                    lambda: DatabaseManager(db_path=str(warm_path), semester=semester)
                )
            )
            warm["latest_snapshot_read"].append(
                timed(lambda: warm_manager.get_snapshot_data(latest_id))
            )
            warm["course_history_read"].append(
                timed(lambda: warm_manager.get_course_history(course_code, semester))
            )
            warm["integrity_check"].append(timed(lambda: _integrity_check(warm_path)))
            warm["snapshot_write"].append(
                timed(
                    lambda index=index: _write_changed_snapshot(
                        warm_path, cold_iterations + index, semester
                    )
                )
            )
    return {
        "cold": {name: summarize(values) for name, values in cold.items()},
        "warm": {name: summarize(values) for name, values in warm.items()},
        "poll_effects": {
            "unchanged": _poll_delta(source, changed=False, semester=semester),
            "changed": _poll_delta(source, changed=True, semester=semester),
        },
    }


class _TrackingCursor:
    def __init__(self, cursor: sqlite3.Cursor, tracker: list[dict[str, Any]]):
        self._cursor = cursor
        self._tracker = tracker
        self._started = 0
        self._sql = ""
        self._recorded = True

    def execute(self, sql: str, parameters: Any = ()) -> _TrackingCursor:
        self._sql = " ".join(sql.split())
        self._started = time.perf_counter_ns()
        self._recorded = False
        self._cursor.execute(sql, parameters)
        return self

    def _record(self) -> None:
        if not self._recorded:
            self._tracker.append(
                {
                    "sql": self._sql,
                    "duration_ns": time.perf_counter_ns() - self._started,
                }
            )
            self._recorded = True

    def fetchall(self) -> list[Any]:
        result = self._cursor.fetchall()
        self._record()
        return result

    def fetchone(self) -> Any:
        result = self._cursor.fetchone()
        self._record()
        return result

    def __iter__(self) -> Any:
        result = iter(self._cursor.fetchall())
        self._record()
        return result

    def __getattr__(self, name: str) -> Any:
        return getattr(self._cursor, name)


class _TrackingConnection:
    def __init__(self, connection: sqlite3.Connection, tracker: list[dict[str, Any]]):
        self._connection = connection
        self._tracker = tracker

    def cursor(self) -> _TrackingCursor:
        return _TrackingCursor(self._connection.cursor(), self._tracker)

    def execute(self, sql: str, parameters: Any = ()) -> _TrackingCursor:
        return self.cursor().execute(sql, parameters)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._connection, name)


class _BenchmarkDatabaseManager(DatabaseManager):
    benchmark_path: Path
    query_tracker: list[dict[str, Any]] | None = None

    def __init__(self, db_path: str | None = None, semester: str | None = None):
        super().__init__(db_path=str(self.benchmark_path), semester=semester)

    @contextmanager
    def get_connection(self) -> Any:
        with super().get_connection() as connection:
            if self.query_tracker is None:
                yield connection
            else:
                yield _TrackingConnection(connection, self.query_tracker)


def generate_website_once(
    database: Path, output: Path, semester: str = SEMESTER
) -> dict[str, Any]:
    """Generate the production semester output through ``WebsiteService``."""
    import registrarmonitor.website.data as website_data

    output.mkdir(parents=True, exist_ok=True)
    # A benchmark output is also the browser server root. Copy the built
    # production assets before generation so WebsiteService resolves the same
    # hashed JS/CSS manifest used by a real publication. Python-only tests can
    # still exercise this function without a frontend build; in that case the
    # service emits the same data artifacts and simply omits asset references.
    assets_manifest = output / "assets" / ".vite" / "manifest.json"
    if not assets_manifest.exists() and (WEBSITE / "public" / "assets").is_dir():
        _copy_frontend_assets(output)

    _BenchmarkDatabaseManager.benchmark_path = database
    tracker: list[dict[str, Any]] = []
    _BenchmarkDatabaseManager.query_tracker = tracker
    try:
        with patch.object(website_data, "DatabaseManager", _BenchmarkDatabaseManager):
            WebsiteService(
                output_dir=output,
                emit_legacy_semester_json=True,
            ).generate_semester_page(semester)
    finally:
        _BenchmarkDatabaseManager.query_tracker = None

    index_path = output / "index.html"
    index_path.write_text(build_redirect_index(), encoding="utf-8")
    html_path = output / semester_to_filename(semester)
    json_path = output / semester_to_filename(semester).replace(".html", ".json")
    legacy_payload = json.loads(json_path.read_text(encoding="utf-8"))
    data = legacy_payload["data"]

    pointer_path = output / "data" / semester_to_slug(semester) / "manifest.json"
    pointer = json.loads(pointer_path.read_text(encoding="utf-8"))
    manifest_path = pointer_path.parent / pointer["current"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    summary_path = (manifest_path.parent / manifest["summary"]["url"]).resolve()
    summary_bytes = summary_path.stat().st_size
    old_payload_bytes = json_path.stat().st_size
    files = [path for path in output.rglob("*") if path.is_file()]
    slowest = sorted(tracker, key=lambda item: item["duration_ns"], reverse=True)[:10]
    return {
        "html_bytes": html_path.stat().st_size,
        "json_bytes": json_path.stat().st_size,
        "courses": len(data.get("cr", {})),
        "snapshots": len(data.get("sn", [])),
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "query_count": len(tracker),
        "slowest_sql": slowest,
        "old_payload_bytes": old_payload_bytes,
        "new_summary_bytes": summary_bytes,
        "new_summary_to_old_payload_ratio": (
            summary_bytes / old_payload_bytes if old_payload_bytes else None
        ),
    }


def benchmark_website(
    source: Path,
    cold_iterations: int,
    warm_iterations: int,
    final_output: Path,
    semester: str = SEMESTER,
) -> dict[str, Any]:
    cold: list[int] = []
    warm: list[int] = []
    artifacts: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(
        prefix="registrar-website-benchmark-"
    ) as directory:
        root = Path(directory)
        for index in range(cold_iterations):
            database = root / f"cold-{index}.db"
            shutil.copy2(source, database)
            output = root / f"cold-output-{index}"
            cold.append(
                timed(
                    lambda database=database, output=output: generate_website_once(
                        database, output, semester
                    )
                )
            )
        warm_database = root / "warm.db"
        shutil.copy2(source, warm_database)
        warm_output = root / "warm-output"
        generate_website_once(warm_database, warm_output, semester)
        for _ in range(warm_iterations):
            warm.append(
                timed(
                    lambda: generate_website_once(warm_database, warm_output, semester)
                )
            )
        artifacts = generate_website_once(warm_database, final_output, semester)
    return {
        "cold": {"semester_generation": summarize(cold)},
        "warm": {"semester_generation": summarize(warm)},
        "artifacts": artifacts,
    }


def _copy_frontend_assets(output: Path) -> None:
    assets = WEBSITE / "public" / "assets"
    if not assets.is_dir():
        raise FileNotFoundError("frontend assets are missing; run make website-build")
    shutil.copytree(assets, output / "assets", dirs_exist_ok=True)


def file_inventory(output: Path) -> dict[str, Any]:
    files = [path for path in output.rglob("*") if path.is_file()]
    return {
        "file_count": len(files),
        "total_bytes": sum(path.stat().st_size for path in files),
        "largest_files": [
            {"path": str(path.relative_to(output)), "bytes": path.stat().st_size}
            for path in sorted(
                files, key=lambda item: item.stat().st_size, reverse=True
            )[:10]
        ],
    }


def benchmark_deployment(
    output: Path,
    *,
    project: str,
    branch: str,
) -> dict[str, Any]:
    """Create one explicitly requested Cloudflare Pages preview deployment."""
    inventory = file_inventory(output)
    load_dotenv(ROOT / ".env")
    environment = os.environ.copy()
    environment.update(
        {
            "CLOUDFLARE_TELEMETRY_DISABLED": "1",
            "NO_UPDATE_NOTIFIER": "1",
            "WRANGLER_LOG_PATH": str(ROOT / "output" / "wrangler-logs"),
        }
    )
    start = time.perf_counter_ns()
    completed = subprocess.run(
        [
            str(WEBSITE / "node_modules" / ".bin" / "wrangler"),
            "pages",
            "deploy",
            str(output),
            "--project-name",
            project,
            "--branch",
            branch,
            "--commit-dirty=true",
        ],
        cwd=WEBSITE,
        env=environment,
        check=False,
        capture_output=True,
        text=True,
    )
    duration = time.perf_counter_ns() - start
    if completed.returncode:
        message = (completed.stderr or completed.stdout).strip().splitlines()
        detail = message[-1] if message else f"exit code {completed.returncode}"
        raise RuntimeError(f"Cloudflare Pages preview deployment failed: {detail}")
    return {
        "status": "deployed",
        "preview_branch": branch,
        "duration": summarize([duration]),
        "uploaded_bytes": inventory["total_bytes"],
        "uploaded_files": inventory["file_count"],
    }


class _QuietHTTPRequestHandler(SimpleHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        """Keep benchmark output machine-readable."""


def benchmark_browser(
    output: Path, cold_iterations: int, warm_iterations: int
) -> dict[str, Any]:
    _copy_frontend_assets(output)
    handler = partial(_QuietHTTPRequestHandler, directory=str(output))
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server_thread = Thread(
        target=server.serve_forever,
        name="registrar-benchmark-http",
        daemon=True,
    )
    server_thread.start()
    port = server.server_address[1]
    try:
        completed = subprocess.run(
            [
                "node",
                "test/benchmark-browser.mjs",
                f"http://127.0.0.1:{port}/summer2026.html",
                str(cold_iterations),
                str(warm_iterations),
            ],
            cwd=WEBSITE,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode:
            raise RuntimeError(
                "browser benchmark failed:\n"
                + (completed.stderr or completed.stdout).strip()
            )
        result = json.loads(completed.stdout)
        result["served_files"] = file_inventory(output)
        return result
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=5)


def command_version(command: list[str]) -> str | None:
    try:
        result = subprocess.run(
            command, check=True, capture_output=True, text=True, timeout=15
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return None
    return (result.stdout or result.stderr).strip().splitlines()[0]


def platform_metadata(browser_version: str | None = None) -> dict[str, Any]:
    cpu = platform.processor() or platform.machine()
    if platform.system() == "Darwin":
        cpu = command_version(["sysctl", "-n", "machdep.cpu.brand_string"]) or cpu
        memory_text = command_version(["sysctl", "-n", "hw.memsize"])
        memory = int(memory_text) if memory_text and memory_text.isdigit() else None
    else:
        memory = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    return {
        "os": platform.platform(),
        "architecture": platform.machine(),
        "cpu": cpu,
        "memory_bytes": memory,
        "tools": {
            "python": platform.python_version(),
            "sqlite": sqlite3.sqlite_version,
            "uv": command_version(["uv", "--version"]),
            "node": command_version(["node", "--version"]),
            "npm": command_version(["npm", "--version"]),
            "vite": command_version(
                [str(WEBSITE / "node_modules" / ".bin" / "vite"), "--version"]
            ),
            "playwright": command_version(
                [str(WEBSITE / "node_modules" / ".bin" / "playwright"), "--version"]
            ),
            "chromium": browser_version,
        },
    }


def render_markdown(result: dict[str, Any]) -> str:
    def milliseconds(value: float) -> str:
        return f"{value / 1_000_000:.2f}"

    lines = [
        "# Performance baseline — 2026-07-29",
        "",
        (
            "This is an observational baseline. It does not set performance thresholds "
            "or recommend architecture changes."
        ),
        "",
        "## Workload",
        "",
        f"- Source: `{result['source']['classification']}` Summer 2026 SQLite copy",
        f"- Database bytes: {result['database']['bytes']:,}",
        f"- SHA-256: `{result['database']['sha256']}`",
        (
            f"- Counts: {result['database']['counts']['snapshots']:,} snapshots, "
            f"{result['database']['counts']['courses']:,} courses, "
            f"{result['database']['counts']['sections']:,} sections, "
            f"{result['database']['counts']['enrollment_data']:,} enrollment rows"
        ),
        (
            f"- Iterations: {result['parameters']['cold_iterations']} cold, "
            f"{result['parameters']['warm_iterations']} warm"
        ),
        "",
        (
            "The copied database is ignored, is not committed, and is not included in CI "
            "artifacts. No row contents are recorded in either result file."
        ),
        "",
        "## Results",
        "",
        "| Subsystem | State | Measurement | Median (ms) | p95 (ms) |",
        "|---|---|---|---:|---:|",
    ]
    for subsystem in ("database", "website", "browser"):
        measurements = result["measurements"].get(subsystem, {})
        for state in ("cold", "warm"):
            for name, summary in measurements.get(state, {}).items():
                if summary["unit"] != "ns":
                    continue
                lines.append(
                    f"| {subsystem} | {state} | {name.replace('_', ' ')} | "
                    f"{milliseconds(summary['median'])} | "
                    f"{milliseconds(summary['p95'])} |"
                )
    deployment = result["measurements"].get("deployment")
    if deployment and deployment.get("duration"):
        lines.append(
            "| deployment | preview | pages upload | "
            f"{milliseconds(deployment['duration']['median'])} | "
            f"{milliseconds(deployment['duration']['p95'])} |"
        )
    browser = result["measurements"].get("browser", {})
    cold_browser = browser.get("cold", {})
    warm_browser = browser.get("warm", {})
    cold_transfer = cold_browser.get("initial_transfer_bytes") or cold_browser.get(
        "transferred_resources"
    )
    warm_transfer = warm_browser.get("initial_transfer_bytes") or warm_browser.get(
        "transferred_resources"
    )
    initial_requests = warm_browser.get("initial_request_count")
    initial_json_requests = warm_browser.get("initial_json_request_count")
    summary_bytes = warm_browser.get("summary_bytes")
    grid_render = warm_browser.get("grid_render_time")
    navigation_to_grid = warm_browser.get("navigation_to_grid_ready")
    course_bytes = warm_browser.get("course_open_bytes")
    course_data_bytes = warm_browser.get("course_open_data_bytes")
    course_requests = warm_browser.get("course_open_request_count")
    website = result["measurements"].get("website", {})
    artifacts = website.get("artifacts", {})
    old_payload_bytes = artifacts.get("old_payload_bytes")
    new_summary_bytes = artifacts.get("new_summary_bytes")
    payload_ratio = artifacts.get("new_summary_to_old_payload_ratio")
    payload_ratio_text = (
        f"{payload_ratio:.4f}"
        if isinstance(payload_ratio, (int, float))
        else "not measured"
    )
    database_measurements = result["measurements"].get("database", {})
    poll_effects = database_measurements.get("poll_effects", {})
    tools = result["platform"]["tools"]
    lines.extend(
        [
            "",
            (
                "Browser request, byte, and mark-derived render values are recorded in "
                "the JSON result."
            ),
            (
                f"- Initial browser transfer median: "
                f"{cold_transfer['median'] if cold_transfer else 'not measured'} bytes "
                f"cold; {warm_transfer['median'] if warm_transfer else 'not measured'} "
                "bytes warm"
            ),
            (
                f"- Initial request count: "
                f"{initial_requests['median'] if initial_requests else 'not measured'} "
                f"({initial_json_requests['median'] if initial_json_requests else 'not measured'} JSON) warm median"
            ),
            (
                f"- Summary bytes: "
                f"{summary_bytes['median'] if summary_bytes else 'not measured'} warm median"
            ),
            (
                f"- Grid render time: "
                f"{milliseconds(grid_render['median']) if grid_render else 'not measured'} ms "
                "from summary-ready to the second animation frame"
            ),
            (
                f"- Navigation to grid ready: "
                f"{milliseconds(navigation_to_grid['median']) if navigation_to_grid else 'not measured'} ms warm median"
            ),
            (
                f"- Additional bytes to open one course: "
                f"{course_bytes['median'] if course_bytes else 'not measured'} "
                "bytes warm median"
            ),
            (
                f"- Course detail JSON bytes: "
                f"{course_data_bytes['median'] if course_data_bytes else 'not measured'} "
                "bytes warm median"
            ),
            (
                f"- Course-open request count: "
                f"{course_requests['median'] if course_requests else 'not measured'} "
                "requests warm median"
            ),
            "",
            "### Database allocation",
            "",
            "| SQLite object | Kind | Bytes | Pages |",
            "|---|---|---:|---:|",
            *[
                f"| {item['name']} | {item['kind']} | {item['bytes']:,} | "
                f"{item['pages']:,} |"
                for item in result["database"]["allocation"]["objects"]
            ],
            "",
            (
                f"Tables use {result['database']['allocation']['table_bytes']:,} "
                f"bytes; indexes use "
                f"{result['database']['allocation']['index_bytes']:,} bytes."
            ),
            "",
            "### Poll storage effects",
            "",
            "| Poll | Database bytes added | Snapshot rows | Enrollment rows |",
            "|---|---:|---:|---:|",
            *[
                f"| {name} | {effect['bytes_added']:,} | "
                f"{effect['rows_added']['snapshots']:,} | "
                f"{effect['rows_added']['enrollment_data']:,} |"
                for name, effect in poll_effects.items()
            ],
            "",
            "### Website generation",
            "",
            f"- SQL operations: {artifacts.get('query_count', 'not measured')}",
            f"- Generated files: {artifacts.get('file_count', 'not measured')}",
            f"- Generated bytes: {artifacts.get('total_bytes', 'not measured')}",
            f"- Old root payload bytes: {old_payload_bytes if old_payload_bytes is not None else 'not measured'}",
            f"- New summary bytes: {new_summary_bytes if new_summary_bytes is not None else 'not measured'}",
            f"- New summary / old payload ratio: {payload_ratio_text}",
            "- Slowest SQL operations:",
            *[
                f"  - {item['duration_ns'] / 1_000_000:.2f} ms — `{item['sql']}`"
                for item in artifacts.get("slowest_sql", [])[:5]
            ],
            "",
            "### Deployment payload",
            "",
            (
                f"- Status: "
                f"`{deployment.get('status', 'not measured') if deployment else 'not measured'}`"
            ),
            (
                f"- Files: "
                f"{deployment.get('uploaded_files', 'not measured') if deployment else 'not measured'}"
            ),
            (
                f"- Bytes: "
                f"{deployment.get('uploaded_bytes', 'not measured') if deployment else 'not measured'}"
            ),
            (
                f"- Duration: {deployment['duration']['median'] / 1_000_000:.2f} ms"
                if deployment and deployment.get("duration")
                else "- Duration: not measured; Wrangler authentication was unavailable"
            ),
            "",
            "## Environment",
            "",
            f"- OS: {result['platform']['os']}",
            f"- Architecture: {result['platform']['architecture']}",
            f"- CPU: {result['platform']['cpu']}",
            f"- Memory: {result['platform']['memory_bytes']} bytes",
            f"- Python {tools['python']}; SQLite {tools['sqlite']}; {tools['uv']}",
            f"- Node {tools['node']}; npm {tools['npm']}; {tools['vite']}",
            f"- {tools['playwright']}; Chromium {tools['chromium']}",
            "",
            "## Reproduction",
            "",
            "```bash",
            "make benchmark DATABASE=output/performance-input/<runtime-copy>.db",
            "make benchmark-record DATABASE=output/performance-input/<runtime-copy>.db",
            "make benchmark-synthetic",
            "```",
            "",
            (
                "Cold database runs use a fresh disposable copy, cold website runs use "
                "fresh database and output paths, and cold browser runs use a new browser "
                "context with an empty cache. Warm runs reuse initialized local state."
            ),
            "",
            "## Limitations",
            "",
            (
                "- The runtime database is a point-in-time copy; Summer 2026 is smaller "
                "than typical fall and spring workloads."
            ),
            (
                "- Fresh processes and browser contexts do not evict operating-system "
                "filesystem caches."
            ),
            (
                "- Background system load and modest sample counts make tail measurements, "
                "especially p95, noisy."
            ),
            (
                "- The browser uses headless Chromium and a local static server, not "
                "end-user devices or Cloudflare Pages."
            ),
            (
                "- The suite does not measure registrar downloads, network latency, VM "
                "performance, or production traffic. Deployment timing, when present, is "
                "a single preview upload and not a production-route measurement."
            ),
            "",
        ]
    )
    return "\n".join(lines)


def repository_revision() -> str | None:
    return command_version(["jj", "log", "-r", "@", "--no-graph", "-T", "commit_id"])


def run(args: argparse.Namespace) -> dict[str, Any]:
    if min(args.cold_iterations, args.warm_iterations) <= 0:
        raise ValueError("iteration counts must be positive")
    semester = getattr(args, "semester", SEMESTER)
    with tempfile.TemporaryDirectory(
        prefix="registrar-performance-input-"
    ) as directory:
        if args.synthetic:
            source = Path(directory) / "synthetic.db"
            create_synthetic_database(source, seed=args.seed, semester=semester)
            classification = "synthetic"
        else:
            if args.database is None:
                raise ValueError("--database is required unless --synthetic is used")
            source = args.database.resolve()
            classification = "runtime_copy"
        before_hash = sha256_file(source)
        metadata = database_metadata(source)
        measurements: dict[str, Any] = {}
        generated_output = Path(directory) / "site"
        if args.mode in {"all", "database"}:
            measurements["database"] = benchmark_database(
                source, args.cold_iterations, args.warm_iterations, semester
            )
        if args.mode in {"all", "website", "browser"}:
            measurements["website"] = benchmark_website(
                source,
                args.cold_iterations,
                args.warm_iterations,
                generated_output,
                semester,
            )
        browser_version = None
        if args.mode in {"all", "browser"}:
            measurements["browser"] = benchmark_browser(
                generated_output, args.cold_iterations, args.warm_iterations
            )
            browser_version = measurements["browser"].pop("browser_version", None)
            served = measurements["browser"]["served_files"]
            measurements["deployment"] = {
                "status": "not_run",
                "uploaded_bytes": served["total_bytes"],
                "uploaded_files": served["file_count"],
                "duration": None,
            }
        if getattr(args, "deploy_preview", False):
            if args.mode not in {"all", "browser"}:
                raise ValueError("--deploy-preview requires --mode all or browser")
            measurements["deployment"] = benchmark_deployment(
                generated_output,
                project=getattr(args, "pages_project", "registrar-monitor"),
                branch=PREVIEW_BRANCH,
            )
        if sha256_file(source) != before_hash:
            raise RuntimeError("benchmark modified its source database")

    result = {
        "format": FORMAT_VERSION,
        "recorded_at": datetime.now(UTC).isoformat(),
        "repository_revision": repository_revision(),
        "source": {"classification": classification, "semester": semester},
        "parameters": {
            "cold_iterations": args.cold_iterations,
            "warm_iterations": args.warm_iterations,
            "seed": args.seed if args.synthetic else None,
            "mode": args.mode,
        },
        "database": metadata,
        "platform": platform_metadata(browser_version),
        "measurements": measurements,
        "definitions": {
            "cold": "fresh disposable database/output or empty browser context",
            "warm": "reused initialized database/output or browser context and cache",
            "p95": "nearest-rank percentile",
        },
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8"
    )
    if args.markdown:
        args.markdown.parent.mkdir(parents=True, exist_ok=True)
        args.markdown.write_text(render_markdown(result), encoding="utf-8")
    return result


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--database", type=Path)
    source.add_argument("--synthetic", action="store_true")
    parser.add_argument(
        "--mode", choices=("all", "database", "website", "browser"), default="all"
    )
    parser.add_argument("--cold-iterations", type=int, default=10)
    parser.add_argument("--warm-iterations", type=int, default=20)
    parser.add_argument("--seed", type=int, default=DEFAULT_SEED)
    parser.add_argument("--semester", default=SEMESTER)
    parser.add_argument(
        "--output", type=Path, default=Path("output/performance-baseline.json")
    )
    parser.add_argument("--markdown", type=Path)
    parser.add_argument("--deploy-preview", action="store_true")
    parser.add_argument("--pages-project", default="registrar-monitor")
    return parser.parse_args(argv)


def main() -> None:
    args = parse_args()
    run(args)
    print(args.output)
    if args.markdown:
        print(args.markdown)


if __name__ == "__main__":
    main()
