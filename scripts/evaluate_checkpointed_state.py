"""Run the disposable ADR-0001 prototype against a legacy SQLite database."""

from __future__ import annotations

import argparse
import bisect
import hashlib
import json
import logging
import os
import shutil
import sqlite3
import statistics
import tempfile
import time
from collections import defaultdict
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import asdict
from datetime import datetime
from itertools import pairwise
from pathlib import Path
from typing import Any
from unittest.mock import patch

from checkpointed_state_prototype import (
    CheckpointedStateStore,
    canonical_state_hash,
)

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.excel_reader import ExcelReader
from registrarmonitor.data.instructor_normalization import (
    aggregate_instructors_by_section,
    normalize_instructors,
)
from registrarmonitor.data.snapshot_comparator import SnapshotComparator
from registrarmonitor.data.snapshot_processor import SnapshotProcessor
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.reporting.report_formatter import ReportFormatter
from registrarmonitor.website import data as website_data

ROOT = Path(__file__).parent.parent
MAX_SAMPLES = 20


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile_nearest_rank(samples: Sequence[int], percentile: float) -> int:
    ordered = sorted(samples)
    rank = max(1, (len(ordered) * int(percentile) + 99) // 100)
    return ordered[rank - 1]


def summary(samples: Sequence[int]) -> dict[str, Any]:
    return {
        "samples": len(samples),
        "median_ns": int(statistics.median(samples)),
        "p95_ns": percentile_nearest_rank(samples, 95),
    }


def timed(callable_: Any) -> int:
    started = time.perf_counter_ns()
    callable_()
    return time.perf_counter_ns() - started


def _parse_timestamp(value: str) -> datetime:
    return datetime.fromisoformat(value)


def _json_default(value: Any) -> Any:
    if isinstance(value, Mapping):
        return dict(value)
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        return to_dict()
    raise TypeError(f"Object of type {type(value).__name__} is not JSON serializable")


class LegacyReader:
    """Read legacy state without allowing schema initialization or writes."""

    def __init__(self, path: Path):
        self.path = path.resolve()

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        uri = f"file:{self.path}?mode=ro&immutable=1"
        connection = sqlite3.connect(uri, uri=True)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    def has_column(self, table: str, column: str) -> bool:
        with self.connection() as connection:
            return column in {
                row["name"] for row in connection.execute(f"PRAGMA table_info({table})")
            }

    def semesters(self) -> list[str]:
        with self.connection() as connection:
            return [
                str(row["semester"])
                for row in connection.execute(
                    "SELECT DISTINCT semester FROM snapshots ORDER BY semester"
                )
            ]

    def catalogs(
        self,
    ) -> tuple[list[sqlite3.Row], list[sqlite3.Row], dict[int, str]]:
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
        return (
            courses,
            sections,
            {int(row["section_id"]): row["instructor"] or "" for row in sections},
        )

    def reporting_rows(self) -> list[tuple[int, int, str, int, str]]:
        with self.connection() as connection:
            return [
                (
                    int(row["report_id"]),
                    int(row["reported_snapshot_id"]),
                    row["report_timestamp"],
                    int(row["changes_found"]),
                    row["created_at"],
                )
                for row in connection.execute(
                    """
                    SELECT report_id, reported_snapshot_id, report_timestamp,
                           changes_found, created_at
                    FROM reporting_log ORDER BY report_id
                    """
                )
            ]

    def instructor_changes(self) -> list[sqlite3.Row]:
        with self.connection() as connection:
            return connection.execute(
                """
                SELECT change_id, section_id, old_instructor, new_instructor, timestamp
                FROM instructor_changes
                ORDER BY timestamp, change_id
                """
            ).fetchall()

    def course_history(self, course_code: str, semester: str) -> list[dict[str, Any]]:
        with self.connection() as connection:
            return [
                {
                    "timestamp": row["timestamp"],
                    "section_code": row["section_code"],
                    "fill_percentage": row["fill_percentage"],
                    "enrollment_count": row["enrollment_count"],
                    "capacity_count": row["capacity_count"],
                }
                for row in connection.execute(
                    """
                    SELECT s.timestamp, sec.section_code, e.fill_percentage,
                           e.enrollment_count, e.capacity_count
                    FROM courses c
                    JOIN sections sec ON c.course_id = sec.course_id
                    JOIN enrollment_data e ON sec.section_id = e.section_id
                    JOIN snapshots s ON e.snapshot_id = s.snapshot_id
                    WHERE c.course_code = ? AND s.semester = ?
                    ORDER BY s.timestamp ASC
                    """,
                    (course_code, semester),
                )
            ]

    def load_snapshots(
        self,
    ) -> tuple[list[tuple[int, EnrollmentSnapshot]], dict[str, Any]]:
        courses, sections, final_instructors = self.catalogs()
        has_last_seen_at = self.has_column("snapshots", "last_seen_at")
        course_by_id = {
            int(row["course_id"]): (
                row["course_code"],
                (row["course_title"] or "").strip(),
                row["department"] or "",
            )
            for row in courses
        }
        section_by_id = {
            int(row["section_id"]): (
                int(row["course_id"]),
                row["section_code"],
                row["section_type"] or "",
            )
            for row in sections
        }
        with self.connection() as connection:
            last_seen_expression = (
                "COALESCE(last_seen_at, timestamp)" if has_last_seen_at else "timestamp"
            )
            metadata = connection.execute(
                f"""
                SELECT snapshot_id, timestamp, semester, overall_fill,
                       {last_seen_expression} AS last_seen_at
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

        snapshots: list[tuple[int, EnrollmentSnapshot]] = []
        last_seen_by_snapshot: dict[int, str] = {}
        source_last_seen_differences = 0
        for row in metadata:
            observed_at = str(row["timestamp"])
            last_seen_at = str(row["last_seen_at"])
            if _parse_timestamp(last_seen_at) < _parse_timestamp(observed_at):
                raise ValueError(
                    "legacy last_seen_at precedes timestamp for snapshot "
                    f"{row['snapshot_id']}"
                )
            last_seen_by_snapshot[int(row["snapshot_id"])] = last_seen_at
            source_last_seen_differences += last_seen_at != observed_at
            snapshot = EnrollmentSnapshot(
                timestamp=observed_at,
                semester=row["semester"],
                overall_fill=row["overall_fill"],
            )
            for enrollment_row in enrollment_by_snapshot[int(row["snapshot_id"])]:
                section_id = int(enrollment_row["section_id"])
                course_id, section_code, section_type = section_by_id[section_id]
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
                    enrollment=int(enrollment_row["enrollment_count"]),
                    capacity=int(enrollment_row["capacity_count"]),
                    fill=float(enrollment_row["fill_percentage"]),
                    instructor=final_instructors[section_id],
                )
            for course in snapshot.courses.values():
                course.average_fill = sum(
                    section.fill for section in course.sections.values()
                ) / len(course.sections)
            snapshots.append((int(row["snapshot_id"]), snapshot))
        return snapshots, {
            "courses": courses,
            "sections": sections,
            "final_instructors": final_instructors,
            "section_by_id": section_by_id,
            "course_by_id": course_by_id,
            "last_seen_by_snapshot": last_seen_by_snapshot,
            "freshness": {
                "source_column_present": has_last_seen_at,
                "values_later_than_observed_at": source_last_seen_differences,
            },
        }

    def read_snapshot(self, snapshot_id: int) -> EnrollmentSnapshot:
        courses, sections, final_instructors = self.catalogs()
        course_by_id = {int(row["course_id"]): row for row in courses}
        section_by_id = {int(row["section_id"]): row for row in sections}
        with self.connection() as connection:
            metadata = connection.execute(
                """
                SELECT timestamp, semester, overall_fill
                FROM snapshots WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            if metadata is None:
                raise KeyError(snapshot_id)
            rows = connection.execute(
                """
                SELECT section_id, enrollment_count, capacity_count, fill_percentage
                FROM enrollment_data WHERE snapshot_id = ? ORDER BY section_id
                """,
                (snapshot_id,),
            ).fetchall()
        snapshot = EnrollmentSnapshot(
            metadata["timestamp"], metadata["semester"], metadata["overall_fill"]
        )
        for row in rows:
            section_id = int(row["section_id"])
            section_catalog = section_by_id[section_id]
            course_catalog = course_by_id[int(section_catalog["course_id"])]
            code = course_catalog["course_code"]
            course = snapshot.courses.setdefault(
                code,
                Course(
                    code,
                    course_catalog["department"] or "",
                    course_title=(course_catalog["course_title"] or "").strip() or None,
                ),
            )
            section_code = section_catalog["section_code"]
            course.sections[section_code] = Section(
                section_code,
                section_catalog["section_type"] or "",
                int(row["enrollment_count"]),
                int(row["capacity_count"]),
                float(row["fill_percentage"]),
                final_instructors[section_id],
            )
        for course in snapshot.courses.values():
            course.average_fill = sum(s.fill for s in course.sections.values()) / len(
                course.sections
            )
        return snapshot


def resolve_semester(reader: LegacyReader, requested: str | None) -> str:
    semesters = reader.semesters()
    if not semesters:
        raise ValueError("source database contains no semester")
    if requested is not None:
        if requested not in semesters:
            raise ValueError(
                f"requested semester {requested!r} is not present; found {semesters!r}"
            )
        return requested
    if len(semesters) != 1:
        raise ValueError(
            f"source contains multiple semesters {semesters!r}; pass --semester"
        )
    return semesters[0]


def _copy_snapshot(snapshot: EnrollmentSnapshot) -> EnrollmentSnapshot:
    return EnrollmentSnapshot.from_dict(deepcopy(snapshot.to_dict()))


def corrected_snapshots(
    legacy: list[tuple[int, EnrollmentSnapshot]],
    catalog: dict[str, Any],
    changes: list[sqlite3.Row],
) -> tuple[list[tuple[int, EnrollmentSnapshot]], dict[str, Any]]:
    snapshot_times = [_parse_timestamp(snapshot.timestamp) for _, snapshot in legacy]
    snapshot_ids = [snapshot_id for snapshot_id, _ in legacy]
    changes_by_snapshot: dict[int, list[sqlite3.Row]] = defaultdict(list)
    ambiguous: list[dict[str, Any]] = []
    transitions_by_section: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for change in changes:
        transitions_by_section[int(change["section_id"])].append(change)
        event_time = _parse_timestamp(change["timestamp"])
        insertion = bisect.bisect_left(snapshot_times, event_time)
        candidates = [
            index
            for index in (insertion - 1, insertion)
            if 0 <= index < len(snapshot_times)
        ]
        nearest = min(
            candidates,
            key=lambda index: abs((snapshot_times[index] - event_time).total_seconds()),
        )
        delta = abs((snapshot_times[nearest] - event_time).total_seconds())
        changes_by_snapshot[snapshot_ids[nearest]].append(change)
        if delta > 60:
            ambiguous.append(
                {
                    "change_id": int(change["change_id"]),
                    "event_at": change["timestamp"],
                    "mapped_snapshot_id": snapshot_ids[nearest],
                    "mapped_snapshot_at": legacy[nearest][1].timestamp,
                    "delta_seconds": delta,
                }
            )

    current_instructors = dict(catalog["final_instructors"])
    for section_id, section_changes in transitions_by_section.items():
        current_instructors[section_id] = normalize_instructors(
            [section_changes[0]["old_instructor"]]
        )

    identity_by_key = {
        (
            catalog["course_by_id"][course_id][0],
            section_code,
        ): section_id
        for section_id, (course_id, section_code, _) in catalog["section_by_id"].items()
    }
    corrected: list[tuple[int, EnrollmentSnapshot]] = []
    normalized_noops = 0
    for snapshot_id, legacy_snapshot in legacy:
        for change in changes_by_snapshot.get(snapshot_id, []):
            section_id = int(change["section_id"])
            before = current_instructors.get(section_id, "")
            after = normalize_instructors([change["new_instructor"]])
            if before == after:
                normalized_noops += 1
            current_instructors[section_id] = after
        snapshot = _copy_snapshot(legacy_snapshot)
        for course_code, course in snapshot.courses.items():
            for section_code, section in course.sections.items():
                section_id = identity_by_key[(course_code, section_code)]
                section.instructor = current_instructors.get(section_id, "")
        corrected.append((snapshot_id, snapshot))
    return corrected, {
        "source_events": len(changes),
        "normalized_noops": normalized_noops,
        "ambiguous_mappings": len(ambiguous),
        "ambiguous_samples": ambiguous[:MAX_SAMPLES],
    }


def ambiguous_instructor_changes(
    legacy: Sequence[tuple[int, EnrollmentSnapshot]],
    changes: Sequence[Mapping[str, Any] | sqlite3.Row],
) -> list[Mapping[str, Any] | sqlite3.Row]:
    """Return events too far from legacy snapshots for timestamp-only mapping."""
    snapshot_times = [_parse_timestamp(snapshot.timestamp) for _, snapshot in legacy]
    ambiguous: list[Mapping[str, Any] | sqlite3.Row] = []
    for change in changes:
        event_time = _parse_timestamp(str(change["timestamp"]))
        insertion = bisect.bisect_left(snapshot_times, event_time)
        candidates = [
            index
            for index in (insertion - 1, insertion)
            if 0 <= index < len(snapshot_times)
        ]
        nearest = min(
            candidates,
            key=lambda index: abs((snapshot_times[index] - event_time).total_seconds()),
        )
        if abs((snapshot_times[nearest] - event_time).total_seconds()) > 60:
            ambiguous.append(change)
    return ambiguous


def evaluate_raw_instructor_evidence(
    raw_dir: Path | Sequence[Path],
    semester: str,
    catalog: dict[str, Any],
    changes: Sequence[Mapping[str, Any] | sqlite3.Row],
    *,
    reader: Any | None = None,
    snapshot_timestamps: Sequence[str] | None = None,
    legacy_snapshots: Sequence[tuple[int, EnrollmentSnapshot]] | None = None,
) -> dict[str, Any]:
    """Evaluate recursive raw XLS evidence for a migration candidate.

    Exact duplicate files are tolerated. Multiple different observations at one
    embedded timestamp are conflicts, because migration cannot choose between
    them deterministically. Legacy instructor-change rows remain diagnostics;
    migration parity is instead gated on raw observation coverage and on fields
    that the legacy database stored historically.
    """
    raw_dirs = [raw_dir] if isinstance(raw_dir, Path) else list(raw_dir)
    resolved_dirs = [path.resolve() for path in raw_dirs]
    for path in resolved_dirs:
        if not path.is_dir():
            raise NotADirectoryError(path)
    excel_reader = reader or ExcelReader()
    files = sorted(
        {
            path.resolve()
            for directory in resolved_dirs
            for path in directory.rglob("*.xls")
        }
    )
    observations: list[dict[str, Any]] = []
    parse_failures: list[dict[str, str]] = []
    parse_failure_count = 0
    for path in files:
        try:
            raw_semester, observed_at, rows = excel_reader.read_excel_data(str(path))
        except Exception as error:
            parse_failure_count += 1
            if len(parse_failures) < MAX_SAMPLES:
                parse_failures.append(
                    {"file": path.name, "error": type(error).__name__}
                )
            continue
        if raw_semester.strip() != semester:
            continue
        row_digest = hashlib.sha256(
            json.dumps(rows, sort_keys=True, default=str).encode()
        ).hexdigest()
        observations.append(
            {
                "file": path.name,
                "observed_at": observed_at,
                "instructors": aggregate_instructors_by_section(rows),
                "row_digest": row_digest,
                "rows": rows,
            }
        )
    observations.sort(
        key=lambda item: (item["observed_at"], item["row_digest"], item["file"])
    )

    deduplicated: list[dict[str, Any]] = []
    duplicate_files = 0
    seen_observations: set[tuple[str, str]] = set()
    for observation in observations:
        identity = (observation["observed_at"], observation["row_digest"])
        if identity in seen_observations:
            duplicate_files += 1
            continue
        seen_observations.add(identity)
        deduplicated.append(observation)

    observations_by_timestamp: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for observation in deduplicated:
        observations_by_timestamp[observation["observed_at"]].append(observation)
    conflicting_timestamps = {
        timestamp: values
        for timestamp, values in observations_by_timestamp.items()
        if len(values) > 1
    }
    canonical_observations = [
        values[0] for values in observations_by_timestamp.values() if len(values) == 1
    ]
    canonical_observations.sort(key=lambda item: item["observed_at"])

    raw_transitions: list[dict[str, Any]] = []
    for before, after in pairwise(canonical_observations):
        shared_sections = set(before["instructors"]) & set(after["instructors"])
        for course_code, section_code in sorted(shared_sections):
            old_instructor = before["instructors"][(course_code, section_code)]
            new_instructor = after["instructors"][(course_code, section_code)]
            if old_instructor == new_instructor:
                continue
            raw_transitions.append(
                {
                    "course_code": course_code,
                    "section_code": section_code,
                    "old_instructor": old_instructor,
                    "new_instructor": new_instructor,
                    "before_observed_at": before["observed_at"],
                    "after_observed_at": after["observed_at"],
                }
            )

    snapshot_coverage: dict[str, int] | None = None
    if snapshot_timestamps is not None:
        requested = list(snapshot_timestamps)
        requested_set = set(requested)
        snapshot_coverage = {
            "snapshots": len(requested),
            "matched": sum(
                len(observations_by_timestamp[timestamp]) == 1
                for timestamp in requested
            ),
            "unmatched": sum(
                len(observations_by_timestamp[timestamp]) == 0
                for timestamp in requested
            ),
            "multiply_matched": sum(
                len(observations_by_timestamp[timestamp]) > 1 for timestamp in requested
            ),
            "raw_observations_without_snapshot": sum(
                observation["observed_at"] not in requested_set
                for observation in canonical_observations
            ),
        }

    raw_parity: dict[str, Any] | None = None
    if legacy_snapshots is not None:
        legacy_by_timestamp = {
            snapshot.timestamp: snapshot for _, snapshot in legacy_snapshots
        }
        mismatch_samples: list[dict[str, Any]] = []
        matched = 0
        mismatch_count = 0
        differences_by_field: dict[str, int] = defaultdict(int)
        with tempfile.TemporaryDirectory(
            prefix="registrar-raw-snapshot-prototype-"
        ) as directory:
            processor = SnapshotProcessor(data_dir=directory)
            for observation in canonical_observations:
                expected = legacy_by_timestamp.get(observation["observed_at"])
                if expected is None:
                    continue
                actual = processor.process_data(
                    observation["rows"], semester, observation["observed_at"]
                )
                expected_payload = _migration_invariant_payload(expected)
                actual_payload = _migration_invariant_payload(actual)
                if actual_payload == expected_payload:
                    matched += 1
                else:
                    mismatch_count += 1
                    _count_payload_differences(
                        expected_payload, actual_payload, differences_by_field
                    )
                    if len(mismatch_samples) < MAX_SAMPLES:
                        mismatch_samples.append(
                            {
                                "observed_at": observation["observed_at"],
                                "file": observation["file"],
                                "expected": expected_payload,
                                "actual": actual_payload,
                            }
                        )
        raw_parity = {
            "snapshots_compared": matched + mismatch_count,
            "matches": matched,
            "mismatches": mismatch_count,
            "differences_by_field": dict(sorted(differences_by_field.items())),
            "mismatch_samples": mismatch_samples,
            "fields": [
                "timestamp",
                "semester",
                "overall_fill",
                "course and section presence",
                "enrollment_count",
                "capacity_count",
            ],
        }

    resolved: list[dict[str, Any]] = []
    unresolved: list[dict[str, Any]] = []
    multiply_matched: list[dict[str, Any]] = []
    for change in changes:
        section_id = int(change["section_id"])
        course_id, section_code, _ = catalog["section_by_id"][section_id]
        course_code = str(catalog["course_by_id"][course_id][0])
        key = (course_code, section_code)
        old_instructor = normalize_instructors([change["old_instructor"]])
        new_instructor = normalize_instructors([change["new_instructor"]])
        matches: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for before, after in pairwise(canonical_observations):
            if (
                key in before["instructors"]
                and key in after["instructors"]
                and before["instructors"][key] == old_instructor
                and after["instructors"][key] == new_instructor
                and old_instructor != new_instructor
            ):
                matches.append((before, after))
        detail = {
            "change_id": int(change["change_id"]),
            "course_code": course_code,
            "section_code": section_code,
            "old_instructor": old_instructor,
            "new_instructor": new_instructor,
        }
        if len(matches) == 1:
            before, after = matches[0]
            resolved.append(
                {
                    **detail,
                    "before": {
                        "file": before["file"],
                        "observed_at": before["observed_at"],
                    },
                    "after": {
                        "file": after["file"],
                        "observed_at": after["observed_at"],
                    },
                }
            )
        elif matches:
            multiply_matched.append({**detail, "match_count": len(matches)})
        else:
            unresolved.append(detail)

    return {
        "raw_dir_classification": "operator_supplied_local_directories",
        "raw_directories": [str(path) for path in resolved_dirs],
        "files_scanned": len(files),
        "semester_files": len(observations),
        "deduplicated_semester_observations": len(deduplicated),
        "duplicate_files": duplicate_files,
        "conflicting_timestamp_count": len(conflicting_timestamps),
        "conflicting_timestamp_samples": [
            {
                "observed_at": timestamp,
                "files": [item["file"] for item in values[:MAX_SAMPLES]],
            }
            for timestamp, values in list(sorted(conflicting_timestamps.items()))[
                :MAX_SAMPLES
            ]
        ],
        "parse_failure_count": parse_failure_count,
        "parse_failure_samples": parse_failures,
        "snapshot_coverage": snapshot_coverage,
        "raw_snapshot_parity": raw_parity,
        "raw_derived_instructor_transitions": len(raw_transitions),
        "raw_derived_transition_samples": raw_transitions[:MAX_SAMPLES],
        "events_checked": len(changes),
        "resolved": len(resolved),
        "unresolved": len(unresolved),
        "multiply_matched": len(multiply_matched),
        "resolved_events": resolved[:MAX_SAMPLES],
        "unresolved_events": unresolved[:MAX_SAMPLES],
        "multiply_matched_events": multiply_matched[:MAX_SAMPLES],
    }


def _migration_invariant_payload(snapshot: EnrollmentSnapshot) -> dict[str, Any]:
    """Return fields independently historical in both raw XLS and legacy SQLite."""
    return {
        "timestamp": snapshot.timestamp,
        "semester": snapshot.semester,
        "overall_fill": snapshot.overall_fill,
        "courses": {
            course_code: {
                section_code: {
                    "enrollment_count": section.enrollment,
                    "capacity_count": section.capacity,
                }
                for section_code, section in sorted(course.sections.items())
            }
            for course_code, course in sorted(snapshot.courses.items())
        },
    }


def _count_payload_differences(
    expected: Any,
    actual: Any,
    counts: dict[str, int],
    *,
    field: str = "root",
) -> None:
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in expected.keys() | actual.keys():
            _count_payload_differences(
                expected.get(key),
                actual.get(key),
                counts,
                field=str(key),
            )
        return
    if expected != actual:
        counts[field] += 1


def compatibility_snapshot(
    snapshot: EnrollmentSnapshot,
    catalog: dict[str, Any],
) -> EnrollmentSnapshot:
    compatible = _copy_snapshot(snapshot)
    identity_by_key = {
        (
            catalog["course_by_id"][course_id][0],
            section_code,
        ): section_id
        for section_id, (course_id, section_code, _) in catalog["section_by_id"].items()
    }
    for course_code, course in compatible.courses.items():
        for section_code, section in course.sections.items():
            section.instructor = catalog["final_instructors"][
                identity_by_key[(course_code, section_code)]
            ]
    return compatible


def _history(
    snapshots: list[tuple[int, EnrollmentSnapshot]], course_code: str
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for _, snapshot in snapshots:
        course = snapshot.courses.get(course_code)
        if course is None:
            continue
        for section_code, section in course.sections.items():
            rows.append(
                {
                    "timestamp": snapshot.timestamp,
                    "section_code": section_code,
                    "fill_percentage": section.fill,
                    "enrollment_count": section.enrollment,
                    "capacity_count": section.capacity,
                }
            )
    return rows


def _format_all_reports(
    snapshots: list[tuple[int, EnrollmentSnapshot]],
) -> int:
    comparator = SnapshotComparator()
    formatter = ReportFormatter()
    total_bytes = 0
    for (_, previous), (_, current) in pairwise(snapshots):
        comparison = comparator.compare_snapshots(current, previous)
        total_bytes += len(
            formatter.format_changes_report(comparison, current, previous).encode()
        )
    return total_bytes


def _normalized_comparison(comparison: Any) -> dict[str, Any]:
    """Normalize comparator set-iteration order without changing semantics."""
    payload = asdict(comparison)
    for key in ("new_courses", "removed_courses"):
        payload[key].sort(key=lambda item: item["course_code"])
    for course in payload["changed_courses"]:
        for key in ("added_sections", "removed_sections", "modified_sections"):
            course[key].sort(key=lambda item: item["section_id"])
    payload["changed_courses"].sort(key=lambda item: item["course_code"])
    return payload


def _structural_events(
    snapshots: list[tuple[int, EnrollmentSnapshot]],
) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for (_, previous), (_, current) in pairwise(snapshots):
        previous_codes = set(previous.courses)
        current_codes = set(current.courses)
        for code in sorted(current_codes - previous_codes):
            events[code].append(
                {
                    "eventType": "course_added",
                    "snapshotTimestamp": current.timestamp,
                }
            )
        for code in sorted(previous_codes - current_codes):
            events[code].append(
                {
                    "eventType": "course_removed",
                    "snapshotTimestamp": current.timestamp,
                }
            )
        for code in sorted(previous_codes & current_codes):
            old_sections = previous.courses[code].sections
            new_sections = current.courses[code].sections
            for section_code in sorted(set(new_sections) - set(old_sections)):
                events[code].append(
                    {
                        "eventType": "section_added",
                        "sectionCode": section_code,
                        "snapshotTimestamp": current.timestamp,
                    }
                )
            for section_code in sorted(set(old_sections) - set(new_sections)):
                events[code].append(
                    {
                        "eventType": "section_removed",
                        "sectionCode": section_code,
                        "snapshotTimestamp": current.timestamp,
                    }
                )
            for section_code in sorted(set(old_sections) & set(new_sections)):
                old = old_sections[section_code]
                new = new_sections[section_code]
                if old.capacity != new.capacity:
                    events[code].append(
                        {
                            "eventType": "capacity_changed",
                            "sectionCode": section_code,
                            "oldValue": str(old.capacity),
                            "newValue": str(new.capacity),
                            "snapshotTimestamp": current.timestamp,
                        }
                    )
    return events


def _instructor_events(
    reader: LegacyReader, catalog: dict[str, Any]
) -> dict[str, list[dict[str, Any]]]:
    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    last_by_section: dict[int, tuple[str, str]] = {}
    for row in reader.instructor_changes():
        section_id = int(row["section_id"])
        transition = (row["old_instructor"] or "", row["new_instructor"] or "")
        if last_by_section.get(section_id) == transition:
            continue
        last_by_section[section_id] = transition
        course_id, section_code, _ = catalog["section_by_id"][section_id]
        course_code = catalog["course_by_id"][course_id][0]
        events[course_code].append(
            {
                "eventType": "instructor_changed",
                "sectionCode": section_code,
                "oldValue": row["old_instructor"] or "TBA",
                "newValue": row["new_instructor"] or "TBA",
                "snapshotTimestamp": row["timestamp"],
            }
        )
    return events


def build_graph(
    snapshots: list[tuple[int, EnrollmentSnapshot]],
    *,
    instructor_events: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    latest = snapshots[-1][1]
    data: dict[str, Any] = {
        "semester": latest.semester,
        "lastReportTime": latest.timestamp,
        "snapshots": [
            {
                "id": snapshot_id,
                "timestamp": snapshot.timestamp,
                "overallFill": snapshot.overall_fill,
            }
            for snapshot_id, snapshot in snapshots
        ],
        "courses": {},
    }
    for code, course in latest.courses.items():
        sections: dict[str, Any] = {}
        for section_code, section in course.sections.items():
            history = []
            for index, (_, historical) in enumerate(snapshots):
                old_course = historical.courses.get(code)
                if old_course is None or section_code not in old_course.sections:
                    continue
                old = old_course.sections[section_code]
                history.append(
                    {
                        "snapshotIdx": index,
                        "fill": old.fill,
                        "enrollment": old.enrollment,
                        "capacity": old.capacity,
                    }
                )
            sections[section_code] = {
                "type": section.section_type,
                "instructor": section.instructor or "",
                "currentEnrollment": section.enrollment,
                "currentCapacity": section.capacity,
                "currentFill": section.fill,
                "sectionId": section_code,
                "history": history,
            }
        data["courses"][code] = {
            "department": course.department,
            "title": course.course_title or "",
            "averageFill": course.average_fill,
            "sections": sections,
            "isFilled": course.is_filled,
        }

    milestones = website_data.get_milestones(latest.semester)
    if milestones:
        keep = website_data._history_indices_in_milestone_window(
            data["snapshots"], milestones, buffer_hours=1
        )
        if keep is not None:
            for course in data["courses"].values():
                for section in course["sections"].values():
                    section["history"] = [
                        point
                        for point in section["history"]
                        if point["snapshotIdx"] in keep
                    ]
    website_data._compact_histories_for_website(data)
    events = _structural_events(snapshots)
    for code, values in instructor_events.items():
        events[code].extend(values)
    for code, values in events.items():
        if code in data["courses"]:
            data["courses"][code]["events"] = values
    return data


def build_graph_from_store(
    store: CheckpointedStateStore,
    *,
    semester: str,
    instructor_events: dict[str, list[dict[str, Any]]],
    compatibility_instructors: dict[int, str] | None = None,
) -> dict[str, Any]:
    """Build the current graph shape directly from latest state and events."""
    with store.connection() as connection:
        metadata = connection.execute(
            """
            SELECT snapshot_id, sequence_no, observed_at, overall_fill
            FROM state_snapshot ORDER BY sequence_no
            """
        ).fetchall()
        current_courses = connection.execute(
            """
            SELECT c.course_id, c.course_code, l.title, l.department
            FROM course_latest_state l
            JOIN course_catalog c ON c.course_id = l.course_id
            ORDER BY c.course_code
            """
        ).fetchall()
        current_sections = connection.execute(
            """
            SELECT s.section_id, c.course_code, s.section_code,
                   l.section_type, l.enrollment_count,
                   l.capacity_count, l.instructor
            FROM section_latest_state l
            JOIN section_catalog s ON s.section_id = l.section_id
            JOIN course_catalog c ON c.course_id = s.course_id
            ORDER BY c.course_code, s.section_code
            """
        ).fetchall()
        section_events = connection.execute(
            """
            SELECT e.snapshot_id, e.section_id, e.event_kind,
                   c.course_code, s.section_code,
                   e.old_enrollment_count, e.new_enrollment_count,
                   e.old_capacity_count, e.new_capacity_count
            FROM section_change_event e
            JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
            JOIN section_catalog s ON s.section_id = e.section_id
            JOIN course_catalog c ON c.course_id = s.course_id
            ORDER BY ss.sequence_no, e.event_id
            """
        ).fetchall()
        course_events = connection.execute(
            """
            SELECT e.snapshot_id, e.event_kind, c.course_code
            FROM course_change_event e
            JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
            JOIN course_catalog c ON c.course_id = e.course_id
            ORDER BY ss.sequence_no, e.event_id
            """
        ).fetchall()

    snapshot_index = {
        int(row["snapshot_id"]): index for index, row in enumerate(metadata)
    }
    courses_data: dict[str, dict[str, Any]] = {
        row["course_code"]: {
            "department": row["department"],
            "title": row["title"] or "",
            "averageFill": 0.0,
            "sections": {},
        }
        for row in current_courses
    }
    data: dict[str, Any] = {
        "semester": semester,
        "lastReportTime": metadata[-1]["observed_at"],
        "snapshots": [
            {
                "id": int(row["snapshot_id"]),
                "timestamp": row["observed_at"],
                "overallFill": row["overall_fill"],
            }
            for row in metadata
        ],
        "courses": courses_data,
    }
    current_by_section = {int(row["section_id"]): row for row in current_sections}
    history_points: dict[int, dict[int, dict[str, Any]]] = defaultdict(dict)
    events_by_snapshot: dict[int, list[sqlite3.Row]] = defaultdict(list)
    for event in section_events:
        section_id = int(event["section_id"])
        if section_id in current_by_section:
            events_by_snapshot[int(event["snapshot_id"])].append(event)

    def add_point(section_id: int, index: int, enrollment: int, capacity: int) -> None:
        if index < 0:
            return
        history_points[section_id][index] = {
            "snapshotIdx": index,
            "fill": enrollment / capacity,
            "enrollment": enrollment,
            "capacity": capacity,
        }

    for event in section_events:
        section_id = int(event["section_id"])
        if section_id not in current_by_section:
            continue
        index = snapshot_index[int(event["snapshot_id"])]
        if event["event_kind"] == "ADD":
            add_point(
                section_id,
                index,
                int(event["new_enrollment_count"]),
                int(event["new_capacity_count"]),
            )
        elif event["event_kind"] == "REMOVE":
            add_point(
                section_id,
                index - 1,
                int(event["old_enrollment_count"]),
                int(event["old_capacity_count"]),
            )
        elif (
            event["old_enrollment_count"] != event["new_enrollment_count"]
            or event["old_capacity_count"] != event["new_capacity_count"]
        ):
            add_point(
                section_id,
                index - 1,
                int(event["old_enrollment_count"]),
                int(event["old_capacity_count"]),
            )
            add_point(
                section_id,
                index,
                int(event["new_enrollment_count"]),
                int(event["new_capacity_count"]),
            )

    milestones = website_data.get_milestones(semester)
    keep = (
        website_data._history_indices_in_milestone_window(
            data["snapshots"], milestones, buffer_hours=1
        )
        if milestones
        else None
    )
    if keep is None:
        boundary_indices = {0, len(metadata) - 1}
    elif keep:
        boundary_indices = {min(keep), max(keep)}
    else:
        boundary_indices = set()
    section_state: dict[int, tuple[int, int]] = {}
    for index, metadata_row in enumerate(metadata):
        for event in events_by_snapshot.get(int(metadata_row["snapshot_id"]), []):
            section_id = int(event["section_id"])
            if event["event_kind"] == "REMOVE":
                section_state.pop(section_id, None)
            else:
                section_state[section_id] = (
                    int(event["new_enrollment_count"]),
                    int(event["new_capacity_count"]),
                )
        if index in boundary_indices:
            for section_id, (enrollment, capacity) in section_state.items():
                add_point(section_id, index, enrollment, capacity)

    events_by_section: dict[int, list[sqlite3.Row]] = defaultdict(list)
    section_ids_by_course: dict[str, list[int]] = defaultdict(list)
    for event in section_events:
        section_id = int(event["section_id"])
        if section_id in current_by_section:
            events_by_section[section_id].append(event)
    for section_id, row in current_by_section.items():
        section_ids_by_course[row["course_code"]].append(section_id)
    for section_ids in section_ids_by_course.values():
        course_indices_set = {
            index
            for section_id in section_ids
            for index in history_points[section_id]
            if keep is None or index in keep
        }
        event_indices = {
            candidate
            for section_id in section_ids
            for event in events_by_section[section_id]
            for candidate in (
                snapshot_index[int(event["snapshot_id"])] - 1,
                snapshot_index[int(event["snapshot_id"])],
            )
            if candidate >= 0 and (keep is None or candidate in keep)
        }
        course_indices_set.update(event_indices)
        course_indices = sorted(course_indices_set)
        for section_id in section_ids:
            state: tuple[int, int] | None = None
            events = events_by_section[section_id]
            event_index = 0
            for index in course_indices:
                while (
                    event_index < len(events)
                    and snapshot_index[int(events[event_index]["snapshot_id"])] <= index
                ):
                    event = events[event_index]
                    if event["event_kind"] == "REMOVE":
                        state = None
                    else:
                        state = (
                            int(event["new_enrollment_count"]),
                            int(event["new_capacity_count"]),
                        )
                    event_index += 1
                if state is not None:
                    add_point(section_id, index, *state)

    for section_id, row in current_by_section.items():
        history = [
            point
            for index, point in sorted(history_points[section_id].items())
            if keep is None or index in keep
        ]
        courses_data[row["course_code"]]["sections"][row["section_code"]] = {
            "type": row["section_type"],
            "instructor": (
                compatibility_instructors.get(section_id, row["instructor"])
                if compatibility_instructors is not None
                else row["instructor"]
            ),
            "currentEnrollment": int(row["enrollment_count"]),
            "currentCapacity": int(row["capacity_count"]),
            "currentFill": int(row["enrollment_count"]) / int(row["capacity_count"]),
            "sectionId": row["section_code"],
            "history": history,
        }
    for course in courses_data.values():
        sections = course["sections"]
        course["averageFill"] = sum(
            section["currentFill"] for section in sections.values()
        ) / len(sections)
        by_type: dict[str, list[float]] = defaultdict(list)
        for section in sections.values():
            by_type[section["type"]].append(section["currentFill"])
        course["isFilled"] = any(
            all(fill >= 1.0 for fill in fills) for fills in by_type.values()
        )
    website_data._compact_histories_for_website(data)

    events: dict[str, list[dict[str, Any]]] = defaultdict(list)
    course_event_keys = {
        (int(row["snapshot_id"]), row["course_code"]): row["event_kind"]
        for row in course_events
    }
    first_snapshot_id = int(metadata[0]["snapshot_id"])
    for event in course_events:
        if int(event["snapshot_id"]) == first_snapshot_id:
            continue
        events[event["course_code"]].append(
            {
                "eventType": (
                    "course_added" if event["event_kind"] == "ADD" else "course_removed"
                ),
                "snapshotTimestamp": metadata[
                    snapshot_index[int(event["snapshot_id"])]
                ]["observed_at"],
            }
        )
    for event in section_events:
        snapshot_id = int(event["snapshot_id"])
        course_code = event["course_code"]
        if (snapshot_id, course_code) in course_event_keys:
            continue
        timestamp = metadata[snapshot_index[snapshot_id]]["observed_at"]
        if event["event_kind"] in {"ADD", "REMOVE"}:
            events[course_code].append(
                {
                    "eventType": (
                        "section_added"
                        if event["event_kind"] == "ADD"
                        else "section_removed"
                    ),
                    "sectionCode": event["section_code"],
                    "snapshotTimestamp": timestamp,
                }
            )
        elif event["old_capacity_count"] != event["new_capacity_count"]:
            events[course_code].append(
                {
                    "eventType": "capacity_changed",
                    "sectionCode": event["section_code"],
                    "oldValue": str(event["old_capacity_count"]),
                    "newValue": str(event["new_capacity_count"]),
                    "snapshotTimestamp": timestamp,
                }
            )
    for code, values in instructor_events.items():
        events[code].extend(values)
    for code, values in events.items():
        if code in courses_data:
            courses_data[code]["events"] = values
    return data


def normalize_graph(data: dict[str, Any]) -> dict[str, Any]:
    normalized = deepcopy(data)
    for course in normalized.get("courses", {}).values():
        for section_code, section in course.get("sections", {}).items():
            section["sectionId"] = section_code
        if "events" in course:
            course["events"] = sorted(
                course["events"],
                key=lambda event: (
                    event.get("snapshotTimestamp", ""),
                    event.get("eventType", ""),
                    event.get("sectionCode", ""),
                    event.get("oldValue", ""),
                    event.get("newValue", ""),
                ),
            )
    return normalized


def graph_differences(
    expected: Any,
    actual: Any,
    *,
    path: str = "$",
    limit: int = MAX_SAMPLES,
) -> list[dict[str, Any]]:
    differences: list[dict[str, Any]] = []

    def compare(left: Any, right: Any, current_path: str) -> None:
        if len(differences) >= limit:
            return
        if type(left) is not type(right):
            differences.append(
                {
                    "path": current_path,
                    "expected": repr(left)[:240],
                    "actual": repr(right)[:240],
                }
            )
            return
        if isinstance(left, dict):
            for key in sorted(set(left) | set(right), key=str):
                if key not in left or key not in right:
                    differences.append(
                        {
                            "path": f"{current_path}.{key}",
                            "expected": repr(left.get(key, "<missing>"))[:240],
                            "actual": repr(right.get(key, "<missing>"))[:240],
                        }
                    )
                else:
                    compare(left[key], right[key], f"{current_path}.{key}")
                if len(differences) >= limit:
                    return
            return
        if isinstance(left, list):
            if len(left) != len(right):
                differences.append(
                    {
                        "path": current_path,
                        "expected": f"list length {len(left)}",
                        "actual": f"list length {len(right)}",
                    }
                )
            for index, (left_item, right_item) in enumerate(
                zip(left, right, strict=False)
            ):
                compare(left_item, right_item, f"{current_path}[{index}]")
                if len(differences) >= limit:
                    return
            return
        if left != right:
            differences.append(
                {
                    "path": current_path,
                    "expected": repr(left)[:240],
                    "actual": repr(right)[:240],
                }
            )

    compare(expected, actual, path)
    return differences


class _ReadOnlyWebsiteDatabase:
    path: Path
    query_count = 0

    def __init__(self, db_path: str | None = None, semester: str | None = None):
        self.db_path = self.path
        # Keep the harness on the legacy reader path.  Website data now
        # selects the reader from the manager's storage mode, and this
        # deliberately read-only adapter never constructs the manager's
        # normal mode state.
        self.storage_mode = "legacy"

    @contextmanager
    def get_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(
            f"file:{self.path.resolve()}?mode=ro&immutable=1", uri=True
        )
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        connection.set_trace_callback(
            lambda sql: setattr(
                type(self),
                "query_count",
                type(self).query_count + int(sql.lstrip().upper().startswith("SELECT")),
            )
        )
        try:
            yield connection
        finally:
            connection.close()


def current_graph(path: Path, semester: str) -> tuple[dict[str, Any], int]:
    _ReadOnlyWebsiteDatabase.path = path
    _ReadOnlyWebsiteDatabase.query_count = 0
    with patch.object(website_data, "DatabaseManager", _ReadOnlyWebsiteDatabase):
        result = website_data.get_semester_data(semester, minify=False)
    return result, _ReadOnlyWebsiteDatabase.query_count


def _record_mismatch(
    bucket: dict[str, Any], category: str, detail: dict[str, Any]
) -> None:
    item = bucket.setdefault(category, {"count": 0, "samples": []})
    item["count"] += 1
    if len(item["samples"]) < MAX_SAMPLES:
        item["samples"].append(detail)


def _benchmark_writes(
    source: Path,
    prototype: Path,
    latest: EnrollmentSnapshot,
    semester: str,
) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="registrar-prototype-writes-") as directory:
        root = Path(directory)
        v2_path = root / "v2.db"
        shutil.copy2(prototype, v2_path)
        v2 = CheckpointedStateStore(v2_path)
        duplicate_samples = []
        duplicate = _copy_snapshot(latest)
        before_bytes = v2_path.stat().st_size
        for index in range(1_000):
            duplicate.timestamp = f"2098-12-31 23:{index // 60:02d}:{index % 60:02d}"
            duplicate_samples.append(timed(lambda: v2.write_snapshot(duplicate)))
        after_bytes = v2_path.stat().st_size

        changed_samples = []
        changed = _copy_snapshot(latest)
        first_course = next(iter(changed.courses.values()))
        first_section = next(iter(first_course.sections.values()))
        for index in range(20):
            changed.timestamp = f"2099-12-31 23:59:{index:02d}"
            first_section.enrollment += 1 if index % 2 == 0 else -1
            first_section.fill = first_section.enrollment / first_section.capacity
            changed_samples.append(timed(lambda: v2.write_snapshot(changed)))

        legacy_path = root / "legacy.db"
        shutil.copy2(source, legacy_path)
        logging.getLogger("registrarmonitor").setLevel(logging.ERROR)
        old = DatabaseManager(db_path=str(legacy_path), semester=semester)
        old_latest_id = old.get_latest_snapshot_id()
        if old_latest_id is None:
            raise ValueError("legacy benchmark has no latest snapshot")
        old_latest = old.get_snapshot_data(old_latest_id)
        if old_latest is None:
            raise ValueError("legacy benchmark latest snapshot is missing")
        old_duplicate_samples = []
        for index in range(1_000):
            old_latest.timestamp = f"2098-12-30 23:{index // 60:02d}:{index % 60:02d}"
            old_duplicate_samples.append(
                timed(lambda: old.store_enrollment_snapshot(old_latest))
            )
        old_changed_samples = []
        old_first_course = next(iter(old_latest.courses.values()))
        old_first_section = next(iter(old_first_course.sections.values()))
        for index in range(20):
            old_latest.timestamp = f"2099-12-30 23:59:{index:02d}"
            old_first_section.enrollment += 1 if index % 2 == 0 else -1
            old_first_section.fill = (
                old_first_section.enrollment / old_first_section.capacity
            )
            old_changed_samples.append(
                timed(lambda: old.store_enrollment_snapshot(old_latest))
            )

    return {
        "prototype": {
            "unchanged_write": summary(duplicate_samples),
            "changed_write": summary(changed_samples),
            "unchanged_bytes_added": after_bytes - before_bytes,
        },
        "legacy": {
            "unchanged_write": summary(old_duplicate_samples),
            "changed_write": summary(old_changed_samples),
        },
    }


def _summarize_profile_phases(
    profiles: Sequence[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    phase_names = profiles[0]["phases_ns"] if profiles else {}
    return {
        phase: summary([profile["phases_ns"][phase] for profile in profiles])
        for phase in phase_names
    }


def benchmark_targeted_performance(
    prototype: Path,
    latest: EnrollmentSnapshot,
    *,
    samples: int = 100,
    warmup: int = 10,
) -> dict[str, Any]:
    """Profile Spring-scale point writes and reads in a disposable copy."""
    if samples < 1:
        raise ValueError("samples must be positive")
    if warmup < 0:
        raise ValueError("warmup must not be negative")

    with tempfile.TemporaryDirectory(
        prefix="registrar-targeted-performance-"
    ) as directory:
        path = Path(directory) / "profile.db"
        shutil.copy2(prototype, path)
        store = CheckpointedStateStore(path)
        changed = _copy_snapshot(latest)
        first_course = next(iter(changed.courses.values()))
        first_section = next(iter(first_course.sections.values()))

        for index in range(warmup):
            changed.timestamp = f"2100-01-01 00:{index // 60:02d}:{index % 60:02d}"
            first_section.enrollment += 1 if index % 2 == 0 else -1
            first_section.fill = first_section.enrollment / first_section.capacity
            store.write_snapshot(changed)

        write_profiles: list[dict[str, Any]] = []
        for index in range(samples):
            sequence = warmup + index
            changed.timestamp = (
                f"2100-01-02 00:{sequence // 60:02d}:{sequence % 60:02d}"
            )
            first_section.enrollment += 1 if sequence % 2 == 0 else -1
            first_section.fill = first_section.enrollment / first_section.capacity
            _, profile = store.profile_write_snapshot(changed)
            write_profiles.append(profile)

        latest_id = store.get_latest_snapshot_id()
        if latest_id is None:
            raise ValueError("targeted benchmark produced no latest snapshot")
        for _ in range(warmup):
            store.reconstruct_snapshot(latest_id)

        read_profiles: list[dict[str, Any]] = []
        for _ in range(samples):
            _, profile = store.profile_reconstruct_snapshot(latest_id)
            read_profiles.append(profile)

        return {
            "samples": samples,
            "warmup": warmup,
            "changed_write": summary(
                [profile["total_ns"] for profile in write_profiles]
            ),
            "changed_write_phases": _summarize_profile_phases(write_profiles),
            "latest_read": summary([profile["total_ns"] for profile in read_profiles]),
            "latest_read_phases": _summarize_profile_phases(read_profiles),
            "query_plans": read_profiles[-1]["query_plans"],
        }


def evaluate_failure_injection(
    source: Path,
    prototype: Path,
    latest: EnrollmentSnapshot,
) -> dict[str, Any]:
    """Exercise migration boundaries using disposable files and databases only."""
    source = source.resolve()
    prototype = prototype.resolve()
    source_hash_before = sha256_file(source)
    scenarios: dict[str, dict[str, Any]] = {}

    with tempfile.TemporaryDirectory(
        prefix="registrar-targeted-recovery-"
    ) as directory:
        root = Path(directory)

        phase_path = root / "phase.db"
        with sqlite3.connect(phase_path) as connection:
            connection.executescript(
                """
                CREATE TABLE phase_marker (
                    phase TEXT PRIMARY KEY,
                    completed_at TEXT NOT NULL
                );
                CREATE TABLE backfill_payload (
                    source_id INTEGER PRIMARY KEY,
                    value TEXT NOT NULL
                );
                """
            )
            try:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    "INSERT INTO backfill_payload(source_id, value) VALUES (1, 'one')"
                )
                connection.execute(
                    """
                    INSERT INTO phase_marker(phase, completed_at)
                    VALUES ('backfill', CURRENT_TIMESTAMP)
                    """
                )
                raise RuntimeError("injected before additive backfill commit")
            except RuntimeError:
                connection.rollback()
            rows_after_interrupt = int(
                connection.execute("SELECT count(*) FROM backfill_payload").fetchone()[
                    0
                ]
            )
            for _ in range(2):
                with connection:
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO backfill_payload(source_id, value)
                        VALUES (1, 'one')
                        """
                    )
                    connection.execute(
                        """
                        INSERT OR IGNORE INTO phase_marker(phase, completed_at)
                        VALUES ('backfill', CURRENT_TIMESTAMP)
                        """
                    )
            rows_after_restart = int(
                connection.execute("SELECT count(*) FROM backfill_payload").fetchone()[
                    0
                ]
            )
            markers_after_restart = int(
                connection.execute("SELECT count(*) FROM phase_marker").fetchone()[0]
            )
        scenarios["additive_backfill"] = {
            "passed": (
                rows_after_interrupt == 0
                and rows_after_restart == 1
                and markers_after_restart == 1
            ),
            "rows_after_interrupt": rows_after_interrupt,
            "rows_after_restart": rows_after_restart,
            "markers_after_restart": markers_after_restart,
        }

        dual_path = root / "dual-write.db"
        shutil.copy2(prototype, dual_path)
        dual = CheckpointedStateStore(dual_path)
        before_stats = dual.statistics()
        invalid = _copy_snapshot(latest)
        invalid.timestamp = "2199-01-01 00:00:00"
        first_course = next(iter(invalid.courses.values()))
        first_section = next(iter(first_course.sections.values()))
        first_section.capacity = 0
        first_section.fill = 0.0
        rejected = False
        try:
            dual.write_snapshot(invalid)
        except sqlite3.IntegrityError:
            rejected = True
        after_stats = dual.statistics()
        dual_integrity = dual.integrity()
        scenarios["dual_write"] = {
            "passed": (
                rejected
                and before_stats == after_stats
                and dual_integrity["integrity_check"] == "ok"
                and dual_integrity["foreign_key_violations"] == 0
            ),
            "injected_write_rejected": rejected,
            "state_unchanged": before_stats == after_stats,
            **dual_integrity,
        }

        pointer = root / "manifest.json"
        previous = b'{"current":"previous"}\n'
        candidate_bytes = b'{"current":"candidate","previous":"previous"}\n'
        pointer.write_bytes(previous)
        candidate_pointer = root / "manifest.candidate.json"
        candidate_pointer.write_bytes(candidate_bytes)
        unchanged_before_cutover = pointer.read_bytes() == previous
        os.replace(candidate_pointer, pointer)
        cutover_applied = pointer.read_bytes() == candidate_bytes
        rollback_pointer = root / "manifest.rollback.json"
        rollback_pointer.write_bytes(previous)
        os.replace(rollback_pointer, pointer)
        restored_previous = pointer.read_bytes() == previous
        scenarios["static_cutover"] = {
            "passed": (
                unchanged_before_cutover and cutover_applied and restored_previous
            ),
            "unchanged_before_cutover": unchanged_before_cutover,
            "cutover_applied": cutover_applied,
            "restored_previous": restored_previous,
        }

        active = root / "active.db"
        backup = root / "active.backup.db"
        candidate = root / "active.candidate.db"
        shutil.copy2(prototype, active)
        shutil.copy2(active, backup)
        active_hash_before = sha256_file(active)
        with sqlite3.connect(active) as connection:
            connection.execute("VACUUM INTO ?", (str(candidate),))
        with sqlite3.connect(candidate) as connection:
            candidate_integrity = connection.execute(
                "PRAGMA integrity_check"
            ).fetchone()[0]
        unchanged_before_replace = sha256_file(active) == active_hash_before
        os.replace(candidate, active)
        replacement_integrity = CheckpointedStateStore(active).integrity()
        restore_candidate = root / "active.restore.db"
        shutil.copy2(backup, restore_candidate)
        os.replace(restore_candidate, active)
        restored_backup = sha256_file(active) == sha256_file(backup)
        scenarios["atomic_replacement"] = {
            "passed": (
                candidate_integrity == "ok"
                and unchanged_before_replace
                and replacement_integrity["integrity_check"] == "ok"
                and replacement_integrity["foreign_key_violations"] == 0
                and restored_backup
            ),
            "candidate_integrity_check": candidate_integrity,
            "unchanged_before_replace": unchanged_before_replace,
            "replacement_integrity_check": replacement_integrity["integrity_check"],
            "restored_backup": restored_backup,
        }

    source_hash_after = sha256_file(source)
    source_hash_unchanged = source_hash_before == source_hash_after
    return {
        "temporary_artifacts_removed_after_run": True,
        "source_hash_unchanged": source_hash_unchanged,
        "scenarios": scenarios,
        "all_passed": source_hash_unchanged
        and all(scenario["passed"] for scenario in scenarios.values()),
    }


def run(
    source: Path,
    output_json: Path,
    output_markdown: Path,
    *,
    semester: str | None = None,
    targeted_samples: int | None = None,
    raw_dir: Path | Sequence[Path] | None = None,
    failure_injection: bool = False,
) -> dict[str, Any]:
    source = source.resolve()
    if not source.is_file():
        raise FileNotFoundError(source)
    reader = LegacyReader(source)
    semester = resolve_semester(reader, semester)
    source_hash_before = sha256_file(source)
    legacy, catalog = reader.load_snapshots()
    corrected, correction = corrected_snapshots(
        legacy, catalog, reader.instructor_changes()
    )
    mismatches: dict[str, Any] = {}

    with tempfile.TemporaryDirectory(
        prefix="registrar-checkpointed-state-prototype-"
    ) as directory:
        target = Path(directory) / "PROTOTYPE-wipe-me.db"
        store = CheckpointedStateStore(target)
        sections_by_course: dict[int, list[tuple[int, str]]] = defaultdict(list)
        for row in catalog["sections"]:
            sections_by_course[int(row["course_id"])].append(
                (int(row["section_id"]), row["section_code"])
            )
        for row in catalog["courses"]:
            store.seed_identity(
                course_id=int(row["course_id"]),
                course_code=row["course_code"],
                sections=sections_by_course[int(row["course_id"])],
            )

        import_started = time.perf_counter_ns()
        duplicate_source_ids: list[int] = []
        previous_hash: bytes | None = None
        for snapshot_id, snapshot in corrected:
            state_hash = canonical_state_hash(snapshot)
            if state_hash == previous_hash:
                duplicate_source_ids.append(snapshot_id)
            store.write_snapshot(
                snapshot,
                snapshot_id=snapshot_id,
                last_seen_at=catalog["last_seen_by_snapshot"][snapshot_id],
                preserve_duplicate_import=True,
            )
            previous_hash = state_hash
        latest_id = store.get_latest_snapshot_id()
        if latest_id is None:
            raise ValueError("prototype import produced no snapshots")
        store.force_checkpoint(latest_id)
        store.copy_reporting_log(reader.reporting_rows())
        import_ns = time.perf_counter_ns() - import_started

        reconstructed = list(store.iter_reconstructed_snapshots())
        reconstructed_by_id = dict(reconstructed)
        compatible = [
            (snapshot_id, compatibility_snapshot(snapshot, catalog))
            for snapshot_id, snapshot in reconstructed
        ]
        compatible_by_id = dict(compatible)

        for snapshot_id, expected in legacy:
            actual = compatible_by_id.get(snapshot_id)
            if actual is None:
                _record_mismatch(
                    mismatches, "missing_snapshot", {"snapshot_id": snapshot_id}
                )
                continue
            if actual.to_dict() != expected.to_dict():
                _record_mismatch(
                    mismatches,
                    "snapshot_state",
                    {
                        "snapshot_id": snapshot_id,
                        "timestamp": expected.timestamp,
                    },
                )

        comparator = SnapshotComparator()
        formatter = ReportFormatter()
        corrected_comparison_differences = 0
        for (previous_id, previous), (current_id, current) in pairwise(legacy):
            actual_previous = compatible_by_id.get(previous_id)
            actual_current = compatible_by_id.get(current_id)
            if actual_previous is None or actual_current is None:
                continue
            expected_comparison = comparator.compare_snapshots(current, previous)
            actual_comparison = comparator.compare_snapshots(
                actual_current, actual_previous
            )
            if _normalized_comparison(actual_comparison) != _normalized_comparison(
                expected_comparison
            ):
                _record_mismatch(
                    mismatches,
                    "adjacent_comparison",
                    {
                        "previous_id": previous_id,
                        "current_id": current_id,
                        "expected": _normalized_comparison(expected_comparison),
                        "actual": _normalized_comparison(actual_comparison),
                    },
                )
            expected_report = formatter.format_changes_report(
                expected_comparison, current, previous
            )
            actual_report = formatter.format_changes_report(
                actual_comparison, actual_current, actual_previous
            )
            if actual_report != expected_report:
                _record_mismatch(
                    mismatches,
                    "formatted_report",
                    {"previous_id": previous_id, "current_id": current_id},
                )
            corrected_previous = reconstructed_by_id.get(previous_id)
            corrected_current = reconstructed_by_id.get(current_id)
            if corrected_previous is not None and corrected_current is not None:
                corrected_comparison = comparator.compare_snapshots(
                    corrected_current, corrected_previous
                )
                if _normalized_comparison(
                    corrected_comparison
                ) != _normalized_comparison(expected_comparison):
                    corrected_comparison_differences += 1

        course_codes = [row["course_code"] for row in catalog["courses"]]
        for code in course_codes:
            expected_history = reader.course_history(code, semester)
            actual_history = store.get_course_history(code, semester)
            if expected_history != actual_history:
                _record_mismatch(
                    mismatches,
                    "course_history",
                    {
                        "course_code": code,
                        "expected_rows": len(expected_history),
                        "actual_rows": len(actual_history),
                        "expected_section_prefix": [
                            row["section_code"] for row in expected_history[:20]
                        ],
                        "actual_section_prefix": [
                            row["section_code"] for row in actual_history[:20]
                        ],
                    },
                )

        graph_expected, legacy_query_count = current_graph(source, semester)
        graph_actual = build_graph_from_store(
            store,
            semester=semester,
            instructor_events=_instructor_events(reader, catalog),
            compatibility_instructors=catalog["final_instructors"],
        )
        normalized_expected = normalize_graph(graph_expected)
        normalized_actual = normalize_graph(graph_actual)
        legacy_graph_order_correction = False
        if normalized_expected != normalized_actual:
            graph_chronological = build_graph(
                legacy,
                instructor_events=_instructor_events(reader, catalog),
            )
            if normalize_graph(graph_chronological) == normalized_actual:
                legacy_graph_order_correction = True
            else:
                _record_mismatch(
                    mismatches,
                    "graph_payload",
                    {
                        "expected_courses": len(graph_expected.get("courses", {})),
                        "actual_courses": len(graph_actual.get("courses", {})),
                        "differences": graph_differences(
                            normalized_expected, normalized_actual
                        ),
                    },
                )

        reporting_rows = reader.reporting_rows()
        with store.connection() as connection:
            reporting_count = int(
                connection.execute("SELECT count(*) FROM reporting_log_v2").fetchone()[
                    0
                ]
            )
        if reporting_count != len(reporting_rows):
            _record_mismatch(
                mismatches,
                "reporting_log",
                {"expected": len(reporting_rows), "actual": reporting_count},
            )

        point_replay_mismatches = 0
        for snapshot_id, expected in reconstructed:
            if store.reconstruct_snapshot(snapshot_id).to_dict() != expected.to_dict():
                point_replay_mismatches += 1
        if point_replay_mismatches:
            _record_mismatch(
                mismatches,
                "checkpoint_replay",
                {"count": point_replay_mismatches},
            )

        latest_read_samples = [
            timed(lambda: store.reconstruct_snapshot(latest_id)) for _ in range(20)
        ]
        legacy_latest_samples = [
            timed(lambda: reader.read_snapshot(legacy[-1][0])) for _ in range(20)
        ]
        history_code = course_codes[0]
        prototype_history_samples = [
            timed(lambda: store.get_course_history(history_code, semester))
            for _ in range(5)
        ]
        legacy_history_samples = [
            timed(lambda: reader.course_history(history_code, semester))
            for _ in range(20)
        ]
        graph_prototype_samples = [
            timed(
                lambda: build_graph_from_store(
                    store,
                    semester=semester,
                    instructor_events=_instructor_events(reader, catalog),
                )
            )
            for _ in range(5)
        ]
        graph_legacy_samples = [
            timed(lambda: current_graph(source, semester)) for _ in range(5)
        ]
        legacy_reporting_samples = [
            timed(lambda: _format_all_reports(legacy)) for _ in range(5)
        ]
        prototype_reporting_samples = [
            timed(lambda: _format_all_reports(compatible)) for _ in range(5)
        ]
        write_benchmark = _benchmark_writes(
            source, target, reconstructed[-1][1], semester
        )
        targeted_performance = (
            benchmark_targeted_performance(
                target,
                reconstructed[-1][1],
                samples=targeted_samples,
                warmup=min(10, targeted_samples),
            )
            if targeted_samples is not None
            else None
        )
        raw_instructor_evidence = (
            evaluate_raw_instructor_evidence(
                raw_dir,
                semester,
                catalog,
                [],
                snapshot_timestamps=[snapshot.timestamp for _, snapshot in legacy],
                legacy_snapshots=legacy,
            )
            if raw_dir is not None
            else None
        )
        recovery_evidence = (
            evaluate_failure_injection(source, target, reconstructed[-1][1])
            if failure_injection
            else None
        )

        with store.connection() as connection:
            target_last_seen_by_snapshot = {
                int(row["snapshot_id"]): str(row["last_seen_at"])
                for row in connection.execute(
                    "SELECT snapshot_id, last_seen_at FROM state_snapshot"
                )
            }
            freshness_preserved = (
                target_last_seen_by_snapshot == catalog["last_seen_by_snapshot"]
            )
            connection.execute("VACUUM")
        compacted_bytes = target.stat().st_size
        integrity = store.integrity()
        stats = store.statistics()
        max_replay = store.max_replay_distance()
        source_hash_after = sha256_file(source)
        semantic_mismatches = {
            "consecutive_duplicate_states_require_backfill_exception": {
                "count": len(duplicate_source_ids),
                "samples": duplicate_source_ids[:MAX_SAMPLES],
                "impact": (
                    "Preserving legacy snapshot/reporting IDs conflicts with the "
                    "ADR rule that consecutive identical states create no snapshot."
                ),
            }
        }
        if legacy_graph_order_correction:
            semantic_mismatches["legacy_graph_history_order_requires_correction"] = {
                "count": 1,
                "samples": [],
                "impact": (
                    "The legacy website orders enrollment rows by snapshot ID before "
                    "compaction even though snapshot IDs are not chronological. The "
                    "prototype preserves timestamp order and intentionally corrects "
                    "the malformed historical graph."
                ),
            }

        gates = {
            "zero_unexplained_mismatches": sum(
                value["count"] for value in mismatches.values()
            )
            == 0,
            "source_hash_unchanged": source_hash_before == source_hash_after,
            "integrity_check": integrity["integrity_check"] == "ok",
            "foreign_key_check": integrity["foreign_key_violations"] == 0,
            "legacy_freshness_preserved": freshness_preserved,
            "storage_le_3_5_mb": compacted_bytes <= 3_500_000,
            "unchanged_write_p95_le_5_ms": (
                write_benchmark["prototype"]["unchanged_write"]["p95_ns"] <= 5_000_000
            ),
            "changed_write_p95_le_10_ms": (
                write_benchmark["prototype"]["changed_write"]["p95_ns"] <= 10_000_000
            ),
            "unchanged_write_zero_growth": (
                write_benchmark["prototype"]["unchanged_bytes_added"] == 0
            ),
            "graph_p95_le_250_ms": summary(graph_prototype_samples)["p95_ns"]
            <= 250_000_000,
            "graph_queries_le_25": 5 <= 25,
        }
        if targeted_performance is not None:
            gates["targeted_changed_write_p95_le_10_ms"] = (
                targeted_performance["changed_write"]["p95_ns"] <= 10_000_000
            )
        if raw_instructor_evidence is not None:
            coverage = raw_instructor_evidence["snapshot_coverage"]
            gates["raw_xls_snapshot_coverage_complete"] = (
                coverage is not None
                and coverage["unmatched"] == 0
                and coverage["multiply_matched"] == 0
                and raw_instructor_evidence["parse_failure_count"] == 0
            )
            raw_parity = raw_instructor_evidence["raw_snapshot_parity"]
            gates["raw_xls_legacy_comparison_complete"] = (
                raw_parity is not None
                and raw_parity["snapshots_compared"] == coverage["snapshots"]
            )
        if recovery_evidence is not None:
            gates["failure_injection_recovery"] = recovery_evidence["all_passed"]
        fatal = (
            len(reconstructed) != len(legacy)
            or not gates["source_hash_unchanged"]
            or not gates["integrity_check"]
            or not gates["foreign_key_check"]
        )
        if fatal:
            recommendation = "reject"
        elif all(gates.values()):
            recommendation = "proceed"
        else:
            recommendation = "revise"

        result = {
            "format": 1,
            "scope": "ADR-0001 data model only",
            "source": {
                "path_classification": "local_runtime_copy",
                "sha256_before": source_hash_before,
                "sha256_after": source_hash_after,
                "bytes": source.stat().st_size,
                "semester": semester,
                "snapshots": len(legacy),
                "has_last_seen_at": reader.has_column("snapshots", "last_seen_at"),
                "freshness": catalog["freshness"],
            },
            "prototype": {
                "temporary_database_removed_after_run": True,
                "import_ns": import_ns,
                "compacted_bytes": compacted_bytes,
                "statistics": stats,
                "max_replay_distance": max_replay,
                "duplicate_source_snapshot_count": len(duplicate_source_ids),
                "duplicate_source_snapshot_ids": duplicate_source_ids[:MAX_SAMPLES],
                "integrity": integrity,
            },
            "equivalence": {
                "snapshots_checked": len(legacy),
                "adjacent_pairs_checked": max(0, len(legacy) - 1),
                "course_histories_checked": len(course_codes),
                "reporting_rows_checked": len(reporting_rows),
                "checkpoint_point_reads_checked": len(reconstructed),
                "mismatches": mismatches,
                "semantic_mismatches": semantic_mismatches,
            },
            "corrected_metadata": {
                **correction,
                "adjacent_comparison_differences": corrected_comparison_differences,
            },
            "benchmarks": {
                "legacy": {
                    "latest_read": summary(legacy_latest_samples),
                    "course_history": summary(legacy_history_samples),
                    "graph_generation": summary(graph_legacy_samples),
                    "adjacent_reporting": summary(legacy_reporting_samples),
                    "graph_query_count": legacy_query_count,
                    **write_benchmark["legacy"],
                },
                "prototype": {
                    "latest_read": summary(latest_read_samples),
                    "course_history": summary(prototype_history_samples),
                    "graph_generation": summary(graph_prototype_samples),
                    "adjacent_reporting": summary(prototype_reporting_samples),
                    "graph_query_count": 5,
                    **write_benchmark["prototype"],
                },
            },
            "targeted_evidence": {
                "performance": targeted_performance,
                "raw_instructors": raw_instructor_evidence,
                "failure_injection": recovery_evidence,
            },
            "gates": gates,
            "unevaluated": (
                ([] if raw_dir is not None else ["raw XLS timestamp coverage"])
                + [
                    "static manifest and blob publication",
                    "browser delivery",
                    "production migration rollback and cutover",
                    "production activation",
                ]
            ),
            "recommendation": recommendation,
        }

    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(
        json.dumps(result, indent=2, sort_keys=True, default=_json_default) + "\n"
    )
    output_markdown.write_text(render_markdown(result))
    return result


def _milliseconds(value: int) -> str:
    return f"{value / 1_000_000:.2f} ms"


def render_markdown(result: dict[str, Any]) -> str:
    mismatch_count = sum(
        item["count"] for item in result["equivalence"]["mismatches"].values()
    )
    lines = [
        f"# Checkpointed Enrollment-State Prototype — {result['source']['semester']}",
        "",
        f"**Recommendation: {result['recommendation'].upper()}**",
        "",
        "## Result",
        "",
        f"- Snapshots checked: {result['equivalence']['snapshots_checked']}",
        f"- Adjacent comparisons/reports: {result['equivalence']['adjacent_pairs_checked']}",
        f"- Course histories: {result['equivalence']['course_histories_checked']}",
        f"- Unexplained mismatches: {mismatch_count}",
        f"- Corrected-history comparison differences: {result['corrected_metadata']['adjacent_comparison_differences']}",
        f"- Ambiguous instructor mappings: {result['corrected_metadata']['ambiguous_mappings']}",
        f"- Compacted prototype size: {result['prototype']['compacted_bytes']:,} bytes",
        f"- Maximum checkpoint replay distance: {result['prototype']['max_replay_distance']} states",
        "",
        "## Gates",
        "",
    ]
    lines.extend(
        f"- {'PASS' if passed else 'FAIL'} — `{name}`"
        for name, passed in result["gates"].items()
    )
    lines.extend(["", "## Legacy versus prototype", ""])
    for operation in (
        "latest_read",
        "course_history",
        "unchanged_write",
        "changed_write",
        "adjacent_reporting",
        "graph_generation",
    ):
        old = result["benchmarks"]["legacy"][operation]["p95_ns"]
        new = result["benchmarks"]["prototype"][operation]["p95_ns"]
        lines.append(
            f"- {operation}: legacy {_milliseconds(old)}, "
            f"prototype {_milliseconds(new)}"
        )
    lines.extend(["", "## Semantic mismatches", ""])
    for category, detail in result["equivalence"]["semantic_mismatches"].items():
        lines.append(f"- `{category}`: {detail['count']} — {detail['impact']}")
    if result["equivalence"]["mismatches"]:
        lines.extend(["", "### Unexplained parity mismatches", ""])
        for category, detail in result["equivalence"]["mismatches"].items():
            lines.append(f"- `{category}`: {detail['count']}")
    lines.extend(["", "## Evidence limitations", ""])
    targeted = result.get("targeted_evidence", {})
    raw_targeted = targeted.get("raw_instructors")
    if not result["source"]["has_last_seen_at"]:
        lines.append("- The source database has no historical `last_seen_at` column.")
    else:
        lines.append(
            "- Preserved source `last_seen_at`; values later than `observed_at`: "
            f"{result['source']['freshness']['values_later_than_observed_at']}."
        )
    lines.append("- Legacy catalog metadata is mutable and temporally smeared.")
    if raw_targeted is None:
        lines.append("- Raw XLS coverage was intentionally not evaluated.")
    else:
        coverage = raw_targeted["snapshot_coverage"]
        lines.append(
            "- Raw XLS timestamp coverage: "
            f"{coverage['matched']} of {coverage['snapshots']} snapshots matched."
        )
    lines.extend(
        [
            "- Ambiguous instructor-event mappings are reported, not silently accepted.",
            "",
            "## Unevaluated",
            "",
        ]
    )
    lines.extend(f"- {item}" for item in result["unevaluated"])
    if any(value is not None for value in targeted.values()):
        lines.extend(["", "## Targeted prototype revisions", ""])
        performance = targeted.get("performance")
        if performance is not None:
            lines.extend(
                [
                    (
                        f"- Performance samples: {performance['samples']} "
                        f"after {performance['warmup']} warm-up operations"
                    ),
                    (
                        "- Targeted changed-write p95: "
                        f"{_milliseconds(performance['changed_write']['p95_ns'])}"
                    ),
                    (
                        "- Targeted latest-read p95: "
                        f"{_milliseconds(performance['latest_read']['p95_ns'])}"
                    ),
                    "- Query plans and per-phase p95 timings are recorded in JSON.",
                ]
            )
        raw = targeted.get("raw_instructors")
        if raw is not None:
            lines.extend(
                [
                    f"- Raw XLS files scanned: {raw['files_scanned']}",
                    f"- Semester-matched raw files: {raw['semester_files']}",
                    f"- Exact duplicate raw files ignored: {raw['duplicate_files']}",
                    (
                        "- Conflicting raw timestamps: "
                        f"{raw['conflicting_timestamp_count']}"
                    ),
                    (
                        "- Raw/legacy invariant mismatches: "
                        f"{raw['raw_snapshot_parity']['mismatches']}"
                    ),
                    (
                        "- Raw-derived instructor transitions: "
                        f"{raw['raw_derived_instructor_transitions']}"
                    ),
                    (
                        "- Legacy instructor-change rows were not used as temporal "
                        "migration evidence."
                    ),
                ]
            )
        recovery = targeted.get("failure_injection")
        if recovery is not None:
            lines.append(
                "- Failure-injection scenarios: "
                f"{'PASS' if recovery['all_passed'] else 'FAIL'}"
            )
    return "\n".join(lines) + "\n"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument(
        "--semester",
        help="Semester to evaluate; inferred when the source contains exactly one",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "output" / "checkpointed-state-prototype.json",
    )
    parser.add_argument(
        "--markdown",
        type=Path,
        default=ROOT / "output" / "checkpointed-state-prototype.md",
    )
    parser.add_argument(
        "--targeted-samples",
        type=int,
        help="Run targeted changed-write/latest-read profiling with this sample count",
    )
    parser.add_argument(
        "--raw-dir",
        type=Path,
        action="append",
        help=(
            "Recursively scan retained raw XLS files; repeat for multiple source "
            "directories"
        ),
    )
    parser.add_argument(
        "--failure-injection",
        action="store_true",
        help="Exercise disposable migration-boundary recovery scenarios",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    result = run(
        args.database,
        args.output,
        args.markdown,
        semester=args.semester,
        targeted_samples=args.targeted_samples,
        raw_dir=args.raw_dir,
        failure_injection=args.failure_injection,
    )
    print(
        json.dumps(
            {
                "recommendation": result["recommendation"],
                "json": str(args.output),
                "markdown": str(args.markdown),
            }
        )
    )


if __name__ == "__main__":
    main()
