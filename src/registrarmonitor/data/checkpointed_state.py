"""Checkpointed enrollment state, append-only events, and bounded replay."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Iterator
from contextlib import contextmanager, nullcontext
from dataclasses import dataclass
from pathlib import Path
from time import perf_counter_ns
from typing import Any

from registrarmonitor.models import Course, EnrollmentSnapshot, Section

from .instructor_normalization import clean_instructor_text, instructor_identity

STATE_THRESHOLD = 96
EVENT_THRESHOLD = 2_048


@dataclass(frozen=True)
class WriteResult:
    snapshot_id: int
    created: bool
    course_events: int
    section_events: int
    checkpoint_created: bool


def _text(value: str | None) -> str:
    return value.strip() if value else ""


def _snapshot_payload(snapshot: EnrollmentSnapshot) -> dict[str, Any]:
    return {
        "semester": snapshot.semester,
        "overall_fill": snapshot.overall_fill,
        "courses": [
            {
                "course_code": code,
                "title": _text(course.course_title),
                "department": _text(course.department),
                "sections": [
                    {
                        "section_code": section_code,
                        "section_type": _text(section.section_type),
                        "enrollment_count": section.enrollment,
                        "capacity_count": section.capacity,
                        "instructor": instructor_identity(section.instructor),
                    }
                    for section_code, section in sorted(course.sections.items())
                ],
            }
            for code, course in sorted(snapshot.courses.items())
        ],
    }


def canonical_state_hash(snapshot: EnrollmentSnapshot) -> bytes:
    encoded = json.dumps(
        _snapshot_payload(snapshot),
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return hashlib.sha256(encoded).digest()


class CheckpointedStateStore:
    """Small public surface around the ADR-0001 production data model."""

    def __init__(
        self,
        path: Path | str,
        *,
        initialize: bool = True,
        set_user_version: bool = True,
    ):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._course_ids: dict[str, int] = {}
        self._section_ids: dict[tuple[str, str], int] = {}
        self._latest_state_cache: (
            tuple[
                int | None,
                bytes | None,
                dict[str, tuple[str, str]],
                dict[tuple[str, str], tuple[str, int, int, str]],
            ]
            | None
        ) = None
        if initialize:
            self._initialize(set_user_version=set_user_version)

    @contextmanager
    def connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.path)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        try:
            yield connection
        finally:
            connection.close()

    def _initialize(self, *, set_user_version: bool = True) -> None:
        with self.connection() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS course_catalog (
                    course_id INTEGER PRIMARY KEY,
                    course_code TEXT NOT NULL UNIQUE
                );
                CREATE TABLE IF NOT EXISTS section_catalog (
                    section_id INTEGER PRIMARY KEY,
                    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
                    section_code TEXT NOT NULL,
                    UNIQUE(course_id, section_code)
                );
                CREATE TABLE IF NOT EXISTS state_snapshot (
                    snapshot_id INTEGER PRIMARY KEY,
                    sequence_no INTEGER NOT NULL UNIQUE,
                    observed_at TEXT NOT NULL UNIQUE,
                    last_seen_at TEXT NOT NULL,
                    semester TEXT NOT NULL,
                    overall_fill REAL NOT NULL,
                    state_hash BLOB NOT NULL CHECK(length(state_hash) = 32),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE TABLE IF NOT EXISTS course_latest_state (
                    course_id INTEGER PRIMARY KEY REFERENCES course_catalog(course_id),
                    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
                    title TEXT,
                    department TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS section_latest_state (
                    section_id INTEGER PRIMARY KEY REFERENCES section_catalog(section_id),
                    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
                    section_type TEXT NOT NULL,
                    enrollment_count INTEGER NOT NULL CHECK(enrollment_count >= 0),
                    capacity_count INTEGER NOT NULL CHECK(capacity_count > 0),
                    instructor TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS course_change_event (
                    event_id INTEGER PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
                    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
                    event_kind TEXT NOT NULL
                        CHECK(event_kind IN ('ADD', 'UPDATE', 'REMOVE')),
                    old_title TEXT,
                    new_title TEXT,
                    old_department TEXT,
                    new_department TEXT,
                    UNIQUE(snapshot_id, course_id)
                );
                CREATE TABLE IF NOT EXISTS section_change_event (
                    event_id INTEGER PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL REFERENCES state_snapshot(snapshot_id),
                    section_id INTEGER NOT NULL REFERENCES section_catalog(section_id),
                    event_kind TEXT NOT NULL
                        CHECK(event_kind IN ('ADD', 'UPDATE', 'REMOVE')),
                    old_section_type TEXT,
                    new_section_type TEXT,
                    old_enrollment_count INTEGER,
                    new_enrollment_count INTEGER,
                    old_capacity_count INTEGER,
                    new_capacity_count INTEGER,
                    old_instructor TEXT,
                    new_instructor TEXT,
                    UNIQUE(snapshot_id, section_id)
                );
                CREATE TABLE IF NOT EXISTS state_checkpoint (
                    checkpoint_id INTEGER PRIMARY KEY,
                    snapshot_id INTEGER NOT NULL UNIQUE
                        REFERENCES state_snapshot(snapshot_id)
                );
                CREATE TABLE IF NOT EXISTS course_checkpoint_state (
                    checkpoint_id INTEGER NOT NULL
                        REFERENCES state_checkpoint(checkpoint_id),
                    course_id INTEGER NOT NULL REFERENCES course_catalog(course_id),
                    title TEXT,
                    department TEXT NOT NULL,
                    PRIMARY KEY(checkpoint_id, course_id)
                );
                CREATE TABLE IF NOT EXISTS section_checkpoint_state (
                    checkpoint_id INTEGER NOT NULL
                        REFERENCES state_checkpoint(checkpoint_id),
                    section_id INTEGER NOT NULL REFERENCES section_catalog(section_id),
                    section_type TEXT NOT NULL,
                    enrollment_count INTEGER NOT NULL CHECK(enrollment_count >= 0),
                    capacity_count INTEGER NOT NULL CHECK(capacity_count > 0),
                    instructor TEXT NOT NULL,
                    PRIMARY KEY(checkpoint_id, section_id)
                );
                CREATE TABLE IF NOT EXISTS reporting_log_v2 (
                    report_id INTEGER PRIMARY KEY,
                    reported_snapshot_id INTEGER NOT NULL
                        REFERENCES state_snapshot(snapshot_id),
                    report_timestamp TEXT NOT NULL,
                    changes_found INTEGER NOT NULL CHECK(changes_found IN (0, 1)),
                    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
                );
                CREATE INDEX IF NOT EXISTS idx_course_change_event_course_snapshot
                    ON course_change_event(course_id, snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_section_change_event_section_snapshot
                    ON section_change_event(section_id, snapshot_id);
                CREATE INDEX IF NOT EXISTS idx_reporting_log_v2_timestamp
                    ON reporting_log_v2(report_timestamp);
                CREATE INDEX IF NOT EXISTS idx_reporting_log_v2_snapshot
                    ON reporting_log_v2(reported_snapshot_id);
                """
            )
            if set_user_version:
                connection.execute("PRAGMA user_version = 2")
            connection.commit()

    @staticmethod
    def _course_state(snapshot: EnrollmentSnapshot) -> dict[str, tuple[str, str]]:
        return {
            code: (_text(course.course_title), _text(course.department))
            for code, course in snapshot.courses.items()
        }

    @staticmethod
    def _section_state(
        snapshot: EnrollmentSnapshot,
    ) -> dict[tuple[str, str], tuple[str, int, int, str]]:
        return {
            (course_code, section_code): (
                _text(section.section_type),
                section.enrollment,
                section.capacity,
                clean_instructor_text(section.instructor),
            )
            for course_code, course in snapshot.courses.items()
            for section_code, section in course.sections.items()
        }

    @staticmethod
    def _section_identity_state(
        sections: dict[tuple[str, str], tuple[str, int, int, str]],
    ) -> dict[tuple[str, str], tuple[str, int, int, str]]:
        """Return section state with presentation-only instructor details removed."""
        return {
            key: (*state[:3], instructor_identity(state[3]))
            for key, state in sections.items()
        }

    @staticmethod
    def _validate_event(
        kind: str,
        old: tuple[Any, ...] | None,
        new: tuple[Any, ...] | None,
    ) -> None:
        valid = (
            (kind == "ADD" and old is None and new is not None)
            or (kind == "REMOVE" and old is not None and new is None)
            or (kind == "UPDATE" and old is not None and new is not None and old != new)
        )
        if not valid:
            raise ValueError(f"invalid {kind} event shape: old={old!r}, new={new!r}")

    @staticmethod
    def _kind(old: tuple[Any, ...] | None, new: tuple[Any, ...] | None) -> str:
        if old is None:
            return "ADD"
        if new is None:
            return "REMOVE"
        return "UPDATE"

    def _latest_states(
        self, connection: sqlite3.Connection
    ) -> tuple[
        dict[str, tuple[str, str]],
        dict[tuple[str, str], tuple[str, int, int, str]],
    ]:
        courses = {
            row["course_code"]: (row["title"] or "", row["department"])
            for row in connection.execute(
                """
                SELECT c.course_code, l.title, l.department
                FROM course_latest_state l
                JOIN course_catalog c ON c.course_id = l.course_id
                """
            )
        }
        sections = {
            (row["course_code"], row["section_code"]): (
                row["section_type"],
                row["enrollment_count"],
                row["capacity_count"],
                row["instructor"],
            )
            for row in connection.execute(
                """
                SELECT c.course_code, s.section_code, l.section_type,
                       l.enrollment_count, l.capacity_count, l.instructor
                FROM section_latest_state l
                JOIN section_catalog s ON s.section_id = l.section_id
                JOIN course_catalog c ON c.course_id = s.course_id
                """
            )
        }
        return courses, sections

    def _ensure_course(
        self,
        connection: sqlite3.Connection,
        course_code: str,
        *,
        identity: int | None = None,
        identity_cache: dict[str, int] | None = None,
    ) -> int:
        cache = self._course_ids if identity_cache is None else identity_cache
        cached = cache.get(course_code)
        if cached is not None:
            return cached
        row = connection.execute(
            "SELECT course_id FROM course_catalog WHERE course_code = ?",
            (course_code,),
        ).fetchone()
        if row:
            course_id = int(row[0])
            cache[course_code] = course_id
            return course_id
        if identity is None:
            cursor = connection.execute(
                "INSERT INTO course_catalog(course_code) VALUES (?)",
                (course_code,),
            )
        else:
            cursor = connection.execute(
                "INSERT INTO course_catalog(course_id, course_code) VALUES (?, ?)",
                (identity, course_code),
            )
        if cursor.lastrowid is None:
            raise sqlite3.Error("course identity insert returned no ID")
        course_id = int(cursor.lastrowid)
        cache[course_code] = course_id
        return course_id

    def _ensure_section(
        self,
        connection: sqlite3.Connection,
        course_code: str,
        section_code: str,
        *,
        course_id: int | None = None,
        identity: int | None = None,
        course_identity_cache: dict[str, int] | None = None,
        section_identity_cache: dict[tuple[str, str], int] | None = None,
    ) -> int:
        course_cache = (
            self._course_ids if course_identity_cache is None else course_identity_cache
        )
        section_cache = (
            self._section_ids
            if section_identity_cache is None
            else section_identity_cache
        )
        cache_key = (course_code, section_code)
        cached = section_cache.get(cache_key)
        if cached is not None:
            return cached
        if course_id is None:
            course_id = self._ensure_course(
                connection,
                course_code,
                identity_cache=course_cache,
            )
        row = connection.execute(
            """
            SELECT section_id FROM section_catalog
            WHERE course_id = ? AND section_code = ?
            """,
            (course_id, section_code),
        ).fetchone()
        if row:
            section_id = int(row[0])
            section_cache[cache_key] = section_id
            return section_id
        if identity is None:
            cursor = connection.execute(
                """
                INSERT INTO section_catalog(course_id, section_code)
                VALUES (?, ?)
                """,
                (course_id, section_code),
            )
        else:
            cursor = connection.execute(
                """
                INSERT INTO section_catalog(section_id, course_id, section_code)
                VALUES (?, ?, ?)
                """,
                (identity, course_id, section_code),
            )
        if cursor.lastrowid is None:
            raise sqlite3.Error("section identity insert returned no ID")
        section_id = int(cursor.lastrowid)
        section_cache[cache_key] = section_id
        return section_id

    def resolve_course_id(
        self, connection: sqlite3.Connection, course_code: str
    ) -> int:
        """Resolve one catalog identity without scanning the full catalog."""
        return self._ensure_course(connection, course_code)

    def resolve_section_id(
        self,
        connection: sqlite3.Connection,
        course_code: str,
        section_code: str,
    ) -> int:
        """Resolve one section identity, creating only a missing catalog row."""
        course_id = self._ensure_course(connection, course_code)
        return self._ensure_section(
            connection,
            course_code,
            section_code,
            course_id=course_id,
        )

    def invalidate_runtime_caches(self) -> None:
        """Forget transaction-local identity and latest-state observations."""
        self._course_ids.clear()
        self._section_ids.clear()
        self._latest_state_cache = None

    def accept_committed_snapshot(
        self, snapshot: EnrollmentSnapshot, result: WriteResult
    ) -> None:
        """Publish a successful external transaction to the warm write cache."""
        if not result.created:
            return
        self._latest_state_cache = (
            result.snapshot_id,
            canonical_state_hash(snapshot),
            self._course_state(snapshot),
            self._section_state(snapshot),
        )

    def latest_states_in_transaction(
        self, connection: sqlite3.Connection
    ) -> tuple[
        dict[str, tuple[str, str]],
        dict[tuple[str, str], tuple[str, int, int, str]],
    ]:
        """Return the cached latest state, loading it once when necessary."""
        latest = connection.execute(
            """
            SELECT snapshot_id, state_hash
            FROM state_snapshot
            ORDER BY sequence_no DESC LIMIT 1
            """
        ).fetchone()
        courses, sections = self._cached_latest_states(connection, latest)
        return dict(courses), dict(sections)

    @staticmethod
    def latest_snapshot_id_in_transaction(
        connection: sqlite3.Connection,
    ) -> int | None:
        """Return the authoritative predecessor ID without reconstructing it."""
        row = connection.execute(
            """
            SELECT snapshot_id
            FROM state_snapshot
            ORDER BY sequence_no DESC LIMIT 1
            """
        ).fetchone()
        return int(row[0]) if row else None

    def _cached_latest_states(
        self,
        connection: sqlite3.Connection,
        latest: sqlite3.Row | None,
    ) -> tuple[
        dict[str, tuple[str, str]],
        dict[tuple[str, str], tuple[str, int, int, str]],
    ]:
        if latest is None:
            self._latest_state_cache = (None, None, {}, {})
            return {}, {}

        snapshot_id = int(latest["snapshot_id"])
        state_hash = bytes(latest["state_hash"])
        cached = self._latest_state_cache
        if cached is not None and cached[0] == snapshot_id and cached[1] == state_hash:
            return cached[2], cached[3]

        courses, sections = self._latest_states(connection)
        self._latest_state_cache = (snapshot_id, state_hash, courses, sections)
        return courses, sections

    def seed_identity(
        self,
        *,
        course_id: int,
        course_code: str,
        sections: list[tuple[int, str]],
    ) -> None:
        """Pre-seed legacy identities before chronological import."""
        with self.connection() as connection:
            with connection:
                actual_course_id = self._ensure_course(
                    connection, course_code, identity=course_id
                )
                if actual_course_id != course_id:
                    raise ValueError(f"course identity mismatch for {course_code}")
                for section_id, section_code in sections:
                    actual_section_id = self._ensure_section(
                        connection,
                        course_code,
                        section_code,
                        course_id=course_id,
                        identity=section_id,
                    )
                    if actual_section_id != section_id:
                        raise ValueError(
                            f"section identity mismatch for {course_code}/{section_code}"
                        )

    def write_snapshot(
        self,
        snapshot: EnrollmentSnapshot,
        *,
        snapshot_id: int | None = None,
        last_seen_at: str | None = None,
        force_checkpoint: bool = False,
        preserve_duplicate_import: bool = False,
        before_commit: Callable[[sqlite3.Connection, int], None] | None = None,
    ) -> WriteResult:
        return self._write_snapshot(
            snapshot,
            snapshot_id=snapshot_id,
            last_seen_at=last_seen_at,
            force_checkpoint=force_checkpoint,
            preserve_duplicate_import=preserve_duplicate_import,
            before_commit=before_commit,
        )

    def write_snapshot_in_transaction(
        self,
        connection: sqlite3.Connection,
        snapshot: EnrollmentSnapshot,
        *,
        snapshot_id: int | None = None,
        last_seen_at: str | None = None,
        force_checkpoint: bool = False,
        preserve_duplicate_import: bool = False,
        before_commit: Callable[[sqlite3.Connection, int], None] | None = None,
    ) -> WriteResult:
        """Write v2 state using the caller's active SQLite transaction."""
        return self._write_snapshot(
            snapshot,
            snapshot_id=snapshot_id,
            last_seen_at=last_seen_at,
            force_checkpoint=force_checkpoint,
            preserve_duplicate_import=preserve_duplicate_import,
            before_commit=before_commit,
            connection=connection,
        )

    def profile_write_snapshot(
        self,
        snapshot: EnrollmentSnapshot,
        *,
        snapshot_id: int | None = None,
        last_seen_at: str | None = None,
        force_checkpoint: bool = False,
        preserve_duplicate_import: bool = False,
    ) -> tuple[WriteResult, dict[str, Any]]:
        """Write one state and expose coarse transaction phase timings."""
        profile: dict[str, Any] = {
            "phases_ns": {
                phase: 0
                for phase in (
                    "canonicalize",
                    "load_current",
                    "append_events",
                    "replace_latest",
                    "checkpoint",
                    "commit",
                )
            },
            "latest_projection_mutations": {"courses": 0, "sections": 0},
        }
        started = perf_counter_ns()
        result = self._write_snapshot(
            snapshot,
            snapshot_id=snapshot_id,
            last_seen_at=last_seen_at,
            force_checkpoint=force_checkpoint,
            preserve_duplicate_import=preserve_duplicate_import,
            profile=profile,
        )
        profile["total_ns"] = perf_counter_ns() - started
        return result, profile

    def _write_snapshot(
        self,
        snapshot: EnrollmentSnapshot,
        *,
        snapshot_id: int | None = None,
        last_seen_at: str | None = None,
        force_checkpoint: bool = False,
        preserve_duplicate_import: bool = False,
        profile: dict[str, Any] | None = None,
        before_commit: Callable[[sqlite3.Connection, int], None] | None = None,
        connection: sqlite3.Connection | None = None,
    ) -> WriteResult:
        phase_started = perf_counter_ns()
        state_hash = canonical_state_hash(snapshot)
        current_courses = self._course_state(snapshot)
        current_sections = self._section_state(snapshot)
        if profile is not None:
            profile["phases_ns"]["canonicalize"] = perf_counter_ns() - phase_started
        owns_connection = connection is None
        course_identity_cache = self._course_ids if owns_connection else {}
        section_identity_cache = self._section_ids if owns_connection else {}
        connection_context = (
            self.connection() if owns_connection else nullcontext(connection)
        )
        with connection_context as connection:
            if connection is None:
                raise sqlite3.Error("checkpointed-state connection is unavailable")
            try:
                if owns_connection:
                    connection.execute("BEGIN IMMEDIATE")
                phase_started = perf_counter_ns()
                latest = connection.execute(
                    """
                    SELECT snapshot_id, state_hash, semester, overall_fill
                    FROM state_snapshot
                    ORDER BY sequence_no DESC LIMIT 1
                    """
                ).fetchone()
                old_courses, old_sections = self._cached_latest_states(
                    connection, latest
                )
                old_section_identities = self._section_identity_state(old_sections)
                current_section_identities = self._section_identity_state(
                    current_sections
                )
                semantically_identical = (
                    latest is not None
                    and latest["semester"] == snapshot.semester
                    and latest["overall_fill"] == snapshot.overall_fill
                    and old_courses == current_courses
                    and old_section_identities == current_section_identities
                )
                if (
                    latest is not None
                    and (
                        bytes(latest["state_hash"]) == state_hash
                        or semantically_identical
                    )
                    and not preserve_duplicate_import
                ):
                    connection.execute(
                        """
                        UPDATE state_snapshot SET last_seen_at = ?
                        WHERE snapshot_id = ?
                        """,
                        (last_seen_at or snapshot.timestamp, latest["snapshot_id"]),
                    )
                    if owns_connection:
                        connection.commit()
                    return WriteResult(
                        snapshot_id=int(latest["snapshot_id"]),
                        created=False,
                        course_events=0,
                        section_events=0,
                        checkpoint_created=False,
                    )

                sequence_no = int(
                    connection.execute(
                        "SELECT COALESCE(MAX(sequence_no), 0) + 1 FROM state_snapshot"
                    ).fetchone()[0]
                )
                if profile is not None:
                    profile["phases_ns"]["load_current"] = (
                        perf_counter_ns() - phase_started
                    )
                phase_started = perf_counter_ns()
                if snapshot_id is None:
                    cursor = connection.execute(
                        """
                        INSERT INTO state_snapshot(
                            sequence_no, observed_at, last_seen_at, semester,
                            overall_fill, state_hash
                        ) VALUES (?, ?, ?, ?, ?, ?)
                        """,
                        (
                            sequence_no,
                            snapshot.timestamp,
                            last_seen_at or snapshot.timestamp,
                            snapshot.semester,
                            snapshot.overall_fill,
                            state_hash,
                        ),
                    )
                    if cursor.lastrowid is None:
                        raise sqlite3.Error("snapshot insert returned no ID")
                    new_snapshot_id = int(cursor.lastrowid)
                else:
                    connection.execute(
                        """
                        INSERT INTO state_snapshot(
                            snapshot_id, sequence_no, observed_at, last_seen_at,
                            semester, overall_fill, state_hash
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            snapshot_id,
                            sequence_no,
                            snapshot.timestamp,
                            last_seen_at or snapshot.timestamp,
                            snapshot.semester,
                            snapshot.overall_fill,
                            state_hash,
                        ),
                    )
                    new_snapshot_id = snapshot_id

                course_changes = [
                    (code, old_courses.get(code), current_courses.get(code))
                    for code in sorted(set(old_courses) | set(current_courses))
                    if old_courses.get(code) != current_courses.get(code)
                ]
                section_changes = [
                    (key, old_sections.get(key), current_sections.get(key))
                    for key in sorted(set(old_sections) | set(current_sections))
                    if old_section_identities.get(key)
                    != current_section_identities.get(key)
                ]
                course_ids: dict[str, int] = {}
                for code, _, _ in course_changes:
                    course_ids[code] = self._ensure_course(
                        connection,
                        code,
                        identity_cache=course_identity_cache,
                    )
                section_ids: dict[tuple[str, str], int] = {}
                for (code, section_code), _, _ in section_changes:
                    course_id = course_ids.setdefault(
                        code,
                        self._ensure_course(
                            connection,
                            code,
                            identity_cache=course_identity_cache,
                        ),
                    )
                    section_ids[(code, section_code)] = self._ensure_section(
                        connection,
                        code,
                        section_code,
                        course_id=course_id,
                        course_identity_cache=course_identity_cache,
                        section_identity_cache=section_identity_cache,
                    )

                course_event_count = 0
                for code, old, new in course_changes:
                    kind = self._kind(old, new)
                    self._validate_event(kind, old, new)
                    course_id = course_ids[code]
                    connection.execute(
                        """
                        INSERT INTO course_change_event(
                            snapshot_id, course_id, event_kind,
                            old_title, new_title, old_department, new_department
                        ) VALUES (?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_snapshot_id,
                            course_id,
                            kind,
                            old[0] if old else None,
                            new[0] if new else None,
                            old[1] if old else None,
                            new[1] if new else None,
                        ),
                    )
                    course_event_count += 1

                section_event_count = 0
                for key, old, new in section_changes:
                    kind = self._kind(old, new)
                    self._validate_event(kind, old, new)
                    section_id = section_ids[key]
                    connection.execute(
                        """
                        INSERT INTO section_change_event(
                            snapshot_id, section_id, event_kind,
                            old_section_type, new_section_type,
                            old_enrollment_count, new_enrollment_count,
                            old_capacity_count, new_capacity_count,
                            old_instructor, new_instructor
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            new_snapshot_id,
                            section_id,
                            kind,
                            old[0] if old else None,
                            new[0] if new else None,
                            old[1] if old else None,
                            new[1] if new else None,
                            old[2] if old else None,
                            new[2] if new else None,
                            old[3] if old else None,
                            new[3] if new else None,
                        ),
                    )
                    section_event_count += 1

                if profile is not None:
                    profile["phases_ns"]["append_events"] = (
                        perf_counter_ns() - phase_started
                    )
                phase_started = perf_counter_ns()
                course_latest_mutations = 0
                for code, old, new in course_changes:
                    course_id = course_ids[code]
                    if new is None:
                        connection.execute(
                            "DELETE FROM course_latest_state WHERE course_id = ?",
                            (course_id,),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO course_latest_state(
                                course_id, snapshot_id, title, department
                            ) VALUES (?, ?, ?, ?)
                            ON CONFLICT(course_id) DO UPDATE SET
                                snapshot_id = excluded.snapshot_id,
                                title = excluded.title,
                                department = excluded.department
                            """,
                            (course_id, new_snapshot_id, *new),
                        )
                    course_latest_mutations += 1

                section_latest_mutations = 0
                for key, old, new in section_changes:
                    section_id = section_ids[key]
                    if new is None:
                        connection.execute(
                            "DELETE FROM section_latest_state WHERE section_id = ?",
                            (section_id,),
                        )
                    else:
                        connection.execute(
                            """
                            INSERT INTO section_latest_state(
                                section_id, snapshot_id, section_type,
                                enrollment_count, capacity_count, instructor
                            ) VALUES (?, ?, ?, ?, ?, ?)
                            ON CONFLICT(section_id) DO UPDATE SET
                                snapshot_id = excluded.snapshot_id,
                                section_type = excluded.section_type,
                                enrollment_count = excluded.enrollment_count,
                                capacity_count = excluded.capacity_count,
                                instructor = excluded.instructor
                            """,
                            (section_id, new_snapshot_id, *new),
                        )
                    section_latest_mutations += 1

                if profile is not None:
                    profile["phases_ns"]["replace_latest"] = (
                        perf_counter_ns() - phase_started
                    )
                    profile["latest_projection_mutations"] = {
                        "courses": course_latest_mutations,
                        "sections": section_latest_mutations,
                    }
                phase_started = perf_counter_ns()
                should_checkpoint = force_checkpoint or self._checkpoint_due(
                    connection, new_snapshot_id
                )
                if should_checkpoint:
                    self._create_checkpoint(connection, new_snapshot_id)
                if before_commit is not None:
                    before_commit(connection, new_snapshot_id)
                if profile is not None:
                    profile["phases_ns"]["checkpoint"] = (
                        perf_counter_ns() - phase_started
                    )
                phase_started = perf_counter_ns()
                if owns_connection:
                    connection.commit()
                if profile is not None:
                    profile["phases_ns"]["commit"] = perf_counter_ns() - phase_started
            except Exception:
                if owns_connection:
                    connection.rollback()
                self.invalidate_runtime_caches()
                raise

        result = WriteResult(
            snapshot_id=new_snapshot_id,
            created=True,
            course_events=course_event_count,
            section_events=section_event_count,
            checkpoint_created=should_checkpoint,
        )
        if owns_connection:
            self.accept_committed_snapshot(snapshot, result)
        return result

    @staticmethod
    def _checkpoint_due(connection: sqlite3.Connection, snapshot_id: int) -> bool:
        previous = connection.execute(
            """
            SELECT c.snapshot_id, s.sequence_no
            FROM state_checkpoint c
            JOIN state_snapshot s ON s.snapshot_id = c.snapshot_id
            ORDER BY s.sequence_no DESC LIMIT 1
            """
        ).fetchone()
        if previous is None:
            return True
        previous_sequence_no = int(previous["sequence_no"])
        target_sequence_no = int(
            connection.execute(
                "SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?",
                (snapshot_id,),
            ).fetchone()[0]
        )
        states = connection.execute(
            """
            SELECT count(*) FROM state_snapshot
            WHERE sequence_no > ? AND sequence_no <= ?
            """,
            (previous_sequence_no, target_sequence_no),
        ).fetchone()[0]
        course_events = connection.execute(
            """
            SELECT count(*) FROM course_change_event e
            JOIN state_snapshot s ON s.snapshot_id = e.snapshot_id
            WHERE s.sequence_no > ? AND s.sequence_no <= ?
            """,
            (previous_sequence_no, target_sequence_no),
        ).fetchone()[0]
        section_events = connection.execute(
            """
            SELECT count(*) FROM section_change_event e
            JOIN state_snapshot s ON s.snapshot_id = e.snapshot_id
            WHERE s.sequence_no > ? AND s.sequence_no <= ?
            """,
            (previous_sequence_no, target_sequence_no),
        ).fetchone()[0]
        return (
            states >= STATE_THRESHOLD
            or course_events + section_events >= EVENT_THRESHOLD
        )

    @staticmethod
    def _create_checkpoint(connection: sqlite3.Connection, snapshot_id: int) -> None:
        existing = connection.execute(
            "SELECT checkpoint_id FROM state_checkpoint WHERE snapshot_id = ?",
            (snapshot_id,),
        ).fetchone()
        if existing:
            return
        cursor = connection.execute(
            "INSERT INTO state_checkpoint(snapshot_id) VALUES (?)", (snapshot_id,)
        )
        if cursor.lastrowid is None:
            raise sqlite3.Error("checkpoint insert returned no ID")
        checkpoint_id = int(cursor.lastrowid)
        connection.execute(
            """
            INSERT INTO course_checkpoint_state(
                checkpoint_id, course_id, title, department
            )
            SELECT ?, course_id, title, department FROM course_latest_state
            """,
            (checkpoint_id,),
        )
        connection.execute(
            """
            INSERT INTO section_checkpoint_state(
                checkpoint_id, section_id, section_type,
                enrollment_count, capacity_count, instructor
            )
            SELECT ?, section_id, section_type, enrollment_count,
                   capacity_count, instructor
            FROM section_latest_state
            """,
            (checkpoint_id,),
        )

    def force_checkpoint(self, snapshot_id: int) -> None:
        with self.connection() as connection:
            with connection:
                latest = self.get_latest_snapshot_id(connection=connection)
                if latest != snapshot_id:
                    snapshot = self.reconstruct_snapshot(snapshot_id)
                    raise ValueError(
                        "forced prototype checkpoints are only supported for "
                        f"latest state; latest={latest}, requested={snapshot.timestamp}"
                    )
                self._create_checkpoint(connection, snapshot_id)

    def get_latest_snapshot_id(
        self, *, connection: sqlite3.Connection | None = None
    ) -> int | None:
        if connection is not None:
            row = connection.execute(
                """
                SELECT snapshot_id FROM state_snapshot
                ORDER BY sequence_no DESC LIMIT 1
                """
            ).fetchone()
            return int(row[0]) if row else None
        with self.connection() as opened:
            return self.get_latest_snapshot_id(connection=opened)

    def _checkpoint_state(
        self, connection: sqlite3.Connection, snapshot_id: int
    ) -> tuple[
        int,
        dict[str, tuple[str, str]],
        dict[tuple[str, str], tuple[str, int, int, str]],
    ]:
        checkpoint = connection.execute(
            """
            SELECT c.checkpoint_id, c.snapshot_id, s.sequence_no
            FROM state_checkpoint c
            JOIN state_snapshot s ON s.snapshot_id = c.snapshot_id
            WHERE s.sequence_no <= (
                SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?
            )
            ORDER BY s.sequence_no DESC LIMIT 1
            """,
            (snapshot_id,),
        ).fetchone()
        if checkpoint is None:
            raise ValueError(f"no checkpoint precedes snapshot {snapshot_id}")
        checkpoint_id = int(checkpoint["checkpoint_id"])
        checkpoint_sequence_no = int(checkpoint["sequence_no"])
        courses = {
            row["course_code"]: (row["title"] or "", row["department"])
            for row in connection.execute(
                """
                SELECT c.course_code, s.title, s.department
                FROM course_checkpoint_state s
                JOIN course_catalog c ON c.course_id = s.course_id
                WHERE s.checkpoint_id = ?
                """,
                (checkpoint_id,),
            )
        }
        sections = {
            (row["course_code"], row["section_code"]): (
                row["section_type"],
                row["enrollment_count"],
                row["capacity_count"],
                row["instructor"],
            )
            for row in connection.execute(
                """
                SELECT c.course_code, sc.section_code, s.section_type,
                       s.enrollment_count, s.capacity_count, s.instructor
                FROM section_checkpoint_state s
                JOIN section_catalog sc ON sc.section_id = s.section_id
                JOIN course_catalog c ON c.course_id = sc.course_id
                WHERE s.checkpoint_id = ?
                """,
                (checkpoint_id,),
            )
        }
        return checkpoint_sequence_no, courses, sections

    def reconstruct_snapshot(self, snapshot_id: int) -> EnrollmentSnapshot:
        return self._reconstruct_snapshot(snapshot_id)

    def profile_reconstruct_snapshot(
        self, snapshot_id: int
    ) -> tuple[EnrollmentSnapshot, dict[str, Any]]:
        """Reconstruct one state with phase timings and SQLite query plans."""
        profile: dict[str, Any] = {
            "phases_ns": {
                phase: 0
                for phase in (
                    "metadata",
                    "checkpoint",
                    "course_events",
                    "section_events",
                    "build_snapshot",
                )
            },
            "query_plans": self.reconstruction_query_plans(snapshot_id),
        }
        started = perf_counter_ns()
        snapshot = self._reconstruct_snapshot(snapshot_id, profile=profile)
        profile["total_ns"] = perf_counter_ns() - started
        return snapshot, profile

    def _reconstruct_snapshot(
        self, snapshot_id: int, *, profile: dict[str, Any] | None = None
    ) -> EnrollmentSnapshot:
        with self.connection() as connection:
            phase_started = perf_counter_ns()
            metadata = connection.execute(
                """
                SELECT sequence_no, observed_at, semester, overall_fill
                FROM state_snapshot WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ).fetchone()
            if metadata is None:
                raise KeyError(snapshot_id)
            if profile is not None:
                profile["phases_ns"]["metadata"] = perf_counter_ns() - phase_started
            phase_started = perf_counter_ns()
            checkpoint_sequence_no, courses, sections = self._checkpoint_state(
                connection, snapshot_id
            )
            target_sequence_no = int(metadata["sequence_no"])
            if profile is not None:
                profile["phases_ns"]["checkpoint"] = perf_counter_ns() - phase_started
            phase_started = perf_counter_ns()
            for row in connection.execute(
                """
                SELECT e.event_kind, c.course_code, e.new_title, e.new_department
                FROM course_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN course_catalog c ON c.course_id = e.course_id
                WHERE ss.sequence_no > ? AND ss.sequence_no <= ?
                ORDER BY ss.sequence_no, e.event_id
                """,
                (checkpoint_sequence_no, target_sequence_no),
            ):
                code = row["course_code"]
                if row["event_kind"] == "REMOVE":
                    courses.pop(code, None)
                else:
                    courses[code] = (
                        row["new_title"] or "",
                        row["new_department"],
                    )
            if profile is not None:
                profile["phases_ns"]["course_events"] = (
                    perf_counter_ns() - phase_started
                )
            phase_started = perf_counter_ns()
            for row in connection.execute(
                """
                SELECT e.event_kind, c.course_code, s.section_code,
                       e.new_section_type, e.new_enrollment_count,
                       e.new_capacity_count, e.new_instructor
                FROM section_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN section_catalog s ON s.section_id = e.section_id
                JOIN course_catalog c ON c.course_id = s.course_id
                WHERE ss.sequence_no > ? AND ss.sequence_no <= ?
                ORDER BY ss.sequence_no, e.event_id
                """,
                (checkpoint_sequence_no, target_sequence_no),
            ):
                key = (row["course_code"], row["section_code"])
                if row["event_kind"] == "REMOVE":
                    sections.pop(key, None)
                else:
                    sections[key] = (
                        row["new_section_type"],
                        row["new_enrollment_count"],
                        row["new_capacity_count"],
                        row["new_instructor"],
                    )

            if profile is not None:
                profile["phases_ns"]["section_events"] = (
                    perf_counter_ns() - phase_started
                )

        phase_started = perf_counter_ns()
        snapshot = self._build_snapshot(metadata, courses, sections)
        if profile is not None:
            profile["phases_ns"]["build_snapshot"] = perf_counter_ns() - phase_started
        return snapshot

    def reconstruction_query_plans(self, snapshot_id: int) -> dict[str, list[str]]:
        """Return the planner details for every point-reconstruction query."""
        queries: dict[str, tuple[str, tuple[Any, ...]]] = {
            "metadata": (
                """
                SELECT observed_at, semester, overall_fill
                FROM state_snapshot WHERE snapshot_id = ?
                """,
                (snapshot_id,),
            ),
            "checkpoint": (
                """
                SELECT c.checkpoint_id, c.snapshot_id
                FROM state_checkpoint c
                JOIN state_snapshot s ON s.snapshot_id = c.snapshot_id
                WHERE s.sequence_no <= (
                    SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?
                )
                ORDER BY s.sequence_no DESC LIMIT 1
                """,
                (snapshot_id,),
            ),
            "course_events": (
                """
                SELECT e.event_kind, c.course_code, e.new_title, e.new_department
                FROM course_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN course_catalog c ON c.course_id = e.course_id
                WHERE ss.sequence_no > ? AND ss.sequence_no <= (
                    SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?
                )
                ORDER BY ss.sequence_no, e.event_id
                """,
                (0, snapshot_id),
            ),
            "section_events": (
                """
                SELECT e.event_kind, c.course_code, s.section_code,
                       e.new_section_type, e.new_enrollment_count,
                       e.new_capacity_count, e.new_instructor
                FROM section_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN section_catalog s ON s.section_id = e.section_id
                JOIN course_catalog c ON c.course_id = s.course_id
                WHERE ss.sequence_no > ? AND ss.sequence_no <= (
                    SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?
                )
                ORDER BY ss.sequence_no, e.event_id
                """,
                (0, snapshot_id),
            ),
        }
        with self.connection() as connection:
            checkpoint = connection.execute(
                """
                SELECT c.checkpoint_id
                FROM state_checkpoint c
                JOIN state_snapshot s ON s.snapshot_id = c.snapshot_id
                WHERE s.sequence_no <= (
                    SELECT sequence_no FROM state_snapshot WHERE snapshot_id = ?
                )
                ORDER BY s.sequence_no DESC LIMIT 1
                """,
                (snapshot_id,),
            ).fetchone()
            if checkpoint is None:
                raise ValueError(f"no checkpoint precedes snapshot {snapshot_id}")
            checkpoint_id = int(checkpoint[0])
            queries["course_checkpoint"] = (
                """
                SELECT c.course_code, s.title, s.department
                FROM course_checkpoint_state s
                JOIN course_catalog c ON c.course_id = s.course_id
                WHERE s.checkpoint_id = ?
                """,
                (checkpoint_id,),
            )
            queries["section_checkpoint"] = (
                """
                SELECT c.course_code, sc.section_code, s.section_type,
                       s.enrollment_count, s.capacity_count, s.instructor
                FROM section_checkpoint_state s
                JOIN section_catalog sc ON sc.section_id = s.section_id
                JOIN course_catalog c ON c.course_id = sc.course_id
                WHERE s.checkpoint_id = ?
                """,
                (checkpoint_id,),
            )
            return {
                label: [
                    str(row["detail"])
                    for row in connection.execute(
                        f"EXPLAIN QUERY PLAN {query}", parameters
                    )
                ]
                for label, (query, parameters) in queries.items()
            }

    def get_snapshot_data(self, snapshot_id: int) -> EnrollmentSnapshot | None:
        """Compatibility adapter for current reporting/database callers."""
        try:
            return self.reconstruct_snapshot(snapshot_id)
        except KeyError:
            return None

    @staticmethod
    def _build_snapshot(
        metadata: sqlite3.Row | dict[str, Any],
        courses: dict[str, tuple[str, str]],
        sections: dict[tuple[str, str], tuple[str, int, int, str]],
    ) -> EnrollmentSnapshot:
        snapshot = EnrollmentSnapshot(
            timestamp=metadata["observed_at"],
            semester=metadata["semester"],
            overall_fill=metadata["overall_fill"],
        )
        for code, (title, department) in courses.items():
            snapshot.courses[code] = Course(
                course_code=code,
                department=department,
                course_title=title or None,
            )
        for (code, section_code), state in sections.items():
            if code not in snapshot.courses:
                continue
            section_type, enrollment, capacity, instructor = state
            section = Section(
                section_id=section_code,
                section_type=section_type,
                enrollment=enrollment,
                capacity=capacity,
                fill=enrollment / capacity,
                instructor=instructor,
            )
            snapshot.courses[code].sections[section_code] = section
        for course in snapshot.courses.values():
            if course.sections:
                course.average_fill = sum(
                    section.fill for section in course.sections.values()
                ) / len(course.sections)
        return snapshot

    def iter_reconstructed_snapshots(self) -> Iterator[tuple[int, EnrollmentSnapshot]]:
        """Yield every state using a fixed set of bulk queries."""
        with self.connection() as connection:
            first = connection.execute(
                """
                SELECT c.checkpoint_id, c.snapshot_id, s.sequence_no
                FROM state_checkpoint c
                JOIN state_snapshot s ON s.snapshot_id = c.snapshot_id
                ORDER BY s.sequence_no LIMIT 1
                """
            ).fetchone()
            if first is None:
                return
            checkpoint_id = int(first["checkpoint_id"])
            first_sequence_no = int(first["sequence_no"])
            courses = {
                row["course_code"]: (row["title"] or "", row["department"])
                for row in connection.execute(
                    """
                    SELECT c.course_code, s.title, s.department
                    FROM course_checkpoint_state s
                    JOIN course_catalog c ON c.course_id = s.course_id
                    WHERE s.checkpoint_id = ?
                    """,
                    (checkpoint_id,),
                )
            }
            sections = {
                (row["course_code"], row["section_code"]): (
                    row["section_type"],
                    row["enrollment_count"],
                    row["capacity_count"],
                    row["instructor"],
                )
                for row in connection.execute(
                    """
                    SELECT c.course_code, sc.section_code, s.section_type,
                           s.enrollment_count, s.capacity_count, s.instructor
                    FROM section_checkpoint_state s
                    JOIN section_catalog sc ON sc.section_id = s.section_id
                    JOIN course_catalog c ON c.course_id = sc.course_id
                    WHERE s.checkpoint_id = ?
                    """,
                    (checkpoint_id,),
                )
            }
            course_events: dict[int, list[sqlite3.Row]] = {}
            for row in connection.execute(
                """
                SELECT e.snapshot_id, e.event_kind, c.course_code,
                       e.new_title, e.new_department
                FROM course_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN course_catalog c ON c.course_id = e.course_id
                WHERE ss.sequence_no > ?
                ORDER BY ss.sequence_no, e.event_id
                """,
                (first_sequence_no,),
            ):
                course_events.setdefault(int(row["snapshot_id"]), []).append(row)
            section_events: dict[int, list[sqlite3.Row]] = {}
            for row in connection.execute(
                """
                SELECT e.snapshot_id, e.event_kind, c.course_code, s.section_code,
                       e.new_section_type, e.new_enrollment_count,
                       e.new_capacity_count, e.new_instructor
                FROM section_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN section_catalog s ON s.section_id = e.section_id
                JOIN course_catalog c ON c.course_id = s.course_id
                WHERE ss.sequence_no > ?
                ORDER BY ss.sequence_no, e.event_id
                """,
                (first_sequence_no,),
            ):
                section_events.setdefault(int(row["snapshot_id"]), []).append(row)
            metadata_rows = connection.execute(
                """
                SELECT snapshot_id, sequence_no, observed_at, semester, overall_fill
                FROM state_snapshot WHERE sequence_no >= ?
                ORDER BY sequence_no
                """,
                (first_sequence_no,),
            ).fetchall()

        for metadata in metadata_rows:
            snapshot_id = int(metadata["snapshot_id"])
            for row in course_events.get(snapshot_id, []):
                code = row["course_code"]
                if row["event_kind"] == "REMOVE":
                    courses.pop(code, None)
                else:
                    courses[code] = (
                        row["new_title"] or "",
                        row["new_department"],
                    )
            for row in section_events.get(snapshot_id, []):
                key = (row["course_code"], row["section_code"])
                if row["event_kind"] == "REMOVE":
                    sections.pop(key, None)
                else:
                    sections[key] = (
                        row["new_section_type"],
                        row["new_enrollment_count"],
                        row["new_capacity_count"],
                        row["new_instructor"],
                    )
            yield snapshot_id, self._build_snapshot(metadata, courses, sections)

    def max_replay_distance(self) -> int:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT max(
                    (SELECT count(*) FROM state_snapshot s2
                     WHERE s2.sequence_no > (
                         SELECT max(cs.sequence_no)
                         FROM state_checkpoint c
                         JOIN state_snapshot cs ON cs.snapshot_id = c.snapshot_id
                         WHERE cs.sequence_no <= s.sequence_no
                     )
                     AND s2.sequence_no <= s.sequence_no)
                )
                FROM state_snapshot s
                """
            ).fetchone()
            return int(row[0] or 0)

    def get_course_history(
        self, course_code: str, semester: str | None = None
    ) -> list[dict[str, Any]]:
        with self.connection() as connection:
            course = connection.execute(
                "SELECT course_id FROM course_catalog WHERE course_code = ?",
                (course_code,),
            ).fetchone()
            if course is None:
                return []
            course_id = int(course[0])
            sections = {
                int(row["section_id"]): row["section_code"]
                for row in connection.execute(
                    """
                    SELECT section_id, section_code FROM section_catalog
                    WHERE course_id = ?
                    """,
                    (course_id,),
                )
            }
            metadata = connection.execute(
                """
                SELECT snapshot_id, observed_at FROM state_snapshot
                WHERE (? IS NULL OR semester = ?)
                ORDER BY sequence_no
                """,
                (semester, semester),
            ).fetchall()
            events: dict[int, list[sqlite3.Row]] = {}
            for row in connection.execute(
                """
                SELECT e.snapshot_id, e.section_id, e.event_kind,
                       e.new_enrollment_count, e.new_capacity_count
                FROM section_change_event e
                JOIN state_snapshot ss ON ss.snapshot_id = e.snapshot_id
                JOIN section_catalog s ON s.section_id = e.section_id
                WHERE s.course_id = ?
                ORDER BY ss.sequence_no, e.event_id
                """,
                (course_id,),
            ):
                events.setdefault(int(row["snapshot_id"]), []).append(row)

        history: list[dict[str, Any]] = []
        state: dict[int, tuple[int, int]] = {}
        for snapshot in metadata:
            snapshot_id = int(snapshot["snapshot_id"])
            for event in events.get(snapshot_id, []):
                section_id = int(event["section_id"])
                if event["event_kind"] == "REMOVE":
                    state.pop(section_id, None)
                else:
                    state[section_id] = (
                        int(event["new_enrollment_count"]),
                        int(event["new_capacity_count"]),
                    )
            for section_id in sorted(state, key=sections.__getitem__):
                enrollment, capacity = state[section_id]
                history.append(
                    {
                        "timestamp": snapshot["observed_at"],
                        "section_code": sections[section_id],
                        "fill_percentage": enrollment / capacity,
                        "enrollment_count": enrollment,
                        "capacity_count": capacity,
                    }
                )
        return history

    def copy_reporting_log(self, rows: list[tuple[int, int, str, int, str]]) -> None:
        with self.connection() as connection:
            with connection:
                connection.executemany(
                    """
                    INSERT INTO reporting_log_v2(
                        report_id, reported_snapshot_id, report_timestamp,
                        changes_found, created_at
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    rows,
                )

    def get_last_reported_snapshot_id(self) -> int | None:
        with self.connection() as connection:
            row = connection.execute(
                """
                SELECT reported_snapshot_id FROM reporting_log_v2
                ORDER BY report_timestamp DESC LIMIT 1
                """
            ).fetchone()
            return int(row[0]) if row else None

    def add_reporting_log(
        self,
        snapshot_id: int,
        changes_were_found: bool,
        *,
        report_timestamp: str = "2099-01-01T00:00:00",
    ) -> None:
        with self.connection() as connection:
            with connection:
                connection.execute(
                    """
                    INSERT INTO reporting_log_v2(
                        reported_snapshot_id, report_timestamp, changes_found
                    ) VALUES (?, ?, ?)
                    """,
                    (snapshot_id, report_timestamp, int(changes_were_found)),
                )

    def statistics(self) -> dict[str, int]:
        tables = {
            "snapshots": "state_snapshot",
            "course_events": "course_change_event",
            "section_events": "section_change_event",
            "checkpoints": "state_checkpoint",
            "latest_courses": "course_latest_state",
            "latest_sections": "section_latest_state",
        }
        with self.connection() as connection:
            return {
                label: int(
                    connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0]
                )
                for label, table in tables.items()
            }

    def integrity(self) -> dict[str, Any]:
        with self.connection() as connection:
            return {
                "integrity_check": connection.execute(
                    "PRAGMA integrity_check"
                ).fetchone()[0],
                "foreign_key_violations": len(
                    connection.execute("PRAGMA foreign_key_check").fetchall()
                ),
            }
