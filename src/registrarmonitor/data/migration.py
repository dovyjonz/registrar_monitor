"""Versioned, resumable migration from legacy snapshots to ADR-0001 state."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from ..models import Course, EnrollmentSnapshot, Section
from ..reporting.report_formatter import ReportFormatter
from .checkpointed_state import CheckpointedStateStore
from .excel_reader import ExcelReader
from .snapshot_comparator import SnapshotComparator
from .snapshot_processor import SnapshotProcessor

TARGET_SCHEMA_VERSION = 2
REPORT_FORMAT_VERSION = 1


class MetadataMode(StrEnum):
    """How temporal catalog metadata is sourced during migration."""

    RAW_ENRICHED = "raw-enriched"
    LEGACY_PRESERVING = "legacy-preserving"


class MigrationInterrupted(RuntimeError):
    """Raised by a failure-injection hook at a deterministic boundary."""


class MigrationError(RuntimeError):
    """Raised when a migration precondition or invariant fails."""


PhaseHook = Callable[[str, str], None]


@dataclass(frozen=True)
class MigrationRequest:
    """All operator-controlled inputs for exactly one semester migration."""

    database: Path
    semester: str
    target_version: int
    metadata_mode: MetadataMode
    report_path: Path
    dry_run: bool
    candidate_path: Path | None = None
    backup_dir: Path | None = None
    raw_dir: Path | None = None
    authorized: bool = False

    def validate(self) -> None:
        def paths_alias(first: Path, second: Path) -> bool:
            if first.resolve() == second.resolve():
                return True
            try:
                return first.exists() and second.exists() and first.samefile(second)
            except OSError:
                return False

        if self.target_version != TARGET_SCHEMA_VERSION:
            raise MigrationError(
                f"unsupported target schema version {self.target_version}"
            )
        if not self.database.is_file():
            raise MigrationError(f"database does not exist: {self.database}")
        if not self.dry_run and not self.authorized:
            raise MigrationError(
                "migration apply requires explicit operator authorization"
            )
        if self.dry_run and self.candidate_path is None:
            raise MigrationError("dry run requires an explicit candidate path")
        if not self.dry_run and self.candidate_path is not None:
            raise MigrationError("apply mode does not accept a candidate path")
        if self.candidate_path is not None and paths_alias(
            self.candidate_path, self.database
        ):
            raise ValueError("candidate path must differ from the source database")
        migration_databases = [self.database]
        if self.candidate_path is not None:
            migration_databases.append(self.candidate_path)
        if any(paths_alias(self.report_path, path) for path in migration_databases):
            raise ValueError("report path must differ from migration database paths")
        if self.metadata_mode is MetadataMode.RAW_ENRICHED:
            if self.raw_dir is None or not self.raw_dir.is_dir():
                raise MigrationError("raw-enriched mode requires --raw-dir")
        elif self.raw_dir is not None:
            raise MigrationError("legacy-preserving mode does not accept --raw-dir")


@dataclass(frozen=True)
class MigrationResult:
    """Observable result returned to the CLI and tests."""

    status: str
    database: Path
    candidate_path: Path | None
    backup_path: Path | None
    backup_verified: bool
    report_path: Path
    source_hash_before: str
    source_hash_after: str


@dataclass(frozen=True)
class ModeTransitionResult:
    """Audited storage-mode transition result."""

    status: str
    previous_mode: str
    active_mode: str
    report_path: Path


@dataclass(frozen=True)
class FinalizationResult:
    """Result of compacting a v2 database and retiring compatibility tables."""

    status: str
    database: Path
    archive_path: Path
    report_path: Path
    source_hash_before: str
    source_hash_after: str


@dataclass(frozen=True)
class LegacySnapshot:
    """One legacy compatibility identity and its canonical state."""

    snapshot_id: int
    snapshot: EnrollmentSnapshot
    last_seen_at: str


class LegacyReader:
    """Read legacy tables without initializing or mutating them."""

    def __init__(self, path: Path, *, immutable: bool):
        self.path = path.resolve()
        self.immutable = immutable

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        suffix = "?mode=ro&immutable=1" if self.immutable else "?mode=ro"
        connection = sqlite3.connect(f"{self.path.as_uri()}{suffix}", uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def user_version(self) -> int:
        with self.connection() as connection:
            return int(connection.execute("PRAGMA user_version").fetchone()[0])

    def semesters(self) -> list[str]:
        with self.connection() as connection:
            return [
                str(row[0])
                for row in connection.execute(
                    "SELECT DISTINCT semester FROM snapshots ORDER BY semester"
                )
            ]

    def catalogs(self) -> tuple[list[sqlite3.Row], list[sqlite3.Row]]:
        with self.connection() as connection:
            courses = connection.execute(
                """
                SELECT course_id, course_code, course_title, department
                FROM courses ORDER BY course_id
                """
            ).fetchall()
            sections = connection.execute(
                """
                SELECT section_id, course_id, section_code, section_type, instructor
                FROM sections ORDER BY section_id
                """
            ).fetchall()
        return courses, sections

    def reporting_rows(self) -> list[tuple[int, int, str, int, str]]:
        with self.connection() as connection:
            return [
                (
                    int(row["report_id"]),
                    int(row["reported_snapshot_id"]),
                    str(row["report_timestamp"]),
                    int(row["changes_found"]),
                    str(row["created_at"]),
                )
                for row in connection.execute(
                    """
                    SELECT report_id, reported_snapshot_id, report_timestamp,
                           changes_found, created_at
                    FROM reporting_log ORDER BY report_id
                    """
                )
            ]

    def snapshots(self) -> tuple[list[LegacySnapshot], dict[str, Any]]:
        courses, sections = self.catalogs()
        course_by_id = {
            int(row["course_id"]): (
                str(row["course_code"]),
                str(row["course_title"] or "").strip(),
                str(row["department"] or ""),
            )
            for row in courses
        }
        section_by_id = {
            int(row["section_id"]): (
                int(row["course_id"]),
                str(row["section_code"]),
                str(row["section_type"] or ""),
                str(row["instructor"] or ""),
            )
            for row in sections
        }
        with self.connection() as connection:
            columns = {
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(snapshots)")
            }
            freshness = (
                "COALESCE(last_seen_at, timestamp)"
                if "last_seen_at" in columns
                else "timestamp"
            )
            metadata = connection.execute(
                f"""
                SELECT snapshot_id, timestamp, semester, overall_fill,
                       {freshness} AS last_seen_at
                FROM snapshots ORDER BY timestamp
                """
            ).fetchall()
            enrollment_by_snapshot: dict[int, list[sqlite3.Row]] = defaultdict(list)
            for row in connection.execute(
                """
                SELECT snapshot_id, section_id, enrollment_count,
                       capacity_count, fill_percentage
                FROM enrollment_data ORDER BY snapshot_id, section_id
                """
            ):
                enrollment_by_snapshot[int(row["snapshot_id"])].append(row)

        result: list[LegacySnapshot] = []
        later_freshness = 0
        for row in metadata:
            observed_at = str(row["timestamp"])
            last_seen_at = str(row["last_seen_at"])
            if last_seen_at < observed_at:
                raise MigrationError(
                    "legacy last_seen_at precedes observed timestamp for "
                    f"snapshot {row['snapshot_id']}"
                )
            later_freshness += last_seen_at != observed_at
            snapshot = EnrollmentSnapshot(
                timestamp=observed_at,
                semester=str(row["semester"]),
                overall_fill=float(row["overall_fill"]),
            )
            for enrollment in enrollment_by_snapshot[int(row["snapshot_id"])]:
                section_id = int(enrollment["section_id"])
                course_id, section_code, section_type, instructor = section_by_id[
                    section_id
                ]
                course_code, title, department = course_by_id[course_id]
                course = snapshot.courses.setdefault(
                    course_code,
                    Course(
                        course_code=course_code,
                        department=department,
                        course_title=title or None,
                    ),
                )
                course.sections[section_code] = Section(
                    section_id=section_code,
                    section_type=section_type,
                    enrollment=int(enrollment["enrollment_count"]),
                    capacity=int(enrollment["capacity_count"]),
                    fill=float(enrollment["fill_percentage"]),
                    instructor=instructor,
                )
            for course in snapshot.courses.values():
                course.average_fill = sum(
                    section.fill for section in course.sections.values()
                ) / len(course.sections)
            result.append(
                LegacySnapshot(
                    snapshot_id=int(row["snapshot_id"]),
                    snapshot=snapshot,
                    last_seen_at=last_seen_at,
                )
            )
        return result, {
            "source_column_present": "last_seen_at" in columns,
            "values_later_than_observed_at": later_freshness,
            "fallback_count": sum(
                item.last_seen_at == item.snapshot.timestamp for item in result
            ),
        }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _legacy_fingerprint(path: Path) -> str:
    digest = hashlib.sha256()
    with sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True) as connection:
        connection.row_factory = sqlite3.Row
        for table, order in (
            ("courses", "course_id"),
            ("sections", "section_id"),
            ("snapshots", "timestamp, snapshot_id"),
            ("enrollment_data", "snapshot_id, section_id"),
            ("reporting_log", "report_id"),
            ("instructor_changes", "change_id"),
        ):
            exists = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            digest.update(table.encode())
            for row in connection.execute(f"SELECT * FROM {table} ORDER BY {order}"):
                digest.update(
                    json.dumps(
                        list(row),
                        ensure_ascii=False,
                        separators=(",", ":"),
                        default=str,
                    ).encode()
                )
                digest.update(b"\n")
    return digest.hexdigest()


CONTROL_SCHEMA = """
CREATE TABLE IF NOT EXISTS storage_control (
    singleton INTEGER PRIMARY KEY CHECK(singleton = 1),
    schema_version INTEGER NOT NULL,
    semester TEXT NOT NULL,
    metadata_mode TEXT NOT NULL
        CHECK(metadata_mode IN ('raw-enriched', 'legacy-preserving')),
    active_mode TEXT NOT NULL
        CHECK(active_mode IN ('legacy', 'shadow', 'v2', 'finalized')),
    migration_phase TEXT NOT NULL,
    application_revision TEXT NOT NULL,
    legacy_fingerprint TEXT NOT NULL,
    legacy_tables_retained INTEGER NOT NULL
        CHECK(legacy_tables_retained IN (0, 1)),
    backup_path TEXT,
    backup_sha256 TEXT,
    last_parity_check_at TEXT,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS migration_phase (
    target_version INTEGER NOT NULL,
    phase TEXT NOT NULL,
    state_digest TEXT NOT NULL,
    completed_at TEXT NOT NULL,
    PRIMARY KEY(target_version, phase)
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _revision() -> str:
    return os.environ.get("REGISTRARMONITOR_REVISION", "development")


def _phase(
    connection: sqlite3.Connection,
    phase: str,
    digest: str,
) -> None:
    connection.execute(
        """
        INSERT INTO migration_phase(
            target_version, phase, state_digest, completed_at
        ) VALUES (?, ?, ?, ?)
        ON CONFLICT(target_version, phase) DO UPDATE SET
            state_digest = excluded.state_digest,
            completed_at = excluded.completed_at
        """,
        (TARGET_SCHEMA_VERSION, phase, digest, _now()),
    )
    connection.execute(
        """
        UPDATE storage_control
        SET migration_phase = ?, updated_at = ?
        WHERE singleton = 1
        """,
        (phase, _now()),
    )


def _phase_exists(path: Path, phase: str) -> bool:
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'migration_phase'"
        ).fetchone()
        if table is None:
            return False
        return (
            connection.execute(
                """
                SELECT 1 FROM migration_phase
                WHERE target_version = ? AND phase = ?
                """,
                (TARGET_SCHEMA_VERSION, phase),
            ).fetchone()
            is not None
        )


def _completed_fingerprint(path: Path) -> str | None:
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'storage_control'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            """
            SELECT legacy_fingerprint FROM storage_control
            WHERE singleton = 1 AND migration_phase = 'complete'
            """
        ).fetchone()
        return str(row[0]) if row else None


def _stored_fingerprint(path: Path) -> str | None:
    if not path.exists():
        return None
    with sqlite3.connect(path) as connection:
        table = connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type = 'table' "
            "AND name = 'storage_control'"
        ).fetchone()
        if table is None:
            return None
        row = connection.execute(
            "SELECT legacy_fingerprint FROM storage_control WHERE singleton = 1"
        ).fetchone()
        return str(row[0]) if row else None


def _backup_database(
    source: Path,
    backup_dir: Path,
    semester: str,
) -> tuple[Path, bool, dict[str, Any]]:
    def critical_state(connection: sqlite3.Connection) -> dict[str, Any]:
        counts = {
            table: int(
                connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
            )
            for table in (
                "courses",
                "sections",
                "snapshots",
                "enrollment_data",
                "reporting_log",
            )
        }
        latest = connection.execute(
            "SELECT snapshot_id, timestamp, semester, overall_fill "
            "FROM snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()
        newest_rows = connection.execute(
            """
            SELECT c.course_code, s.section_code, ed.enrollment_count,
                   ed.capacity_count, ed.fill_percentage
            FROM enrollment_data ed
            JOIN sections s ON s.section_id = ed.section_id
            JOIN courses c ON c.course_id = s.course_id
            WHERE ed.snapshot_id = (
                SELECT snapshot_id FROM snapshots
                ORDER BY timestamp DESC LIMIT 1
            )
            ORDER BY c.course_code, s.section_code
            """
        ).fetchall()
        newest_digest = hashlib.sha256(
            json.dumps(
                [tuple(row) for row in newest_rows],
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        return {
            "counts": counts,
            "newest_snapshot": tuple(latest) if latest else None,
            "newest_state_sha256": newest_digest,
        }

    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    slug = semester.lower().replace(" ", "-")
    backup = backup_dir / f"{slug}-v1-{stamp}.db"
    restored = backup_dir / f"{slug}-v1-{stamp}.restore-check.db"
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as source_db:
        source_state = critical_state(source_db)
        with sqlite3.connect(backup) as target:
            source_db.backup(target)
    with sqlite3.connect(backup) as backup_db:
        with sqlite3.connect(restored) as restored_db:
            backup_db.backup(restored_db)
    with sqlite3.connect(restored) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
        restored_state = critical_state(connection)
    verified = (
        integrity == "ok" and foreign_keys == 0 and restored_state == source_state
    )
    if not verified:
        raise MigrationError("automatic backup restoration verification failed")
    return (
        backup,
        True,
        {
            "path": str(backup),
            "sha256": sha256_file(backup),
            "restore_check_path": str(restored),
            "restore_check_sha256": sha256_file(restored),
            "integrity_check": integrity,
            "foreign_key_violations": foreign_keys,
            "critical_state": restored_state,
            "source_state_matches": restored_state == source_state,
        },
    )


def _install_schema(
    target: Path,
    request: MigrationRequest,
    legacy_fingerprint: str,
    backup: dict[str, Any] | None,
    hook: PhaseHook | None,
) -> None:
    if _phase_exists(target, "schema"):
        stored = _stored_fingerprint(target)
        if stored != legacy_fingerprint:
            raise MigrationError("schema marker disagrees with legacy fingerprint")
        return
    if hook:
        hook("schema", "before_commit")
    CheckpointedStateStore(target, set_user_version=False)
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executescript(CONTROL_SCHEMA)
        connection.execute(
            """
            INSERT OR IGNORE INTO storage_control(
                singleton, schema_version, semester, metadata_mode, active_mode,
                migration_phase, application_revision, legacy_fingerprint,
                legacy_tables_retained, backup_path, backup_sha256, updated_at
            ) VALUES (1, ?, ?, ?, 'legacy', 'schema', ?, ?, 1, ?, ?, ?)
            """,
            (
                TARGET_SCHEMA_VERSION,
                request.semester,
                request.metadata_mode.value,
                _revision(),
                legacy_fingerprint,
                backup["path"] if backup else None,
                backup["sha256"] if backup else None,
                _now(),
            ),
        )
        _phase(connection, "schema", legacy_fingerprint)
        connection.commit()
    if hook:
        hook("schema", "after_commit")


def _backfill_catalogs(
    target: Path,
    courses: list[sqlite3.Row],
    sections: list[sqlite3.Row],
    hook: PhaseHook | None,
) -> None:
    if _phase_exists(target, "catalog"):
        with sqlite3.connect(target) as connection:
            actual_courses = connection.execute(
                "SELECT course_id, course_code FROM course_catalog ORDER BY course_id"
            ).fetchall()
            actual_sections = connection.execute(
                "SELECT section_id, course_id, section_code "
                "FROM section_catalog ORDER BY section_id"
            ).fetchall()
        expected_courses = sorted(
            (int(row["course_id"]), str(row["course_code"])) for row in courses
        )
        expected_sections = sorted(
            (
                int(row["section_id"]),
                int(row["course_id"]),
                str(row["section_code"]),
            )
            for row in sections
        )
        if actual_courses != expected_courses or actual_sections != expected_sections:
            raise MigrationError("catalog marker disagrees with catalog data")
        return
    if hook:
        hook("catalog", "before_commit")
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO course_catalog(course_id, course_code)
            VALUES (?, ?) ON CONFLICT(course_id) DO NOTHING
            """,
            [(int(row["course_id"]), str(row["course_code"])) for row in courses],
        )
        connection.executemany(
            """
            INSERT INTO section_catalog(section_id, course_id, section_code)
            VALUES (?, ?, ?) ON CONFLICT(section_id) DO NOTHING
            """,
            [
                (
                    int(row["section_id"]),
                    int(row["course_id"]),
                    str(row["section_code"]),
                )
                for row in sections
            ],
        )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "courses": len(courses),
                    "sections": len(sections),
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        _phase(connection, "catalog", digest)
        connection.commit()
    if hook:
        hook("catalog", "after_commit")


def _backfill_snapshots(
    target: Path,
    snapshots: list[LegacySnapshot],
    hook: PhaseHook | None,
) -> None:
    store = CheckpointedStateStore(target, initialize=False)
    with sqlite3.connect(target) as connection:
        completed_snapshot_phases = {
            str(row[0])
            for row in connection.execute(
                "SELECT phase FROM migration_phase WHERE phase LIKE 'snapshots:%'"
            )
        }
        sequence_by_snapshot = {
            int(row[0]): int(row[1])
            for row in connection.execute(
                "SELECT snapshot_id, sequence_no FROM state_snapshot"
            )
        }
    reconstructed_by_snapshot = (
        dict(store.iter_reconstructed_snapshots()) if completed_snapshot_phases else {}
    )
    for ordinal, item in enumerate(snapshots, start=1):
        phase = f"snapshots:{ordinal}"
        if phase in completed_snapshot_phases:
            actual = reconstructed_by_snapshot.get(item.snapshot_id)
            if actual is None or actual.to_dict() != item.snapshot.to_dict():
                raise MigrationError(
                    f"{phase} marker disagrees with reconstructed state"
                )
            if sequence_by_snapshot.get(item.snapshot_id) != ordinal:
                raise MigrationError(f"{phase} marker disagrees with sequence")
            continue

        def before_commit(
            connection: sqlite3.Connection,
            snapshot_id: int,
            *,
            current_phase: str = phase,
        ) -> None:
            if hook:
                hook(current_phase, "before_commit")
            _phase(connection, current_phase, str(snapshot_id))

        store.write_snapshot(
            item.snapshot,
            snapshot_id=item.snapshot_id,
            last_seen_at=item.last_seen_at,
            force_checkpoint=ordinal == len(snapshots),
            preserve_duplicate_import=True,
            before_commit=before_commit,
        )
        if hook:
            hook(phase, "after_commit")


def _backfill_reporting(
    target: Path,
    rows: list[tuple[int, int, str, int, str]],
    hook: PhaseHook | None,
) -> None:
    if _phase_exists(target, "reporting"):
        with sqlite3.connect(target) as connection:
            actual = connection.execute(
                """
                SELECT report_id, reported_snapshot_id, report_timestamp,
                       changes_found, created_at
                FROM reporting_log_v2 ORDER BY report_id
                """
            ).fetchall()
        if [tuple(row) for row in actual] != sorted(rows):
            raise MigrationError("reporting marker disagrees with reporting data")
        return
    if hook:
        hook("reporting", "before_commit")
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO reporting_log_v2(
                report_id, reported_snapshot_id, report_timestamp,
                changes_found, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO NOTHING
            """,
            rows,
        )
        _phase(connection, "reporting", str(len(rows)))
        connection.commit()
    if hook:
        hook("reporting", "after_commit")


def _verify(
    target: Path,
    snapshots: list[LegacySnapshot],
    reporting_rows: list[tuple[int, int, str, int, str]],
) -> dict[str, Any]:
    store = CheckpointedStateStore(target, initialize=False)
    mismatches: list[int] = []
    reconstructed: list[EnrollmentSnapshot] = []
    for item in snapshots:
        replayed = store.reconstruct_snapshot(item.snapshot_id)
        reconstructed.append(replayed)
        if replayed.to_dict() != item.snapshot.to_dict():
            mismatches.append(item.snapshot_id)

    comparator = SnapshotComparator()
    formatter = ReportFormatter()
    adjacent_diff_mismatches: list[int] = []
    adjacent_diff_details: list[dict[str, Any]] = []
    formatted_report_mismatches: list[int] = []

    def comparison_shape(comparison: Any) -> dict[str, Any]:
        return {
            "new_courses": [course.course_code for course in comparison.new_courses],
            "removed_courses": [
                course.course_code for course in comparison.removed_courses
            ],
            "changed_courses": [
                {
                    "course_code": detail.course_code,
                    "previous_average_fill": detail.previous_average_fill,
                    "current_average_fill": detail.current_average_fill,
                    "added_sections": [
                        section.section_id for section in detail.added_sections
                    ],
                    "removed_sections": [
                        section.section_id for section in detail.removed_sections
                    ],
                    "modified_sections": [
                        {
                            "section_id": section.section_id,
                            "previous_fill": section.previous_fill,
                            "current_fill": section.current_fill,
                            "previous_enrollment": section.previous_enrollment,
                            "current_enrollment": section.current_enrollment,
                            "previous_capacity": section.previous_capacity,
                            "current_capacity": section.current_capacity,
                            "previous_instructor": section.previous_instructor,
                            "current_instructor": section.current_instructor,
                        }
                        for section in detail.modified_sections
                    ],
                }
                for detail in comparison.changed_courses
            ],
        }

    for index in range(1, len(snapshots)):
        expected_previous = snapshots[index - 1].snapshot
        expected_current = snapshots[index].snapshot
        actual_previous = reconstructed[index - 1]
        actual_current = reconstructed[index]
        expected_diff = comparator.compare_snapshots(
            expected_current, expected_previous
        )
        actual_diff = comparator.compare_snapshots(actual_current, actual_previous)
        if expected_diff != actual_diff:
            adjacent_diff_mismatches.append(snapshots[index].snapshot_id)
            if len(adjacent_diff_details) < 3:
                adjacent_diff_details.append(
                    {
                        "snapshot_id": snapshots[index].snapshot_id,
                        "expected": comparison_shape(expected_diff),
                        "actual": comparison_shape(actual_diff),
                    }
                )
        expected_report = formatter.format_changes_report(
            expected_diff, expected_current, expected_previous
        )
        actual_report = formatter.format_changes_report(
            actual_diff, actual_current, actual_previous
        )
        if expected_report != actual_report:
            formatted_report_mismatches.append(snapshots[index].snapshot_id)

    course_codes = sorted(
        {code for item in snapshots for code in item.snapshot.courses}
    )
    history_mismatches: list[str] = []
    for course_code in course_codes:
        expected_history: list[dict[str, Any]] = []
        for item in snapshots:
            course = item.snapshot.courses.get(course_code)
            if course is None:
                continue
            for section_code in sorted(course.sections):
                section = course.sections[section_code]
                expected_history.append(
                    {
                        "timestamp": item.snapshot.timestamp,
                        "section_code": section_code,
                        "fill_percentage": section.enrollment / section.capacity,
                        "enrollment_count": section.enrollment,
                        "capacity_count": section.capacity,
                    }
                )
        if store.get_course_history(course_code) != expected_history:
            history_mismatches.append(course_code)

    integrity = store.integrity()
    statistics = store.statistics()
    with sqlite3.connect(target) as connection:
        actual_reporting_rows = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT report_id, reported_snapshot_id, report_timestamp,
                       changes_found, created_at
                FROM reporting_log_v2 ORDER BY report_id
                """
            )
        ]
    semantic_payload = {
        "snapshots": [snapshot.to_dict() for snapshot in reconstructed],
        "reporting_rows": actual_reporting_rows,
    }
    semantic_digest = hashlib.sha256(
        json.dumps(
            semantic_payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    expected_last_reported = (
        max(reporting_rows, key=lambda row: row[2])[1] if reporting_rows else None
    )
    latest_snapshot_id = store.get_latest_snapshot_id()
    result = {
        "semantic_mismatches": len(mismatches),
        "mismatch_snapshot_ids": mismatches[:20],
        "adjacent_diff_mismatches": len(adjacent_diff_mismatches),
        "adjacent_diff_mismatch_snapshot_ids": adjacent_diff_mismatches[:20],
        "adjacent_diff_details": adjacent_diff_details,
        "formatted_report_mismatches": len(formatted_report_mismatches),
        "formatted_report_mismatch_snapshot_ids": formatted_report_mismatches[:20],
        "course_history_mismatches": len(history_mismatches),
        "course_history_mismatch_codes": history_mismatches[:20],
        "reporting_rows_exact": actual_reporting_rows == sorted(reporting_rows),
        "last_reported_snapshot_exact": (
            store.get_last_reported_snapshot_id() == expected_last_reported
        ),
        "integrity_check": integrity["integrity_check"],
        "foreign_key_violations": integrity["foreign_key_violations"],
        "latest_equals_replay": (
            not snapshots
            or (
                latest_snapshot_id is not None
                and store.reconstruct_snapshot(latest_snapshot_id).to_dict()
                == reconstructed[-1].to_dict()
            )
        ),
        "max_replay_distance": store.max_replay_distance(),
        "semantic_sha256": semantic_digest,
        "statistics": {
            **statistics,
            "reporting_rows": len(actual_reporting_rows),
        },
    }
    if (
        result["semantic_mismatches"]
        or result["adjacent_diff_mismatches"]
        or result["formatted_report_mismatches"]
        or result["course_history_mismatches"]
        or not result["reporting_rows_exact"]
        or not result["last_reported_snapshot_exact"]
        or not result["latest_equals_replay"]
        or result["integrity_check"] != "ok"
        or result["foreign_key_violations"]
    ):
        diagnostics = {
            key: result[key]
            for key in (
                "semantic_mismatches",
                "mismatch_snapshot_ids",
                "adjacent_diff_mismatches",
                "adjacent_diff_mismatch_snapshot_ids",
                "adjacent_diff_details",
                "formatted_report_mismatches",
                "formatted_report_mismatch_snapshot_ids",
                "course_history_mismatches",
                "course_history_mismatch_codes",
                "reporting_rows_exact",
                "last_reported_snapshot_exact",
                "latest_equals_replay",
                "integrity_check",
                "foreign_key_violations",
            )
        }
        raise MigrationError(
            "semantic or SQLite verification failed: "
            + json.dumps(diagnostics, sort_keys=True, separators=(",", ":"))
        )
    return result


def _operational_evidence(target: Path) -> dict[str, Any]:
    """Capture post-completion schema, marker, and preserved-ID evidence."""
    with sqlite3.connect(target) as connection:
        connection.row_factory = sqlite3.Row
        tables = {
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        legacy_tables = {
            "courses",
            "sections",
            "snapshots",
            "enrollment_data",
            "reporting_log",
        }
        control = connection.execute(
            "SELECT * FROM storage_control WHERE singleton = 1"
        ).fetchone()
        phases = [
            dict(row)
            for row in connection.execute(
                "SELECT phase, state_digest, completed_at "
                "FROM migration_phase ORDER BY rowid"
            )
        ]

        def ids(table: str, column: str) -> list[int]:
            return [
                int(row[0])
                for row in connection.execute(
                    f"SELECT {column} FROM {table} ORDER BY {column}"
                )
            ]

        id_pairs = {
            "course_ids": (
                ids("courses", "course_id"),
                ids("course_catalog", "course_id"),
            ),
            "section_ids": (
                ids("sections", "section_id"),
                ids("section_catalog", "section_id"),
            ),
            "snapshot_ids": (
                ids("snapshots", "snapshot_id"),
                ids("state_snapshot", "snapshot_id"),
            ),
            "report_ids": (
                ids("reporting_log", "report_id"),
                ids("reporting_log_v2", "report_id"),
            ),
        }
        return {
            "user_version": int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            ),
            "storage_control": dict(control) if control is not None else None,
            "legacy_tables_retained": legacy_tables.issubset(tables),
            "legacy_tables": sorted(legacy_tables & tables),
            "phase_markers": phases,
            "id_preservation": {
                name: {
                    "legacy_count": len(pair[0]),
                    "v2_count": len(pair[1]),
                    "exact": pair[0] == pair[1],
                }
                for name, pair in id_pairs.items()
            },
        }


def _snapshots_match(
    actual: EnrollmentSnapshot,
    expected: EnrollmentSnapshot,
    metadata_mode: str,
) -> bool:
    if metadata_mode == MetadataMode.LEGACY_PRESERVING.value:
        return actual.to_dict() == expected.to_dict()
    expected_state = {
        code: {
            section_code: (section.enrollment, section.capacity)
            for section_code, section in course.sections.items()
        }
        for code, course in expected.courses.items()
    }
    actual_state = {
        code: {
            section_code: (section.enrollment, section.capacity)
            for section_code, section in course.sections.items()
        }
        for code, course in actual.courses.items()
    }
    return (
        actual.timestamp == expected.timestamp
        and actual.semester == expected.semester
        and actual.overall_fill == expected.overall_fill
        and actual_state == expected_state
    )


def _assert_legacy_v2_parity(database: Path, metadata_mode: str) -> None:
    """Verify compatibility identities and state before a mode transition."""
    reader = LegacyReader(database, immutable=False)
    legacy_snapshots, _ = reader.snapshots()
    legacy_snapshot_ids = {item.snapshot_id for item in legacy_snapshots}
    legacy_courses, legacy_sections = reader.catalogs()
    legacy_reporting = reader.reporting_rows()
    store = CheckpointedStateStore(database, initialize=False)

    with sqlite3.connect(database) as connection:
        v2_snapshot_ids = {
            int(row[0])
            for row in connection.execute("SELECT snapshot_id FROM state_snapshot")
        }
        v2_courses = [
            tuple(row)
            for row in connection.execute(
                "SELECT course_id, course_code FROM course_catalog ORDER BY course_id"
            )
        ]
        v2_sections = [
            tuple(row)
            for row in connection.execute(
                "SELECT section_id, course_id, section_code "
                "FROM section_catalog ORDER BY section_id"
            )
        ]
        v2_reporting = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT report_id, reported_snapshot_id, report_timestamp,
                       changes_found
                FROM reporting_log_v2 ORDER BY report_id
                """
            )
        ]

    if v2_snapshot_ids != legacy_snapshot_ids:
        missing = sorted(legacy_snapshot_ids - v2_snapshot_ids)
        extra = sorted(v2_snapshot_ids - legacy_snapshot_ids)
        raise MigrationError(
            "legacy/v2 snapshot identity parity failed; "
            f"missing_in_v2={missing[:20]}, extra_in_v2={extra[:20]}"
        )

    mismatches = [
        item.snapshot_id
        for item in legacy_snapshots
        if not _snapshots_match(
            store.reconstruct_snapshot(item.snapshot_id),
            item.snapshot,
            metadata_mode,
        )
    ]
    if mismatches:
        raise MigrationError(
            "legacy/v2 snapshot parity failed for snapshot IDs "
            + ", ".join(str(value) for value in mismatches[:20])
        )

    expected_courses = sorted(
        (int(row["course_id"]), str(row["course_code"])) for row in legacy_courses
    )
    expected_sections = sorted(
        (
            int(row["section_id"]),
            int(row["course_id"]),
            str(row["section_code"]),
        )
        for row in legacy_sections
    )
    if v2_courses != expected_courses or v2_sections != expected_sections:
        raise MigrationError("legacy/v2 catalog identity parity failed")

    expected_reporting = [row[:4] for row in sorted(legacy_reporting)]
    if v2_reporting != expected_reporting:
        raise MigrationError("legacy/v2 reporting continuity parity failed")


def _reconcile_completed_migration(
    target: Path,
    *,
    snapshots: list[LegacySnapshot],
    reporting_rows: list[tuple[int, int, str, int, str]],
    legacy_fingerprint: str,
    metadata_mode: str,
) -> dict[str, Any]:
    """Append legacy-only writes after completion without changing preserved IDs."""
    store = CheckpointedStateStore(target, initialize=False)
    with sqlite3.connect(target) as connection:
        control = connection.execute(
            "SELECT active_mode FROM storage_control WHERE singleton = 1"
        ).fetchone()
        existing_ids = {
            int(row[0])
            for row in connection.execute("SELECT snapshot_id FROM state_snapshot")
        }
        latest_time = connection.execute(
            "SELECT max(observed_at) FROM state_snapshot"
        ).fetchone()[0]
    if control is None:
        raise MigrationError("completed migration has no storage control row")
    active_mode = str(control[0])

    missing = [item for item in snapshots if item.snapshot_id not in existing_ids]
    for item in snapshots:
        if item.snapshot_id not in existing_ids:
            continue
        actual = store.reconstruct_snapshot(item.snapshot_id)
        if not _snapshots_match(actual, item.snapshot, metadata_mode):
            raise MigrationError(
                f"preserved snapshot {item.snapshot_id} changed after migration"
            )
    if missing and active_mode != "legacy":
        raise MigrationError(
            "shadow/v2 legacy divergence violates atomic dual-write guarantees"
        )
    if any(
        latest_time is not None and item.snapshot.timestamp <= str(latest_time)
        for item in missing
    ):
        raise MigrationError(
            "legacy divergence is not a chronological suffix; automatic "
            "reconciliation is unsafe"
        )

    for item in missing:
        phase = f"reconcile:snapshot:{item.snapshot_id}"

        def marker(
            connection: sqlite3.Connection,
            snapshot_id: int,
            *,
            current_phase: str = phase,
        ) -> None:
            _phase(connection, current_phase, str(snapshot_id))

        store.write_snapshot(
            item.snapshot,
            snapshot_id=item.snapshot_id,
            last_seen_at=item.last_seen_at,
            preserve_duplicate_import=True,
            before_commit=marker,
        )

    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        connection.executemany(
            """
            INSERT INTO reporting_log_v2(
                report_id, reported_snapshot_id, report_timestamp,
                changes_found, created_at
            ) VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(report_id) DO NOTHING
            """,
            reporting_rows,
        )
        digest = hashlib.sha256(
            json.dumps(
                {
                    "legacy_fingerprint": legacy_fingerprint,
                    "snapshots_added": [item.snapshot_id for item in missing],
                },
                sort_keys=True,
            ).encode()
        ).hexdigest()
        _phase(connection, "reconciled", digest)
        connection.execute(
            """
            UPDATE storage_control
            SET legacy_fingerprint = ?, migration_phase = 'complete',
                last_parity_check_at = ?, updated_at = ?
            WHERE singleton = 1
            """,
            (legacy_fingerprint, _now(), _now()),
        )
        connection.commit()
    return {
        "snapshots_added": len(missing),
        "reporting_rows_seen": len(reporting_rows),
    }


def _raw_enrich_snapshots(
    snapshots: list[LegacySnapshot],
    request: MigrationRequest,
    excel_reader: Any,
) -> tuple[list[LegacySnapshot], dict[str, Any]]:
    if request.raw_dir is None:
        raise MigrationError("raw-enriched mode requires a raw directory")
    observations: dict[str, dict[str, Any]] = {}
    conflicts: set[str] = set()
    duplicate_content = 0
    parse_failures: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for path in sorted(request.raw_dir.rglob("*.xls")):
        try:
            semester, observed_at, rows = excel_reader.read_excel_data(str(path))
        except Exception as error:
            parse_failures.append({"file": path.name, "error": type(error).__name__})
            continue
        if semester.strip() != request.semester:
            continue
        digest = hashlib.sha256(
            json.dumps(
                rows,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
                default=str,
            ).encode()
        ).hexdigest()
        identity = (observed_at, digest)
        if identity in seen:
            duplicate_content += 1
            continue
        seen.add(identity)
        existing = observations.get(observed_at)
        if existing is not None and existing["digest"] != digest:
            conflicts.add(observed_at)
            continue
        observations[observed_at] = {
            "path": path,
            "digest": digest,
            "rows": rows,
        }

    expected_timestamps = {item.snapshot.timestamp for item in snapshots}
    missing = sorted(expected_timestamps - observations.keys())
    conflicting = sorted(expected_timestamps & conflicts)
    if missing or conflicting:
        raise MigrationError(
            "raw-enriched coverage is incomplete or ambiguous: "
            f"missing={len(missing)}, conflicting={len(conflicting)}"
        )

    processor = SnapshotProcessor(data_dir=str(request.raw_dir))
    enriched: list[LegacySnapshot] = []
    disagreements: dict[str, int] = defaultdict(int)
    for item in snapshots:
        observation = observations[item.snapshot.timestamp]
        raw = processor.process_data(
            observation["rows"],
            request.semester,
            item.snapshot.timestamp,
        )
        snapshot = EnrollmentSnapshot.from_dict(item.snapshot.to_dict())
        if raw.overall_fill != item.snapshot.overall_fill:
            disagreements["overall_fill"] += 1
        if set(raw.courses) != set(item.snapshot.courses):
            disagreements["course_presence"] += 1
        for course_code, course in snapshot.courses.items():
            raw_course = raw.courses.get(course_code)
            if raw_course is None:
                continue
            course.course_title = raw_course.course_title
            if set(raw_course.sections) != set(course.sections):
                disagreements["section_presence"] += 1
            for section_code, section in course.sections.items():
                raw_section = raw_course.sections.get(section_code)
                if raw_section is None:
                    continue
                if raw_section.enrollment != section.enrollment:
                    disagreements["enrollment_count"] += 1
                if raw_section.capacity != section.capacity:
                    disagreements["capacity_count"] += 1
                section.instructor = raw_section.instructor
        enriched.append(
            LegacySnapshot(
                snapshot_id=item.snapshot_id,
                snapshot=snapshot,
                last_seen_at=item.last_seen_at,
            )
        )
    return enriched, {
        "mode": request.metadata_mode.value,
        "files_scanned": len(list(request.raw_dir.rglob("*.xls"))),
        "matched": len(snapshots),
        "missing": len(missing),
        "conflicting": len(conflicting),
        "duplicate_content": duplicate_content,
        "parse_failure_count": len(parse_failures),
        "parse_failure_samples": parse_failures[:20],
        "raw_observations_without_snapshot": len(
            set(observations) - expected_timestamps
        ),
        "disagreements": dict(sorted(disagreements.items())),
    }


def _mark_complete(
    target: Path,
    verification: dict[str, Any],
    hook: PhaseHook | None,
) -> None:
    if _phase_exists(target, "complete"):
        return
    if hook:
        hook("complete", "before_commit")
    digest = hashlib.sha256(
        json.dumps(verification, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    with sqlite3.connect(target) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        _phase(connection, "verified", digest)
        _phase(connection, "complete", digest)
        connection.execute(
            """
            UPDATE storage_control
            SET migration_phase = 'complete',
                last_parity_check_at = ?,
                updated_at = ?
            WHERE singleton = 1
            """,
            (_now(), _now()),
        )
        connection.execute(f"PRAGMA user_version = {TARGET_SCHEMA_VERSION}")
        connection.commit()
    if hook:
        hook("complete", "after_commit")


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    os.replace(temporary, path)


def _write_report(path: Path, report: dict[str, Any]) -> None:
    _atomic_write(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    markdown = path.with_suffix(".md")
    verification = report["verification"]
    operations = verification.get("operational_evidence", {})
    preserved_ids = operations.get("id_preservation", {})
    body = "\n".join(
        [
            "# Checkpointed-state migration report",
            "",
            f"- Status: `{report['status']}`",
            f"- Semester: `{report['semester']}`",
            f"- Metadata mode: `{report['metadata_mode']}`",
            f"- Source: `{report['source']['path']}`",
            f"- Target: `{report['target']['path']}`",
            f"- Semantic mismatches: `{verification['semantic_mismatches']}`",
            (
                "- Adjacent-diff mismatches: "
                f"`{verification['adjacent_diff_mismatches']}`"
            ),
            (
                "- Formatted-report mismatches: "
                f"`{verification['formatted_report_mismatches']}`"
            ),
            (
                "- Course-history mismatches: "
                f"`{verification['course_history_mismatches']}`"
            ),
            f"- Reporting rows exact: `{verification['reporting_rows_exact']}`",
            (
                "- Last-reported snapshot exact: "
                f"`{verification['last_reported_snapshot_exact']}`"
            ),
            f"- Latest equals replay: `{verification['latest_equals_replay']}`",
            f"- Maximum replay distance: `{verification['max_replay_distance']}`",
            f"- Semantic SHA-256: `{verification['semantic_sha256']}`",
            f"- Integrity check: `{verification['integrity_check']}`",
            (f"- Foreign-key violations: `{verification['foreign_key_violations']}`"),
            f"- Legacy tables retained: `{operations.get('legacy_tables_retained')}`",
            f"- Schema user version: `{operations.get('user_version')}`",
            f"- Migration phase markers: `{len(operations.get('phase_markers', []))}`",
            "- Preserved IDs: "
            + ", ".join(
                f"{name}=`{details.get('exact')}`"
                for name, details in sorted(preserved_ids.items())
            ),
            "",
        ]
    )
    _atomic_write(markdown, body)


def run_migration(
    request: MigrationRequest,
    *,
    phase_hook: PhaseHook | None = None,
    excel_reader: Any | None = None,
) -> MigrationResult:
    """Run or resume one dry-run/apply migration without silent fallback."""
    request.validate()
    source = request.database.resolve()
    source_hash_before = sha256_file(source)
    legacy_fingerprint = _legacy_fingerprint(source)
    completed = _completed_fingerprint(source)
    stored_source_fingerprint = _stored_fingerprint(source)
    if (
        completed is None
        and stored_source_fingerprint is not None
        and stored_source_fingerprint != legacy_fingerprint
    ):
        raise MigrationError(
            "legacy fingerprint changed during an incomplete migration"
        )

    reader = LegacyReader(source, immutable=request.dry_run)
    if reader.user_version() not in (1, TARGET_SCHEMA_VERSION):
        raise MigrationError(
            f"unsupported source schema version {reader.user_version()}"
        )
    semesters = reader.semesters()
    if semesters != [request.semester]:
        raise MigrationError(
            f"source must contain exactly semester {request.semester!r}; "
            f"found {semesters!r}"
        )
    snapshots, freshness = reader.snapshots()
    courses, sections = reader.catalogs()
    reporting_rows = reader.reporting_rows()
    raw_evidence: dict[str, Any]
    if request.metadata_mode is MetadataMode.RAW_ENRICHED:
        snapshots, raw_evidence = _raw_enrich_snapshots(
            snapshots,
            request,
            excel_reader or ExcelReader(),
        )
    else:
        raw_evidence = {
            "mode": request.metadata_mode.value,
            "matched": 0,
            "missing": 0,
            "conflicting": 0,
            "duplicate_content": 0,
            "disagreements": {},
            "historical_metadata_accuracy": "unknown",
        }

    if not request.dry_run and completed is not None:
        reconciliation = None
        status = "already_complete"
        if completed != legacy_fingerprint:
            reconciliation = _reconcile_completed_migration(
                source,
                snapshots=snapshots,
                reporting_rows=reporting_rows,
                legacy_fingerprint=legacy_fingerprint,
                metadata_mode=request.metadata_mode.value,
            )
            status = "reconciled"
        verification = _verify(source, snapshots, reporting_rows)
        verification["operational_evidence"] = _operational_evidence(source)
        report: dict[str, Any] = {
            "format": REPORT_FORMAT_VERSION,
            "status": status,
            "semester": request.semester,
            "metadata_mode": request.metadata_mode.value,
            "target_version": request.target_version,
            "authorization": {
                "required": not request.dry_run,
                "provided": request.authorized,
            },
            "source": {
                "path": str(source),
                "sha256_before": source_hash_before,
                "sha256_after": sha256_file(source),
                "hash_unchanged": status == "already_complete",
                "legacy_fingerprint": legacy_fingerprint,
                "bytes": source.stat().st_size,
                "snapshot_count": len(snapshots),
                "freshness": freshness,
            },
            "target": {
                "path": str(source),
                "sha256": sha256_file(source),
                "bytes": source.stat().st_size,
                "schema_version": TARGET_SCHEMA_VERSION,
            },
            "backup": None,
            "verification": verification,
            "raw_evidence": raw_evidence,
            "reconciliation": reconciliation,
        }
        _write_report(request.report_path, report)
        return MigrationResult(
            status=status,
            database=source,
            candidate_path=None,
            backup_path=None,
            backup_verified=True,
            report_path=request.report_path,
            source_hash_before=source_hash_before,
            source_hash_after=sha256_file(source),
        )

    target = (
        request.candidate_path.resolve()
        if request.dry_run and request.candidate_path is not None
        else source
    )
    if request.dry_run and target.exists():
        try:
            candidate_fingerprint = _legacy_fingerprint(target)
        except sqlite3.Error as error:
            raise MigrationError(f"invalid candidate database: {target}") from error
        if candidate_fingerprint != legacy_fingerprint:
            raise MigrationError(f"candidate already exists: {target}")
        stored = _stored_fingerprint(target)
        if stored is not None and stored != legacy_fingerprint:
            raise MigrationError(
                "candidate migration fingerprint disagrees with source"
            )
    elif request.dry_run:
        target.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(
            f"{source.resolve().as_uri()}?mode=ro",
            uri=True,
        ) as source_connection:
            with sqlite3.connect(target) as candidate_connection:
                source_connection.backup(candidate_connection)

    backup_path: Path | None = None
    backup_verified = request.dry_run
    backup_details: dict[str, Any] | None = None
    if not request.dry_run:
        with sqlite3.connect(source) as connection:
            table = connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' "
                "AND name = 'storage_control'"
            ).fetchone()
            prior = (
                connection.execute(
                    "SELECT backup_path, backup_sha256 FROM storage_control "
                    "WHERE singleton = 1"
                ).fetchone()
                if table
                else None
            )
        if prior and prior[0] and Path(prior[0]).is_file():
            backup_path = Path(prior[0])
            backup_verified = sha256_file(backup_path) == str(prior[1])
            backup_details = {
                "path": str(backup_path),
                "sha256": str(prior[1]),
                "reused": True,
            }
        else:
            backup_dir = request.backup_dir or source.parent / "backups"
            backup_path, backup_verified, backup_details = _backup_database(
                source, backup_dir, request.semester
            )

    _install_schema(
        target,
        request,
        legacy_fingerprint,
        backup_details,
        phase_hook,
    )
    _backfill_catalogs(target, courses, sections, phase_hook)
    _backfill_snapshots(target, snapshots, phase_hook)
    _backfill_reporting(target, reporting_rows, phase_hook)
    verification = _verify(target, snapshots, reporting_rows)
    _mark_complete(target, verification, phase_hook)
    verification["operational_evidence"] = _operational_evidence(target)

    source_hash_after = sha256_file(source)
    status = "verified" if request.dry_run else "applied"
    report = {
        "format": REPORT_FORMAT_VERSION,
        "status": status,
        "semester": request.semester,
        "metadata_mode": request.metadata_mode.value,
        "target_version": request.target_version,
        "authorization": {
            "required": not request.dry_run,
            "provided": request.authorized,
        },
        "application_revision": _revision(),
        "source": {
            "path": str(source),
            "sha256_before": source_hash_before,
            "sha256_after": source_hash_after,
            "hash_unchanged": source_hash_before == source_hash_after,
            "legacy_fingerprint": legacy_fingerprint,
            "bytes": source.stat().st_size,
            "snapshot_count": len(snapshots),
            "freshness": freshness,
        },
        "target": {
            "path": str(target),
            "sha256": sha256_file(target),
            "bytes": target.stat().st_size,
            "schema_version": TARGET_SCHEMA_VERSION,
        },
        "backup": backup_details,
        "verification": verification,
        "raw_evidence": raw_evidence,
    }
    if request.dry_run and not report["source"]["hash_unchanged"]:
        raise MigrationError("dry run changed the source database")
    _write_report(request.report_path, report)
    return MigrationResult(
        status=status,
        database=source,
        candidate_path=target if request.dry_run else None,
        backup_path=backup_path,
        backup_verified=backup_verified,
        report_path=request.report_path,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
    )


def transition_storage_mode(
    database: Path,
    *,
    semester: str,
    target_mode: str,
    report_path: Path,
) -> ModeTransitionResult:
    """Move a completed v2 database through audited compatibility modes."""
    database = database.resolve()
    if target_mode not in {"legacy", "shadow", "v2"}:
        raise MigrationError(f"unsupported storage mode {target_mode!r}")
    if not database.is_file():
        raise MigrationError(f"database does not exist: {database}")
    current_fingerprint = _legacy_fingerprint(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if version != TARGET_SCHEMA_VERSION:
            raise MigrationError(
                f"mode transitions require schema version {TARGET_SCHEMA_VERSION}"
            )
        control = connection.execute(
            "SELECT * FROM storage_control WHERE singleton = 1"
        ).fetchone()
        if control is None or control["migration_phase"] != "complete":
            raise MigrationError("mode transition requires a completed migration")
        if str(control["semester"]) != semester:
            raise MigrationError(
                f"database semester {control['semester']!r} does not match {semester!r}"
            )
        previous_mode = str(control["active_mode"])
        stored_fingerprint = str(control["legacy_fingerprint"])
        metadata_mode = str(control["metadata_mode"])

    if stored_fingerprint != current_fingerprint:
        if previous_mode not in {"shadow", "v2"}:
            raise MigrationError(
                "legacy tables changed; reconcile migration before changing mode"
            )

    if previous_mode in {"shadow", "v2"} or target_mode in {"shadow", "v2"}:
        _assert_legacy_v2_parity(database, metadata_mode)

    if previous_mode == target_mode:
        status = "already_active"
    else:
        allowed = {
            ("legacy", "shadow"),
            ("shadow", "v2"),
            ("shadow", "legacy"),
            ("v2", "legacy"),
        }
        if (previous_mode, target_mode) not in allowed:
            raise MigrationError(
                f"unsupported mode transition {previous_mode!r} -> {target_mode!r}"
            )
        store = CheckpointedStateStore(database, initialize=False)
        integrity = store.integrity()
        if integrity["integrity_check"] != "ok" or integrity["foreign_key_violations"]:
            raise MigrationError("mode transition blocked by SQLite health checks")
        latest_id = store.get_latest_snapshot_id()
        if latest_id is None:
            raise MigrationError("mode transition requires migrated snapshots")
        if target_mode == "v2":
            store.force_checkpoint(latest_id)
        with sqlite3.connect(database) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                """
                UPDATE storage_control
                SET active_mode = ?, legacy_fingerprint = ?,
                    last_parity_check_at = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (target_mode, current_fingerprint, _now(), _now()),
            )
            _phase(connection, f"mode:{target_mode}", current_fingerprint)
            connection.execute(
                "UPDATE storage_control SET migration_phase = 'complete' "
                "WHERE singleton = 1"
            )
            connection.commit()
        status = "changed"

    report = {
        "format": REPORT_FORMAT_VERSION,
        "status": status,
        "database": str(database),
        "semester": semester,
        "schema_version": TARGET_SCHEMA_VERSION,
        "previous_mode": previous_mode,
        "active_mode": target_mode,
        "legacy_fingerprint": current_fingerprint,
        "completed_at": _now(),
    }
    _atomic_write(report_path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _atomic_write(
        report_path.with_suffix(".md"),
        "\n".join(
            [
                "# Storage-mode transition",
                "",
                f"- Semester: `{semester}`",
                f"- Previous mode: `{previous_mode}`",
                f"- Active mode: `{target_mode}`",
                f"- Status: `{status}`",
                "",
            ]
        ),
    )
    return ModeTransitionResult(
        status=status,
        previous_mode=previous_mode,
        active_mode=target_mode,
        report_path=report_path,
    )


def _v2_semantic_digest(database: Path) -> str:
    """Hash all reconstructed v2 snapshots and reporting rows in stable order."""
    store = CheckpointedStateStore(database, initialize=False)
    snapshots = [
        snapshot.to_dict() for _, snapshot in store.iter_reconstructed_snapshots()
    ]
    with sqlite3.connect(database) as connection:
        reporting = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT report_id, reported_snapshot_id, report_timestamp,
                       changes_found, created_at
                FROM reporting_log_v2 ORDER BY report_id
                """
            )
        ]
    return hashlib.sha256(
        json.dumps(
            {"snapshots": snapshots, "reporting": reporting},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _verified_database_copy(source: Path, target: Path) -> dict[str, Any]:
    """Create a SQLite-consistent copy and return bounded restore evidence."""
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        raise MigrationError(f"refusing to overwrite existing archive: {target}")
    with sqlite3.connect(f"{source.resolve().as_uri()}?mode=ro", uri=True) as source_db:
        with sqlite3.connect(target) as target_db:
            source_db.backup(target_db)
    with sqlite3.connect(target) as connection:
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0])
        foreign_keys = len(connection.execute("PRAGMA foreign_key_check").fetchall())
    if integrity != "ok" or foreign_keys:
        raise MigrationError("database archive failed SQLite verification")
    return {
        "path": str(target),
        "sha256": sha256_file(target),
        "integrity_check": integrity,
        "foreign_key_violations": foreign_keys,
    }


def _finalized_operational_evidence(database: Path, semester: str) -> dict[str, Any]:
    """Exercise production readers against a v2-only candidate."""
    from ..website.checksums import compute_semester_hash
    from ..website.data import get_semester_data
    from .database_manager import DatabaseManager

    manager = DatabaseManager(db_path=str(database), semester=semester)
    latest_id = manager.get_latest_snapshot_id()
    if latest_id is None:
        raise MigrationError("finalized candidate has no latest snapshot")
    latest = manager.get_snapshot_data(latest_id)
    if latest is None:
        raise MigrationError("finalized candidate cannot reconstruct latest snapshot")
    previous_id = manager.get_previous_snapshot_id(latest_id)
    if previous_id is not None and manager.get_snapshot_data(previous_id) is None:
        raise MigrationError("finalized candidate cannot reconstruct previous snapshot")
    website = get_semester_data(semester, minify=False, database=manager)
    snapshots = website.get("snapshots", [])
    if len(snapshots) != manager.get_database_stats()["snapshots"]:
        raise MigrationError("finalized website snapshot count disagrees with storage")
    if website.get("lastReportTime") != latest.timestamp:
        raise MigrationError("finalized website freshness disagrees with latest state")
    return {
        "latest_snapshot_id": latest_id,
        "previous_snapshot_id": previous_id,
        "latest_last_seen_at": manager.get_latest_snapshot_last_seen_at(),
        "database_stats": manager.get_database_stats(),
        "latest_enrollment_summary": manager.get_enrollment_summary(latest_id),
        "website_snapshot_count": len(snapshots),
        "website_course_count": len(website.get("courses", {})),
        "website_checksum": compute_semester_hash(semester, database=manager),
    }


def _write_finalization_report(path: Path, report: dict[str, Any]) -> None:
    """Write the finalization report and its operator-readable companion."""
    _atomic_write(path, json.dumps(report, indent=2, sort_keys=True) + "\n")
    _atomic_write(
        path.with_suffix(".md"),
        "\n".join(
            [
                "# Checkpointed-state finalization report",
                "",
                f"- Status: `{report['status']}`",
                f"- Semester: `{report['semester']}`",
                f"- Database: `{report['database']}`",
                f"- Rollback archive: `{report['archive']['path']}`",
                f"- Source SHA-256 before: `{report['source_sha256_before']}`",
                f"- Active SHA-256 after: `{report['source_sha256_after']}`",
                f"- Semantic digest preserved: `{report['semantic_digest_preserved']}`",
                "",
            ]
        ),
    )


def finalize_storage(
    database: Path,
    *,
    semester: str,
    report_path: Path,
    rollback_dir: Path | None = None,
    authorized: bool = False,
    phase_hook: PhaseHook | None = None,
) -> FinalizationResult:
    """Finalize a v2 database behind an explicit operator authorization.

    The old database is archived first. A compact candidate is then built from
    a disposable copy, verified, and atomically moved into place. If execution
    stops after candidate creation, the next invocation reuses that candidate;
    if it stops after replacement, the finalized database is reported as an
    idempotent completion.
    """
    if not authorized:
        raise MigrationError("finalization requires explicit authorization")
    database = database.resolve()
    if not database.is_file():
        raise MigrationError(f"database does not exist: {database}")

    source_hash_before = sha256_file(database)
    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        control = connection.execute(
            "SELECT * FROM storage_control WHERE singleton = 1"
        ).fetchone()
    if version != TARGET_SCHEMA_VERSION or control is None:
        raise MigrationError("finalization requires a completed v2 database")
    if str(control["semester"]) != semester:
        raise MigrationError(
            f"database semester {control['semester']!r} does not match {semester!r}"
        )
    active_mode = str(control["active_mode"])
    phase = str(control["migration_phase"])
    if active_mode == "finalized" and phase == "finalized":
        archive_path = Path(str(control["backup_path"] or ""))
        archive_sha256 = str(control["backup_sha256"] or "")
        if (
            not archive_path.is_file()
            or not archive_sha256
            or sha256_file(archive_path) != archive_sha256
        ):
            raise MigrationError(
                "finalized database is missing its verified rollback archive"
            )
        semantic_digest = _v2_semantic_digest(database)
        report = {
            "format": REPORT_FORMAT_VERSION,
            "status": "already_finalized",
            "semester": semester,
            "database": str(database),
            "archive": {
                "path": str(archive_path),
                "sha256": archive_sha256,
            },
            "source_sha256_before": source_hash_before,
            "source_sha256_after": source_hash_before,
            "semantic_digest_preserved": True,
            "semantic_digest": semantic_digest,
        }
        _write_finalization_report(report_path, report)
        return FinalizationResult(
            status="already_finalized",
            database=database,
            archive_path=archive_path,
            report_path=report_path,
            source_hash_before=source_hash_before,
            source_hash_after=source_hash_before,
        )
    if active_mode != "v2" or phase not in {
        "complete",
        "finalizing",
        "finalization:prepared",
    }:
        raise MigrationError(
            "finalization requires active v2 mode and a completed migration"
        )

    if _legacy_fingerprint(database) != str(control["legacy_fingerprint"]):
        raise MigrationError(
            "legacy tables changed during finalization; refusing to discard them"
        )

    store = CheckpointedStateStore(database, initialize=False)
    integrity = store.integrity()
    if integrity["integrity_check"] != "ok" or integrity["foreign_key_violations"]:
        raise MigrationError("finalization blocked by SQLite health checks")
    latest_id = store.get_latest_snapshot_id()
    if latest_id is None:
        raise MigrationError("finalization requires at least one migrated snapshot")
    semantic_digest = _v2_semantic_digest(database)
    if phase == "complete":
        if phase_hook:
            phase_hook("finalization", "before_checkpoint")
        store.force_checkpoint(latest_id)
        if phase_hook:
            phase_hook("finalization", "after_checkpoint")

    rollback_dir = rollback_dir or database.parent / "backups"
    rollback_dir.mkdir(parents=True, exist_ok=True)
    archive = rollback_dir / f"{database.stem}-pre-finalization.db"
    if archive.exists():
        with sqlite3.connect(archive) as connection:
            archive_integrity = str(
                connection.execute("PRAGMA integrity_check").fetchone()[0]
            )
        if archive_integrity != "ok":
            raise MigrationError(f"existing rollback archive is invalid: {archive}")
        with sqlite3.connect(archive) as connection:
            archive_foreign_keys = len(
                connection.execute("PRAGMA foreign_key_check").fetchall()
            )
        archive_details = {
            "path": str(archive),
            "sha256": sha256_file(archive),
            "integrity_check": archive_integrity,
            "foreign_key_violations": archive_foreign_keys,
            "reused": True,
        }
        if archive_foreign_keys:
            raise MigrationError(
                f"existing rollback archive has {archive_foreign_keys} foreign-key violations"
            )
        try:
            archive_semantic_digest = _v2_semantic_digest(archive)
        except (sqlite3.Error, KeyError) as error:
            raise MigrationError(
                f"existing rollback archive is not a v2 database: {archive}"
            ) from error
        if archive_semantic_digest != semantic_digest:
            raise MigrationError(
                "existing rollback archive does not match the active v2 state"
            )
    else:
        if phase_hook:
            phase_hook("finalization", "before_archive")
        archive_details = _verified_database_copy(database, archive)
        if phase_hook:
            phase_hook("finalization", "after_archive")

    candidate = database.with_name(f".{database.name}.finalized")
    staging = database.with_name(f".{database.name}.finalizing-source")
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        marker_digest = hashlib.sha256(
            json.dumps(
                {
                    "archive": archive_details["sha256"],
                    "candidate": str(candidate),
                    "semantic_digest": semantic_digest,
                },
                sort_keys=True,
                separators=(",", ":"),
            ).encode()
        ).hexdigest()
        _phase(connection, "finalization:prepared", marker_digest)
        connection.commit()

    if candidate.exists():
        try:
            existing_candidate_store = CheckpointedStateStore(
                candidate, initialize=False
            )
            existing_integrity = existing_candidate_store.integrity()
            with sqlite3.connect(candidate) as connection:
                existing_control = connection.execute(
                    "SELECT active_mode, migration_phase, legacy_tables_retained "
                    "FROM storage_control WHERE singleton = 1"
                ).fetchone()
            if (
                existing_integrity["integrity_check"] != "ok"
                or existing_integrity["foreign_key_violations"]
                or tuple(existing_control or ()) != ("finalized", "finalized", 0)
                or _v2_semantic_digest(candidate) != semantic_digest
            ):
                candidate.unlink()
        except (sqlite3.Error, MigrationError, OSError):
            candidate.unlink()
        if candidate.exists() and staging.exists():
            staging.unlink()

    if not candidate.exists():
        # A VACUUM interruption leaves only a disposable staging copy. It is
        # safe to discard that generated path because the source and verified
        # archive remain untouched.
        if staging.exists():
            staging.unlink()
        with sqlite3.connect(f"{database.as_uri()}?mode=ro", uri=True) as source_db:
            with sqlite3.connect(staging) as staging_db:
                source_db.backup(staging_db)
        with sqlite3.connect(staging) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("BEGIN IMMEDIATE")
            for table in (
                "reporting_log",
                "enrollment_data",
                "instructor_changes",
                "sections",
                "courses",
                "snapshots",
            ):
                connection.execute(f"DROP TABLE IF EXISTS {table}")
            connection.execute(
                """
                UPDATE storage_control
                SET active_mode = 'finalized', legacy_tables_retained = 0,
                    migration_phase = 'finalized', backup_path = ?,
                    backup_sha256 = ?, updated_at = ?
                WHERE singleton = 1
                """,
                (archive_details["path"], archive_details["sha256"], _now()),
            )
            _phase(connection, "finalization:complete", semantic_digest)
            connection.execute(
                "UPDATE storage_control SET migration_phase = 'finalized' "
                "WHERE singleton = 1"
            )
            connection.execute("PRAGMA user_version = 2")
            connection.commit()
        with sqlite3.connect(staging) as connection:
            escaped = str(candidate).replace("'", "''")
            if phase_hook:
                phase_hook("finalization", "before_vacuum")
            connection.execute(f"VACUUM INTO '{escaped}'")
            if phase_hook:
                phase_hook("finalization", "after_vacuum")
        staging.unlink()

    candidate_store = CheckpointedStateStore(candidate, initialize=False)
    candidate_integrity = candidate_store.integrity()
    if (
        candidate_integrity["integrity_check"] != "ok"
        or candidate_integrity["foreign_key_violations"]
        or _v2_semantic_digest(candidate) != semantic_digest
    ):
        raise MigrationError("finalization candidate failed verification")
    with sqlite3.connect(candidate) as connection:
        candidate_control = connection.execute(
            "SELECT active_mode, migration_phase, legacy_tables_retained "
            "FROM storage_control WHERE singleton = 1"
        ).fetchone()
        legacy_tables = [
            str(row[0])
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table' "
                "AND name IN ('courses','sections','snapshots','enrollment_data',"
                "'reporting_log','instructor_changes')"
            )
        ]
    if (
        candidate_control is None
        or tuple(candidate_control) != ("finalized", "finalized", 0)
        or legacy_tables
    ):
        raise MigrationError(
            "finalization candidate retained legacy compatibility tables"
        )
    operational_evidence = _finalized_operational_evidence(candidate, semester)
    if phase_hook:
        phase_hook("finalization", "before_replace")
    os.replace(candidate, database)
    if phase_hook:
        phase_hook("finalization", "after_replace")

    source_hash_after = sha256_file(database)
    with sqlite3.connect(database) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        final_integrity = str(
            connection.execute("PRAGMA integrity_check").fetchone()[0]
        )
        final_foreign_keys = len(
            connection.execute("PRAGMA foreign_key_check").fetchall()
        )
    if final_integrity != "ok" or final_foreign_keys:
        raise MigrationError("finalized database failed post-replacement verification")
    report = {
        "format": REPORT_FORMAT_VERSION,
        "status": "finalized",
        "semester": semester,
        "database": str(database),
        "archive": archive_details,
        "candidate": str(candidate),
        "source_sha256_before": source_hash_before,
        "source_sha256_after": source_hash_after,
        "semantic_digest": semantic_digest,
        "semantic_digest_preserved": True,
        "integrity_check": final_integrity,
        "foreign_key_violations": final_foreign_keys,
        "operational_evidence": operational_evidence,
    }
    _write_finalization_report(report_path, report)
    return FinalizationResult(
        status="finalized",
        database=database,
        archive_path=archive,
        report_path=report_path,
        source_hash_before=source_hash_before,
        source_hash_after=source_hash_after,
    )
