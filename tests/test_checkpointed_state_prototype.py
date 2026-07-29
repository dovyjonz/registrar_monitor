"""Behavior tests for the disposable checkpointed-state prototype."""

import sqlite3
import sys
from contextlib import closing
from pathlib import Path

import pytest

from registrarmonitor.models import Course, EnrollmentSnapshot, Section

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

from checkpointed_state_prototype import CheckpointedStateStore
from evaluate_checkpointed_state import (
    LegacyReader,
    benchmark_targeted_performance,
    evaluate_failure_injection,
    evaluate_raw_instructor_evidence,
    resolve_semester,
)

pytestmark = pytest.mark.integration


def test_evaluation_derives_or_validates_the_source_semester(tmp_path: Path) -> None:
    source = tmp_path / "legacy.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.execute(
            "CREATE TABLE snapshots (snapshot_id INTEGER PRIMARY KEY, semester TEXT)"
        )
        connection.execute(
            "INSERT INTO snapshots (snapshot_id, semester) VALUES (1, 'Spring 2026')"
        )
        connection.commit()

    reader = LegacyReader(source)

    assert resolve_semester(reader, None) == "Spring 2026"
    assert resolve_semester(reader, "Spring 2026") == "Spring 2026"
    with pytest.raises(ValueError, match="requested semester"):
        resolve_semester(reader, "Summer 2026")


def test_legacy_reader_preserves_valid_freshness_and_defaults_missing_values(
    tmp_path: Path,
) -> None:
    source = tmp_path / "legacy.db"
    with closing(sqlite3.connect(source)) as connection:
        connection.executescript(
            """
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL,
                semester TEXT NOT NULL,
                overall_fill REAL NOT NULL,
                last_seen_at TEXT
            );
            CREATE TABLE courses (
                course_id INTEGER PRIMARY KEY,
                course_code TEXT NOT NULL,
                course_title TEXT,
                department TEXT
            );
            CREATE TABLE sections (
                section_id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL,
                section_code TEXT NOT NULL,
                section_type TEXT,
                instructor TEXT
            );
            CREATE TABLE enrollment_data (
                snapshot_id INTEGER NOT NULL,
                section_id INTEGER NOT NULL,
                enrollment_count INTEGER NOT NULL,
                capacity_count INTEGER NOT NULL,
                fill_percentage REAL NOT NULL
            );
            INSERT INTO courses VALUES
                (1, 'CSCI 101', 'Introduction to Computing', 'CSCI');
            INSERT INTO sections VALUES (1, 1, '1L', 'L', 'Ada Lovelace');
            INSERT INTO snapshots VALUES
                (1, '2026-05-11 10:00:00', 'Summer 2026', 0.5,
                 '2026-05-11 10:05:00'),
                (2, '2026-05-11 10:10:00', 'Summer 2026', 0.55, NULL);
            INSERT INTO enrollment_data VALUES
                (1, 1, 10, 20, 0.5),
                (2, 1, 11, 20, 0.55);
            """
        )

    _, catalog = LegacyReader(source).load_snapshots()

    assert catalog["last_seen_by_snapshot"] == {
        1: "2026-05-11 10:05:00",
        2: "2026-05-11 10:10:00",
    }
    assert catalog["freshness"] == {
        "source_column_present": True,
        "values_later_than_observed_at": 1,
    }


def _snapshot(
    timestamp: str,
    *,
    enrollment: int = 10,
    instructor: str = "Ada Lovelace",
) -> EnrollmentSnapshot:
    section = Section(
        section_id="1L",
        section_type="L",
        enrollment=enrollment,
        capacity=20,
        fill=enrollment / 20,
        instructor=instructor,
    )
    course = Course(
        course_code="CSCI 101",
        department="CSCI",
        course_title="Introduction to Computing",
        sections={"1L": section},
        average_fill=section.fill,
    )
    return EnrollmentSnapshot(
        timestamp=timestamp,
        semester="Summer 2026",
        overall_fill=section.fill,
        courses={course.course_code: course},
    )


def test_first_state_creates_add_events_latest_state_and_checkpoint(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")

    result = store.write_snapshot(
        _snapshot("2026-05-11 10:00:00"),
        snapshot_id=7,
    )

    assert result.snapshot_id == 7
    assert result.created is True
    assert result.course_events == 1
    assert result.section_events == 1
    assert result.checkpoint_created is True
    assert store.get_latest_snapshot_id() == 7
    assert (
        store.reconstruct_snapshot(7).to_dict()
        == _snapshot("2026-05-11 10:00:00").to_dict()
    )
    assert store.statistics() == {
        "snapshots": 1,
        "course_events": 1,
        "section_events": 1,
        "checkpoints": 1,
        "latest_courses": 1,
        "latest_sections": 1,
    }


def test_identical_poll_updates_freshness_but_a_b_a_creates_a_new_state(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    state_a = _snapshot("2026-05-11 10:00:00", enrollment=10)
    store.write_snapshot(state_a, snapshot_id=1)

    duplicate = _snapshot("2026-05-11 10:05:00", enrollment=10)
    duplicate_result = store.write_snapshot(duplicate, snapshot_id=2)

    assert duplicate_result.created is False
    assert duplicate_result.snapshot_id == 1
    assert store.statistics()["snapshots"] == 1
    with store.connection() as connection:
        row = connection.execute(
            "SELECT observed_at, last_seen_at FROM state_snapshot"
        ).fetchone()
        assert tuple(row) == (
            "2026-05-11 10:00:00",
            "2026-05-11 10:05:00",
        )

    store.write_snapshot(
        _snapshot("2026-05-11 10:10:00", enrollment=11),
        snapshot_id=2,
    )
    returned = store.write_snapshot(
        _snapshot("2026-05-11 10:15:00", enrollment=10),
        snapshot_id=3,
    )

    assert returned.created is True
    assert store.statistics()["snapshots"] == 3
    with store.connection() as connection:
        hashes = [
            bytes(row[0])
            for row in connection.execute(
                "SELECT state_hash FROM state_snapshot ORDER BY snapshot_id"
            )
        ]
    assert hashes[0] == hashes[2]
    assert hashes[0] != hashes[1]


def test_replay_order_is_independent_of_preserved_legacy_snapshot_ids(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    chronological = [
        (119, _snapshot("2024-12-09 22:17:58", enrollment=1)),
        (44, _snapshot("2024-12-09 22:18:30", enrollment=2)),
        (175, _snapshot("2024-12-09 22:25:51", enrollment=3)),
    ]

    for snapshot_id, snapshot in chronological:
        store.write_snapshot(
            snapshot,
            snapshot_id=snapshot_id,
            preserve_duplicate_import=True,
        )

    assert [snapshot_id for snapshot_id, _ in store.iter_reconstructed_snapshots()] == [
        119,
        44,
        175,
    ]
    assert store.get_latest_snapshot_id() == 175
    assert (
        store.reconstruct_snapshot(44).courses["CSCI 101"].sections["1L"].enrollment
        == 2
    )
    assert [
        row["enrollment_count"]
        for row in store.get_course_history("CSCI 101", "Summer 2026")
    ] == [1, 2, 3]
    with store.connection() as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT snapshot_id, sequence_no FROM state_snapshot ORDER BY sequence_no"
            )
        ] == [(119, 1), (44, 2), (175, 3)]


def test_removal_and_reappearance_are_explicit_and_reuse_identity(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.write_snapshot(_snapshot("2026-05-11 10:00:00"), snapshot_id=1)
    empty = EnrollmentSnapshot(
        timestamp="2026-05-11 10:05:00",
        semester="Summer 2026",
        overall_fill=0.0,
    )

    removed = store.write_snapshot(empty, snapshot_id=2)
    reappeared = store.write_snapshot(
        _snapshot("2026-05-11 10:10:00", instructor=""),
        snapshot_id=3,
    )

    assert (removed.course_events, removed.section_events) == (1, 1)
    assert (reappeared.course_events, reappeared.section_events) == (1, 1)
    assert store.reconstruct_snapshot(2).courses == {}
    assert "CSCI 101" in store.reconstruct_snapshot(3).courses
    with store.connection() as connection:
        section_ids = [
            row[0]
            for row in connection.execute("SELECT section_id FROM section_catalog")
        ]
        transitions = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT event_kind, old_instructor, new_instructor
                FROM section_change_event ORDER BY snapshot_id
                """
            )
        ]
    assert section_ids == [1]
    assert transitions == [
        ("ADD", None, "Ada Lovelace"),
        ("REMOVE", "Ada Lovelace", None),
        ("ADD", None, ""),
    ]


def test_checkpoint_is_created_after_96_subsequent_distinct_states(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    for snapshot_id in range(1, 98):
        result = store.write_snapshot(
            _snapshot(
                f"2026-05-11 10:{snapshot_id:03d}:00",
                instructor=f"Instructor {snapshot_id}",
            ),
            snapshot_id=snapshot_id,
        )
        if snapshot_id == 96:
            assert result.checkpoint_created is False
        if snapshot_id == 97:
            assert result.checkpoint_created is True

    with store.connection() as connection:
        checkpoint_ids = [
            row[0]
            for row in connection.execute(
                "SELECT snapshot_id FROM state_checkpoint ORDER BY snapshot_id"
            )
        ]
    assert checkpoint_ids == [1, 97]
    assert (
        store.reconstruct_snapshot(96).courses["CSCI 101"].sections["1L"].instructor
        == "Instructor 96"
    )


def test_checkpoint_is_created_at_2048_events(tmp_path: Path) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")

    def event_heavy_snapshot(timestamp: str, enrollment: int) -> EnrollmentSnapshot:
        sections = {
            f"{index:04d}L": Section(
                section_id=f"{index:04d}L",
                section_type="L",
                enrollment=enrollment,
                capacity=20,
                fill=enrollment / 20,
                instructor="Ada",
            )
            for index in range(2_048)
        }
        course = Course(
            course_code="CSCI 999",
            department="CSCI",
            course_title="Event Load",
            sections=sections,
            average_fill=enrollment / 20,
        )
        return EnrollmentSnapshot(
            timestamp=timestamp,
            semester="Summer 2026",
            overall_fill=enrollment / 20,
            courses={course.course_code: course},
        )

    store.write_snapshot(event_heavy_snapshot("2026-05-11 10:00:00", 1), snapshot_id=1)
    result = store.write_snapshot(
        event_heavy_snapshot("2026-05-11 10:05:00", 2),
        snapshot_id=2,
    )

    assert result.section_events == 2_048
    assert result.checkpoint_created is True


def test_failed_state_write_rolls_back_snapshot_events_and_latest_state(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.write_snapshot(_snapshot("2026-05-11 10:00:00"), snapshot_id=1)
    invalid_section = Section("1L", "L", 10, 0, 0.0, instructor="Ada")
    invalid_course = Course(
        "CSCI 101",
        "CSCI",
        {"1L": invalid_section},
        0.0,
        "Introduction to Computing",
    )
    invalid = EnrollmentSnapshot(
        "2026-05-11 10:05:00",
        "Summer 2026",
        0.0,
        {"CSCI 101": invalid_course},
    )

    with pytest.raises(sqlite3.IntegrityError):
        store.write_snapshot(invalid, snapshot_id=2)

    assert store.statistics() == {
        "snapshots": 1,
        "course_events": 1,
        "section_events": 1,
        "checkpoints": 1,
        "latest_courses": 1,
        "latest_sections": 1,
    }
    assert (
        store.reconstruct_snapshot(1).to_dict()
        == _snapshot("2026-05-11 10:00:00").to_dict()
    )


def test_updates_record_both_sides_and_reconstruct_all_persisted_fields(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.write_snapshot(_snapshot("2026-05-11 10:00:00"), snapshot_id=1)
    updated_section = Section("1L", "R", 12, 24, 0.5, instructor="")
    updated_course = Course(
        "CSCI 101",
        "SEDS",
        {"1L": updated_section},
        0.5,
        "Renamed Computing",
    )
    updated = EnrollmentSnapshot(
        "2026-05-11 10:05:00",
        "Summer 2026",
        0.5,
        {"CSCI 101": updated_course},
    )

    result = store.write_snapshot(updated, snapshot_id=2)

    assert (result.course_events, result.section_events) == (1, 1)
    reconstructed = store.get_snapshot_data(2)
    assert reconstructed is not None
    assert reconstructed.to_dict() == updated.to_dict()
    with store.connection() as connection:
        course_event = tuple(
            connection.execute(
                """
                SELECT event_kind, old_title, new_title,
                       old_department, new_department
                FROM course_change_event WHERE snapshot_id = 2
                """
            ).fetchone()
        )
        section_event = tuple(
            connection.execute(
                """
                SELECT event_kind, old_section_type, new_section_type,
                       old_enrollment_count, new_enrollment_count,
                       old_capacity_count, new_capacity_count,
                       old_instructor, new_instructor
                FROM section_change_event WHERE snapshot_id = 2
                """
            ).fetchone()
        )
    assert course_event == (
        "UPDATE",
        "Introduction to Computing",
        "Renamed Computing",
        "CSCI",
        "SEDS",
    )
    assert section_event == (
        "UPDATE",
        "L",
        "R",
        10,
        12,
        20,
        24,
        "Ada Lovelace",
        "",
    )


def test_reporting_log_adapter_preserves_ids_and_stateful_position(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.write_snapshot(_snapshot("2026-05-11 10:00:00"), snapshot_id=7)
    store.write_snapshot(
        _snapshot("2026-05-11 10:05:00", enrollment=11),
        snapshot_id=9,
    )

    store.copy_reporting_log(
        [
            (3, 7, "2026-05-11T10:01:00", 0, "2026-05-11 10:01:00"),
            (8, 9, "2026-05-11T10:06:00", 1, "2026-05-11 10:06:00"),
        ]
    )

    assert store.get_last_reported_snapshot_id() == 9
    with store.connection() as connection:
        rows = [
            tuple(row)
            for row in connection.execute(
                """
                SELECT report_id, reported_snapshot_id, changes_found
                FROM reporting_log_v2 ORDER BY report_id
                """
            )
        ]
    assert rows == [(3, 7, 0), (8, 9, 1)]


def test_course_history_preserves_legacy_section_code_order(tmp_path: Path) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.seed_identity(
        course_id=72,
        course_code="CHEM 101L",
        sections=[(116, "9ChLb"), (117, "10ChLb")],
    )
    sections = {
        code: Section(code, "L", enrollment, 20, enrollment / 20, instructor="Ada")
        for code, enrollment in (("10ChLb", 10), ("9ChLb", 9))
    }
    snapshot = EnrollmentSnapshot(
        timestamp="2026-01-01 10:00:00",
        semester="Spring 2026",
        overall_fill=0.475,
        courses={
            "CHEM 101L": Course(
                "CHEM 101L",
                "CHEM",
                sections,
                0.475,
                "General Chemistry I lab",
            )
        },
    )
    store.write_snapshot(snapshot, snapshot_id=1)

    history = store.get_course_history("CHEM 101L", "Spring 2026")

    assert [row["section_code"] for row in history] == ["10ChLb", "9ChLb"]


def test_bulk_replay_matches_checkpoint_point_reads(tmp_path: Path) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    for snapshot_id in range(1, 102):
        store.write_snapshot(
            _snapshot(
                f"2026-05-11 11:{snapshot_id:03d}:00",
                instructor=f"Instructor {snapshot_id}",
            ),
            snapshot_id=snapshot_id,
        )

    bulk = dict(store.iter_reconstructed_snapshots())

    assert len(bulk) == 101
    assert store.max_replay_distance() == 95
    for snapshot_id, snapshot in bulk.items():
        assert store.reconstruct_snapshot(snapshot_id).to_dict() == snapshot.to_dict()


def test_profiled_write_and_reconstruction_report_bounded_phases_and_query_plans(
    tmp_path: Path,
) -> None:
    store = CheckpointedStateStore(tmp_path / "prototype.db")
    store.write_snapshot(_snapshot("2026-05-11 10:00:00"), snapshot_id=1)

    write_result, write_profile = store.profile_write_snapshot(
        _snapshot("2026-05-11 10:05:00", enrollment=11),
        snapshot_id=2,
    )
    reconstructed, read_profile = store.profile_reconstruct_snapshot(2)

    assert write_result.created is True
    assert (
        reconstructed.to_dict()
        == _snapshot("2026-05-11 10:05:00", enrollment=11).to_dict()
    )
    assert set(write_profile["phases_ns"]) == {
        "canonicalize",
        "load_current",
        "append_events",
        "replace_latest",
        "checkpoint",
        "commit",
    }
    assert set(read_profile["phases_ns"]) == {
        "metadata",
        "checkpoint",
        "course_events",
        "section_events",
        "build_snapshot",
    }
    assert all(value >= 0 for value in write_profile["phases_ns"].values())
    assert all(value >= 0 for value in read_profile["phases_ns"].values())
    assert write_profile["total_ns"] >= sum(write_profile["phases_ns"].values())
    assert read_profile["total_ns"] >= sum(read_profile["phases_ns"].values())
    assert write_profile["latest_projection_mutations"] == {
        "courses": 0,
        "sections": 1,
    }
    assert set(read_profile["query_plans"]) == {
        "metadata",
        "checkpoint",
        "course_checkpoint",
        "section_checkpoint",
        "course_events",
        "section_events",
    }
    assert all(read_profile["query_plans"].values())


def test_targeted_performance_uses_requested_samples_and_reports_phase_p95(
    tmp_path: Path,
) -> None:
    path = tmp_path / "prototype.db"
    store = CheckpointedStateStore(path)
    latest = _snapshot("2026-05-11 10:00:00")
    store.write_snapshot(latest, snapshot_id=1)

    result = benchmark_targeted_performance(
        path,
        latest,
        samples=3,
        warmup=1,
    )

    assert result["samples"] == 3
    assert result["warmup"] == 1
    assert result["changed_write"]["samples"] == 3
    assert result["latest_read"]["samples"] == 3
    assert set(result["changed_write_phases"]) == {
        "canonicalize",
        "load_current",
        "append_events",
        "replace_latest",
        "checkpoint",
        "commit",
    }
    assert set(result["latest_read_phases"]) == {
        "metadata",
        "checkpoint",
        "course_events",
        "section_events",
        "build_snapshot",
    }
    assert result["query_plans"]["metadata"]


def test_raw_instructor_evidence_resolves_only_unique_exact_transitions(
    tmp_path: Path,
) -> None:
    files = [tmp_path / name for name in ("first.xls", "second.xls", "third.xls")]
    for path in files:
        path.touch()

    class FakeReader:
        observations: dict[str, tuple[str, str, list[dict[str, object]]]] = {
            "first.xls": (
                "Spring 2026",
                "2025-12-17 09:00:00",
                [
                    {
                        "Course Abbr": "CSCI 101",
                        "S/T": "1L",
                        "Instructor": "Ada",
                    }
                ],
            ),
            "second.xls": (
                "Spring 2026",
                "2025-12-17 10:00:00",
                [
                    {
                        "Course Abbr": "CSCI 101",
                        "S/T": "1L",
                        "Instructor": "Grace",
                    }
                ],
            ),
            "third.xls": (
                "Summer 2026",
                "2026-05-11 10:00:00",
                [],
            ),
        }

        def read_excel_data(
            self, input_file: str
        ) -> tuple[str, str, list[dict[str, object]]]:
            return self.observations[Path(input_file).name]

    changes = [
        {
            "change_id": 1,
            "section_id": 7,
            "old_instructor": "Ada",
            "new_instructor": "Grace",
            "timestamp": "2026-07-29 00:00:00",
        },
        {
            "change_id": 2,
            "section_id": 7,
            "old_instructor": "Grace",
            "new_instructor": "",
            "timestamp": "2026-07-29 00:00:00",
        },
    ]
    catalog = {
        "section_by_id": {7: (3, "1L", "L")},
        "course_by_id": {3: ("CSCI 101", "Introduction", "CSCI")},
    }

    result = evaluate_raw_instructor_evidence(
        tmp_path,
        "Spring 2026",
        catalog,
        changes,
        reader=FakeReader(),
        snapshot_timestamps=[
            "2025-12-17 09:00:00",
            "2025-12-17 10:00:00",
            "2025-12-17 11:00:00",
        ],
    )

    assert result["files_scanned"] == 3
    assert result["semester_files"] == 2
    assert result["events_checked"] == 2
    assert result["resolved"] == 1
    assert result["unresolved"] == 1
    assert result["multiply_matched"] == 0
    assert result["snapshot_coverage"] == {
        "snapshots": 3,
        "matched": 2,
        "unmatched": 1,
        "multiply_matched": 0,
        "raw_observations_without_snapshot": 0,
    }
    assert result["raw_derived_instructor_transitions"] == 1
    assert result["resolved_events"][0] == {
        "change_id": 1,
        "course_code": "CSCI 101",
        "section_code": "1L",
        "old_instructor": "Ada",
        "new_instructor": "Grace",
        "before": {
            "file": "first.xls",
            "observed_at": "2025-12-17 09:00:00",
        },
        "after": {
            "file": "second.xls",
            "observed_at": "2025-12-17 10:00:00",
        },
    }
    assert result["unresolved_events"][0]["change_id"] == 2


def test_raw_evidence_recurses_deduplicates_and_checks_migration_invariants(
    tmp_path: Path,
) -> None:
    first_dir = tmp_path / "first"
    second_dir = tmp_path / "second" / "nested"
    first_dir.mkdir()
    second_dir.mkdir(parents=True)
    files = [
        first_dir / "first.xls",
        second_dir / "first-copy.xls",
        second_dir / "second.xls",
        second_dir / "second-conflict.xls",
    ]
    for path in files:
        path.touch()

    def row(enrollment: int) -> dict[str, object]:
        return {
            "Level": "UG",
            "Cap": 20,
            "Enr": enrollment,
            "Fill": enrollment / 20,
            "Course Abbr": "CSCI 101",
            "Course Title": "Introduction to Computing",
            "S/T": "1L",
            "Instructor": "Ada",
        }

    class FakeReader:
        observations = {
            "first.xls": ("Summer 2026", "2026-05-11 10:00:00", [row(10)]),
            "first-copy.xls": ("Summer 2026", "2026-05-11 10:00:00", [row(10)]),
            "second.xls": ("Summer 2026", "2026-05-11 10:05:00", [row(11)]),
            "second-conflict.xls": (
                "Summer 2026",
                "2026-05-11 10:05:00",
                [row(12)],
            ),
        }

        def read_excel_data(
            self, input_file: str
        ) -> tuple[str, str, list[dict[str, object]]]:
            return self.observations[Path(input_file).name]

    result = evaluate_raw_instructor_evidence(
        [first_dir, tmp_path / "second"],
        "Summer 2026",
        {"section_by_id": {}, "course_by_id": {}},
        [],
        reader=FakeReader(),
        snapshot_timestamps=[
            "2026-05-11 10:00:00",
            "2026-05-11 10:05:00",
        ],
        legacy_snapshots=[
            (1, _snapshot("2026-05-11 10:00:00")),
            (2, _snapshot("2026-05-11 10:05:00", enrollment=11)),
        ],
    )

    assert result["files_scanned"] == 4
    assert result["duplicate_files"] == 1
    assert result["conflicting_timestamp_count"] == 1
    assert result["snapshot_coverage"] == {
        "snapshots": 2,
        "matched": 1,
        "unmatched": 0,
        "multiply_matched": 1,
        "raw_observations_without_snapshot": 0,
    }
    assert result["raw_snapshot_parity"] == {
        "snapshots_compared": 1,
        "matches": 1,
        "mismatches": 0,
        "differences_by_field": {},
        "mismatch_samples": [],
        "fields": [
            "timestamp",
            "semester",
            "overall_fill",
            "course and section presence",
            "enrollment_count",
            "capacity_count",
        ],
    }


def test_failure_injection_proves_restart_restore_and_source_preservation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "source.db"
    store = CheckpointedStateStore(source)
    latest = _snapshot("2026-05-11 10:00:00")
    store.write_snapshot(latest, snapshot_id=1)

    result = evaluate_failure_injection(source, source, latest)

    assert result["source_hash_unchanged"] is True
    assert result["all_passed"] is True
    assert set(result["scenarios"]) == {
        "additive_backfill",
        "dual_write",
        "static_cutover",
        "atomic_replacement",
    }
    assert all(scenario["passed"] for scenario in result["scenarios"].values())
    assert result["scenarios"]["additive_backfill"]["rows_after_restart"] == 1
    assert result["scenarios"]["dual_write"]["integrity_check"] == "ok"
    assert result["scenarios"]["static_cutover"]["restored_previous"] is True
    assert result["scenarios"]["atomic_replacement"]["restored_backup"] is True
