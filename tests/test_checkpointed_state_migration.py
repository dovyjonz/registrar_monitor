"""Production migration behavior through its public service interface."""

from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from registrarmonitor.cli.commands import DatabaseCommands
from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.migration import (
    MetadataMode,
    MigrationError,
    MigrationInterrupted,
    MigrationRequest,
    finalize_storage,
    initialize_fresh_storage,
    run_migration,
    transition_storage_mode,
)
from registrarmonitor.data.migration_rehearsal import (
    RehearsalRequest,
    run_rehearsal,
)
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.checksums import compute_semester_hash
from registrarmonitor.website.data import get_semester_data


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _legacy_database(path: Path) -> Path:
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            PRAGMA foreign_keys = ON;
            CREATE TABLE courses (
                course_id INTEGER PRIMARY KEY,
                course_code TEXT NOT NULL UNIQUE,
                course_title TEXT,
                department TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE sections (
                section_id INTEGER PRIMARY KEY,
                course_id INTEGER NOT NULL REFERENCES courses(course_id),
                section_code TEXT NOT NULL,
                section_type TEXT,
                instructor TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP,
                UNIQUE(course_id, section_code)
            );
            CREATE TABLE snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                timestamp TEXT NOT NULL UNIQUE,
                last_seen_at TEXT,
                semester TEXT NOT NULL,
                overall_fill REAL NOT NULL
            );
            CREATE TABLE enrollment_data (
                enrollment_id INTEGER PRIMARY KEY,
                snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
                section_id INTEGER NOT NULL REFERENCES sections(section_id),
                status TEXT NOT NULL,
                enrollment_count INTEGER NOT NULL,
                capacity_count INTEGER NOT NULL,
                fill_percentage REAL NOT NULL,
                UNIQUE(snapshot_id, section_id)
            );
            CREATE TABLE reporting_log (
                report_id INTEGER PRIMARY KEY,
                reported_snapshot_id INTEGER NOT NULL REFERENCES snapshots(snapshot_id),
                report_timestamp TEXT NOT NULL,
                changes_found INTEGER NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE instructor_changes (
                change_id INTEGER PRIMARY KEY,
                section_id INTEGER NOT NULL REFERENCES sections(section_id),
                old_instructor TEXT,
                new_instructor TEXT,
                timestamp TEXT NOT NULL
            );
            INSERT INTO courses(
                course_id, course_code, course_title, department
            ) VALUES (4, 'CSCI 101', 'Computing', 'CSCI');
            INSERT INTO sections(
                section_id, course_id, section_code, section_type, instructor
            ) VALUES (8, 4, '1L', 'L', 'Ada');
            INSERT INTO snapshots VALUES
                (9, '2026-05-01 10:00:00', '2026-05-01 10:02:00',
                 'Summer 2025', 0.5),
                (3, '2026-05-01 10:05:00', NULL, 'Summer 2025', 0.6);
            INSERT INTO enrollment_data VALUES
                (1, 9, 8, 'OPEN', 10, 20, 0.5),
                (2, 3, 8, 'OPEN', 12, 20, 0.6);
            INSERT INTO reporting_log VALUES
                (7, 9, '2026-05-01T10:03:00', 0, '2026-05-01 10:03:00'),
                (11, 3, '2026-05-01T10:06:00', 1, '2026-05-01 10:06:00');
            PRAGMA user_version = 1;
            """
        )
    return path


def _request(
    source: Path,
    tmp_path: Path,
    *,
    dry_run: bool,
) -> MigrationRequest:
    return MigrationRequest(
        database=source,
        semester="Summer 2025",
        target_version=2,
        metadata_mode=MetadataMode.LEGACY_PRESERVING,
        report_path=tmp_path / "migration.json",
        dry_run=dry_run,
        authorized=not dry_run,
        candidate_path=tmp_path / "candidate.db" if dry_run else None,
        backup_dir=tmp_path / "backups",
    )


def test_dry_run_builds_verified_candidate_without_changing_source(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    source_hash = _sha256(source)

    result = run_migration(_request(source, tmp_path, dry_run=True))

    assert result.status == "verified"
    assert _sha256(source) == source_hash
    assert result.candidate_path == tmp_path / "candidate.db"
    with sqlite3.connect(result.candidate_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT snapshot_id, sequence_no FROM state_snapshot "
                "ORDER BY sequence_no"
            )
        ] == [(9, 1), (3, 2)]
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute(
                "SELECT legacy_tables_retained FROM storage_control"
            ).fetchone()[0]
            == 1
        )
    report = json.loads((tmp_path / "migration.json").read_text())
    assert report["source"]["hash_unchanged"] is True
    assert report["verification"]["semantic_mismatches"] == 0
    assert (tmp_path / "migration.md").is_file()


def test_unmarked_legacy_schema_version_zero_is_a_supported_migration_source(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy-v0.db")
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 0")
    source_hash = _sha256(source)

    result = run_migration(
        replace(
            _request(source, tmp_path, dry_run=True),
            candidate_path=tmp_path / "legacy-v0-candidate.db",
        )
    )

    assert result.status == "verified"
    assert _sha256(source) == source_hash
    report = json.loads((tmp_path / "migration.json").read_text())
    assert report["source"]["schema_version"] == 0
    assert report["verification"]["step3_gates"]
    assert all(report["verification"]["step3_gates"].values())
    assert result.candidate_path is not None
    with sqlite3.connect(result.candidate_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2


def test_unmarked_non_legacy_database_is_rejected_before_candidate_creation(
    tmp_path: Path,
) -> None:
    source = tmp_path / "not-a-legacy-database.db"
    with sqlite3.connect(source) as connection:
        connection.execute("PRAGMA user_version = 0")

    with pytest.raises(MigrationError, match="legacy table shape"):
        run_migration(_request(source, tmp_path, dry_run=True))
    assert not (tmp_path / "candidate.db").exists()


def test_dry_run_rejects_source_as_candidate_before_mutation(tmp_path: Path) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    source_hash = _sha256(source)
    request = replace(
        _request(source, tmp_path, dry_run=True),
        candidate_path=source,
    )

    with pytest.raises(
        ValueError,
        match="candidate path must differ from the source database",
    ):
        run_migration(request)

    assert _sha256(source) == source_hash
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_apply_requires_explicit_operator_authorization(tmp_path: Path) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    request = replace(_request(source, tmp_path, dry_run=False), authorized=False)

    with pytest.raises(MigrationError, match="explicit operator authorization"):
        run_migration(request)


def test_fresh_storage_starts_shadow_and_promotes_after_first_dual_write(
    tmp_path: Path,
) -> None:
    database = tmp_path / "fall-2026.db"
    result = initialize_fresh_storage(
        database,
        semester="Fall 2026",
        metadata_mode=MetadataMode.LEGACY_PRESERVING,
        report_path=tmp_path / "fresh.json",
    )

    assert result.status == "initialized"
    assert result.active_mode == "shadow"
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute(
            "SELECT semester, metadata_mode, active_mode, migration_phase "
            "FROM storage_control WHERE singleton = 1"
        ).fetchone() == (
            "Fall 2026",
            "legacy-preserving",
            "shadow",
            "complete",
        )

    manager = DatabaseManager(db_path=str(database), semester="Fall 2026")
    first = replace(
        _changed_snapshot(),
        semester="Fall 2026",
        timestamp="2026-08-02 10:00:00",
    )
    manager.store_enrollment_snapshot(first)

    with sqlite3.connect(database) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 1
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 1
        )
        assert connection.execute(
            "SELECT snapshot_id, sequence_no FROM state_snapshot"
        ).fetchone() == (1, 1)
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    stored = manager.get_snapshot_data(1)
    assert stored is not None
    assert stored.to_dict() == first.to_dict()

    transition_storage_mode(
        database,
        semester="Fall 2026",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    reopened = DatabaseManager(db_path=str(database), semester="Fall 2026")
    assert reopened.storage_mode == "v2"
    stored = reopened.get_snapshot_data(1)
    assert stored is not None
    assert stored.to_dict() == first.to_dict()


def test_migration_rejects_database_as_report_path_before_mutation(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    source_hash = _sha256(source)
    request = replace(
        _request(source, tmp_path, dry_run=False),
        report_path=source,
    )

    with pytest.raises(
        ValueError,
        match="report path must differ from migration database paths",
    ):
        run_migration(request)

    assert _sha256(source) == source_hash
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 1


def test_interrupted_dry_run_resumes_the_same_matching_candidate(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    request = _request(source, tmp_path, dry_run=True)

    def interrupt(phase: str, boundary: str) -> None:
        if (phase, boundary) == ("catalog", "after_commit"):
            raise MigrationInterrupted("injected candidate interruption")

    with pytest.raises(MigrationInterrupted):
        run_migration(request, phase_hook=interrupt)

    result = run_migration(request)

    assert result.status == "verified"
    assert result.candidate_path is not None
    with sqlite3.connect(result.candidate_path) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 2
        )


def test_apply_creates_verified_backup_and_repeated_run_is_a_noop(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    request = _request(source, tmp_path, dry_run=False)

    first = run_migration(request)
    source_hash_after_first = _sha256(source)
    backups_after_first = list((tmp_path / "backups").glob("*.db"))
    second = run_migration(request)

    assert first.status == "applied"
    assert first.backup_path in backups_after_first
    assert first.backup_verified is True
    assert second.status == "already_complete"
    assert _sha256(source) == source_hash_after_first
    assert list((tmp_path / "backups").glob("*.db")) == backups_after_first
    report = json.loads((tmp_path / "migration.json").read_text())
    assert report["verification"]["legacy_observations_unchanged"] is True
    assert all(report["verification"]["step3_gates"].values())
    with sqlite3.connect(source) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 2
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM migration_phase WHERE phase = 'complete'"
            ).fetchone()[0]
            == 1
        )


def test_repeated_run_reconciles_a_legacy_only_chronological_suffix(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    request = _request(source, tmp_path, dry_run=False)
    run_migration(request)
    legacy_manager = DatabaseManager(
        db_path=str(source),
        semester="Summer 2025",
    )

    legacy_manager.store_enrollment_snapshot(_changed_snapshot())
    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 3
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 2
        )

    result = run_migration(request)

    assert result.status == "reconciled"
    with sqlite3.connect(source) as connection:
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 3
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM migration_phase WHERE phase = 'reconciled'"
            ).fetchone()[0]
            == 1
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


def test_interrupted_apply_resumes_without_duplicate_state(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    request = _request(source, tmp_path, dry_run=False)

    def interrupt(phase: str, boundary: str) -> None:
        if (phase, boundary) == ("snapshots:1", "after_commit"):
            raise MigrationInterrupted("injected interruption")

    with pytest.raises(MigrationInterrupted):
        run_migration(request, phase_hook=interrupt)

    resumed = run_migration(request)

    assert resumed.status == "applied"
    with sqlite3.connect(source) as connection:
        assert [
            tuple(row)
            for row in connection.execute(
                "SELECT snapshot_id, sequence_no FROM state_snapshot "
                "ORDER BY sequence_no"
            )
        ] == [(9, 1), (3, 2)]
        assert (
            connection.execute("SELECT count(*) FROM course_change_event").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM section_change_event").fetchone()[
                0
            ]
            == 2
        )
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize(
    ("phase", "boundary"),
    [
        ("schema", "before_commit"),
        ("schema", "after_commit"),
        ("catalog", "before_commit"),
        ("catalog", "after_commit"),
        ("snapshots:1", "before_commit"),
        ("snapshots:1", "after_commit"),
        ("snapshots:2", "before_commit"),
        ("snapshots:2", "after_commit"),
        ("reporting", "before_commit"),
        ("reporting", "after_commit"),
        ("complete", "before_commit"),
        ("complete", "after_commit"),
    ],
)
def test_every_migration_boundary_resumes_to_identical_digest(
    tmp_path: Path,
    phase: str,
    boundary: str,
) -> None:
    baseline = _legacy_database(tmp_path / "baseline.db")
    interrupted = _legacy_database(tmp_path / "interrupted.db")
    baseline_request = _request(baseline, tmp_path / "baseline-run", dry_run=False)
    interrupted_request = _request(
        interrupted,
        tmp_path / "interrupted-run",
        dry_run=False,
    )
    baseline_request.report_path.parent.mkdir()
    interrupted_request.report_path.parent.mkdir()

    run_migration(baseline_request)

    def inject(current_phase: str, current_boundary: str) -> None:
        if (current_phase, current_boundary) == (phase, boundary):
            raise MigrationInterrupted(f"injected at {phase}/{boundary}")

    with pytest.raises(MigrationInterrupted):
        run_migration(interrupted_request, phase_hook=inject)

    resumed = run_migration(interrupted_request)
    assert resumed.status in {"applied", "already_complete"}

    with (
        sqlite3.connect(baseline) as expected,
        sqlite3.connect(interrupted) as actual,
    ):
        expected_digest = expected.execute(
            "SELECT state_digest FROM migration_phase "
            "WHERE target_version = 2 AND phase = 'complete'"
        ).fetchone()[0]
        actual_digest = actual.execute(
            "SELECT state_digest FROM migration_phase "
            "WHERE target_version = 2 AND phase = 'complete'"
        ).fetchone()[0]
        assert actual_digest == expected_digest
        for table in (
            "state_snapshot",
            "course_change_event",
            "section_change_event",
            "state_checkpoint",
            "reporting_log_v2",
        ):
            assert (
                actual.execute(f"SELECT count(*) FROM {table}").fetchone()
                == expected.execute(f"SELECT count(*) FROM {table}").fetchone()
            )
        assert actual.execute("PRAGMA foreign_key_check").fetchall() == []


def test_raw_enriched_requires_unique_observation_and_applies_temporal_metadata(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    raw_dir = tmp_path / "raw"
    raw_dir.mkdir()
    first = raw_dir / "first.xls"
    second = raw_dir / "second.xls"
    first.touch()
    second.touch()

    class RawReader:
        def read_excel_data(self, path: str):
            observed_at = (
                "2026-05-01 10:00:00"
                if Path(path).name == "first.xls"
                else "2026-05-01 10:05:00"
            )
            suffix = "A" if Path(path).name == "first.xls" else "B"
            return (
                "Summer 2025",
                observed_at,
                [
                    {
                        "Level": "UG",
                        "Cap": 20,
                        "Enr": 10 if suffix == "A" else 12,
                        "Fill": 0.5 if suffix == "A" else 0.6,
                        "Course Abbr": "CSCI 101",
                        "Course Title": f"Computing {suffix}",
                        "S/T": "1L",
                        "Instructor": f"Instructor {suffix}",
                    }
                ],
            )

    request = MigrationRequest(
        database=source,
        semester="Summer 2025",
        target_version=2,
        metadata_mode=MetadataMode.RAW_ENRICHED,
        report_path=tmp_path / "raw-migration.json",
        dry_run=True,
        candidate_path=tmp_path / "raw-candidate.db",
        raw_dir=raw_dir,
    )

    result = run_migration(request, excel_reader=RawReader())

    store = __import__(
        "registrarmonitor.data.checkpointed_state",
        fromlist=["CheckpointedStateStore"],
    ).CheckpointedStateStore(result.candidate_path, initialize=False)
    assert (
        store.reconstruct_snapshot(9).courses["CSCI 101"].course_title == "Computing A"
    )
    assert (
        store.reconstruct_snapshot(3).courses["CSCI 101"].sections["1L"].instructor
        == "Instructor B"
    )
    report = json.loads(request.report_path.read_text())
    assert report["raw_evidence"]["matched"] == 2
    assert report["raw_evidence"]["missing"] == 0
    assert report["raw_evidence"]["conflicting"] == 0


def _changed_snapshot(*, capacity: int = 20) -> EnrollmentSnapshot:
    section = Section(
        section_id="1L",
        section_type="L",
        enrollment=14,
        capacity=capacity,
        fill=14 / capacity if capacity else 0.0,
        instructor="Ada",
    )
    return EnrollmentSnapshot(
        timestamp="2026-05-01 10:10:00",
        semester="Summer 2025",
        overall_fill=0.7,
        courses={
            "CSCI 101": Course(
                course_code="CSCI 101",
                department="CSCI",
                sections={"1L": section},
                average_fill=section.fill,
                course_title="Computing",
            )
        },
    )


def test_shadow_dual_write_is_atomic_and_v2_mode_reads_checkpointed_state(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")

    manager.store_enrollment_snapshot(_changed_snapshot())

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 3
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 3
        )
        legacy_id = connection.execute(
            "SELECT snapshot_id FROM snapshots ORDER BY timestamp DESC LIMIT 1"
        ).fetchone()[0]
        v2_id = connection.execute(
            "SELECT snapshot_id FROM state_snapshot ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone()[0]
    assert legacy_id == v2_id

    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    v2_manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    assert v2_manager.storage_mode == "v2"
    assert v2_manager.get_latest_snapshot_id() == v2_id
    actual = v2_manager.get_snapshot_data(v2_id)
    assert actual is not None
    assert actual.to_dict() == _changed_snapshot().to_dict()


def test_dual_write_skips_unchanged_legacy_catalog_updates(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    with sqlite3.connect(source) as connection:
        connection.executescript(
            """
            CREATE TABLE catalog_update_audit(table_name TEXT NOT NULL);
            CREATE TRIGGER courses_update_audit AFTER UPDATE ON courses
            BEGIN
                INSERT INTO catalog_update_audit(table_name) VALUES ('courses');
            END;
            CREATE TRIGGER sections_update_audit AFTER UPDATE ON sections
            BEGIN
                INSERT INTO catalog_update_audit(table_name) VALUES ('sections');
            END;
            """
        )
        connection.commit()

    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    first = _changed_snapshot()
    manager.store_enrollment_snapshot(first)
    second = _changed_snapshot()
    second.timestamp = "2026-05-01 10:15:00"
    second.courses["CSCI 101"].sections["1L"].enrollment = 15
    second.courses["CSCI 101"].sections["1L"].fill = 0.75
    second.overall_fill = 0.75
    manager.store_enrollment_snapshot(second)

    with sqlite3.connect(source) as connection:
        assert (
            connection.execute("SELECT count(*) FROM catalog_update_audit").fetchone()[
                0
            ]
            == 0
        )
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 4
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 4
        )
        assert (
            connection.execute("SELECT count(*) FROM enrollment_data").fetchone()[0]
            == 4
        )


def test_dual_write_rolls_back_v2_when_legacy_compatibility_fails(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")

    def fail_legacy_write(*args: object, **kwargs: object) -> None:
        raise RuntimeError("injected legacy compatibility failure")

    monkeypatch.setattr(manager, "_write_legacy_compatibility", fail_legacy_write)
    with pytest.raises(RuntimeError, match="injected legacy"):
        manager.store_enrollment_snapshot(_changed_snapshot())

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 2
        )
        assert (
            connection.execute("SELECT count(*) FROM course_change_event").fetchone()[0]
            == 1
        )
        assert (
            connection.execute("SELECT count(*) FROM section_change_event").fetchone()[
                0
            ]
            == 2
        )


def test_transaction_rollback_does_not_publish_identity_cache(tmp_path: Path) -> None:
    database = tmp_path / "checkpointed.db"
    store = CheckpointedStateStore(database)
    snapshot = _changed_snapshot()

    with sqlite3.connect(database) as connection:
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("BEGIN IMMEDIATE")
        store.write_snapshot_in_transaction(connection, snapshot)
        connection.rollback()

    snapshot.timestamp = "2026-05-01 10:15:00"
    result = store.write_snapshot(snapshot)

    assert result.created is True
    assert (
        store.reconstruct_snapshot(result.snapshot_id).to_dict() == snapshot.to_dict()
    )


def test_dual_write_preserves_identity_through_removal_and_reappearance(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")

    first = _changed_snapshot()
    manager.store_enrollment_snapshot(first)
    removed = EnrollmentSnapshot(
        timestamp="2026-05-01 10:15:00",
        semester="Summer 2025",
        overall_fill=0.0,
    )
    manager.store_enrollment_snapshot(removed)
    reappeared = _changed_snapshot()
    reappeared.timestamp = "2026-05-01 10:20:00"
    manager.store_enrollment_snapshot(reappeared)

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 5
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 5
        )
        legacy_section_id = connection.execute(
            "SELECT section_id FROM sections WHERE section_code = '1L'"
        ).fetchone()[0]
        v2_section_id = connection.execute(
            "SELECT section_id FROM section_catalog WHERE section_code = '1L'"
        ).fetchone()[0]
        assert legacy_section_id == v2_section_id
        removed_id = connection.execute(
            "SELECT snapshot_id FROM snapshots WHERE timestamp = ?",
            (removed.timestamp,),
        ).fetchone()[0]
        assert (
            connection.execute(
                "SELECT count(*) FROM enrollment_data WHERE snapshot_id = ?",
                (removed_id,),
            ).fetchone()[0]
            == 0
        )

    latest_id = manager.get_latest_snapshot_id()
    assert latest_id is not None
    latest = manager.get_snapshot_data(latest_id)
    assert latest is not None
    assert latest.to_dict() == reappeared.to_dict()


def test_dual_write_deduplicates_identical_poll_in_both_representations(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    first = _changed_snapshot()
    manager.store_enrollment_snapshot(first)
    duplicate = _changed_snapshot()
    duplicate.timestamp = "2026-05-01 10:15:00"
    manager.store_enrollment_snapshot(duplicate)

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 3
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 3
        )
        assert connection.execute(
            "SELECT timestamp, last_seen_at FROM snapshots "
            "ORDER BY snapshot_id DESC LIMIT 1"
        ).fetchone() == (
            first.timestamp,
            duplicate.timestamp,
        )
        assert connection.execute(
            "SELECT observed_at, last_seen_at FROM state_snapshot "
            "ORDER BY sequence_no DESC LIMIT 1"
        ).fetchone() == (
            first.timestamp,
            duplicate.timestamp,
        )


def test_v2_deduplicates_instructor_markup_and_order_only_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpointed.db"
    store = CheckpointedStateStore(database)
    first = _changed_snapshot()
    first.courses["CSCI 101"].sections["1L"].instructor = "Akarca, Halit"
    store.write_snapshot(first)

    equivalent = _changed_snapshot()
    equivalent.timestamp = "2026-05-01 10:15:00"
    equivalent.courses["CSCI 101"].sections["1L"].instructor = "<b>Halit</b> Akarca"
    result = store.write_snapshot(equivalent)

    assert result.created is False
    assert result.section_events == 0
    assert store.statistics()["snapshots"] == 1


def test_v2_records_genuine_instructor_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpointed.db"
    store = CheckpointedStateStore(database)
    first = _changed_snapshot()
    first.courses["CSCI 101"].sections["1L"].instructor = "Ada Lovelace"
    store.write_snapshot(first)

    changed = _changed_snapshot()
    changed.timestamp = "2026-05-01 10:15:00"
    changed.courses["CSCI 101"].sections["1L"].instructor = "Grace Hopper"
    result = store.write_snapshot(changed)

    assert result.created is True
    assert result.section_events == 1


def test_v2_ignores_presentation_only_instructor_change_with_other_changes(
    tmp_path: Path,
) -> None:
    database = tmp_path / "checkpointed.db"
    store = CheckpointedStateStore(database)
    first = _changed_snapshot()
    first.courses["CSCI 101"].sections["1L"].instructor = "Akarca, Halit"
    first.courses["CSCI 101"].sections["2L"] = Section(
        "2L", "L", 10, 20, 0.5, "Ada Lovelace"
    )
    store.write_snapshot(first)

    changed = _changed_snapshot()
    changed.timestamp = "2026-05-01 10:15:00"
    changed.courses["CSCI 101"].sections["1L"].instructor = "<b>Halit</b> Akarca"
    changed.courses["CSCI 101"].sections["2L"] = Section(
        "2L", "L", 11, 20, 0.55, "Ada Lovelace"
    )
    result = store.write_snapshot(changed)

    assert result.created is True
    assert result.section_events == 1
    with store.connection() as connection:
        event = connection.execute(
            """
            SELECT section_code, old_instructor, new_instructor
            FROM section_change_event e
            JOIN section_catalog s ON s.section_id = e.section_id
            WHERE e.snapshot_id = ?
            """,
            (result.snapshot_id,),
        ).fetchone()
    assert tuple(event) == ("2L", "Ada Lovelace", "Ada Lovelace")


def test_v2_mode_can_roll_back_to_legacy_after_dual_write(tmp_path: Path) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    manager.store_enrollment_snapshot(_changed_snapshot())

    result = transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="legacy",
        report_path=tmp_path / "legacy-mode.json",
    )

    assert result.status == "changed"
    assert result.active_mode == "legacy"
    with sqlite3.connect(source) as connection:
        legacy_ids = connection.execute(
            "SELECT snapshot_id FROM snapshots ORDER BY timestamp"
        ).fetchall()
        checkpointed_ids = connection.execute(
            "SELECT snapshot_id FROM state_snapshot ORDER BY sequence_no"
        ).fetchall()
    assert legacy_ids == checkpointed_ids


def test_mode_transition_rejects_v2_only_snapshot_divergence(tmp_path: Path) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    CheckpointedStateStore(source, initialize=False).write_snapshot(
        _changed_snapshot(), snapshot_id=99
    )

    with pytest.raises(MigrationError, match="snapshot identity parity"):
        transition_storage_mode(
            source,
            semester="Summer 2025",
            target_mode="v2",
            report_path=tmp_path / "v2.json",
        )


def _migrated_v2_database(tmp_path: Path) -> Path:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    return source


def test_finalization_requires_authorization_and_retires_legacy_tables(
    tmp_path: Path,
) -> None:
    source = _migrated_v2_database(tmp_path)

    with pytest.raises(MigrationError, match="explicit authorization"):
        finalize_storage(
            source,
            semester="Summer 2025",
            report_path=tmp_path / "finalize.json",
        )

    result = finalize_storage(
        source,
        semester="Summer 2025",
        report_path=tmp_path / "finalize.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )

    assert result.status == "finalized"
    assert result.archive_path.is_file()
    assert result.source_hash_before != result.source_hash_after
    report = json.loads((tmp_path / "finalize.json").read_text())
    assert report["semantic_digest_preserved"] is True
    assert report["operational_evidence"]["website_snapshot_count"] == 2
    assert report["operational_evidence"]["website_course_count"] == 1

    repeated = finalize_storage(
        source,
        semester="Summer 2025",
        report_path=tmp_path / "repeat-finalize.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )
    repeated_report = json.loads((tmp_path / "repeat-finalize.json").read_text())
    assert repeated.status == "already_finalized"
    assert repeated_report["integrity_check"] == "ok"
    assert repeated_report["foreign_key_violations"] == 0
    assert repeated_report["operational_evidence"]["website_snapshot_count"] == 2

    with sqlite3.connect(source) as connection:
        assert connection.execute(
            "SELECT active_mode, migration_phase, legacy_tables_retained "
            "FROM storage_control WHERE singleton = 1"
        ).fetchone() == ("finalized", "finalized", 0)
        assert (
            connection.execute("SELECT count(*) FROM state_checkpoint").fetchone()[0]
            >= 1
        )
        tables = {
            row[0]
            for row in connection.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
    assert not tables.intersection(
        {"courses", "sections", "snapshots", "enrollment_data", "reporting_log"}
    )

    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    assert manager.storage_mode == "finalized"
    assert manager.get_snapshot_data(9) is not None
    assert manager.get_previous_snapshot_id(3) == 9
    assert manager.get_latest_snapshot_last_seen_at() == "2026-05-01 10:05:00"
    assert manager.get_database_stats() == {
        "snapshots": 2,
        "courses": 1,
        "sections": 1,
        "earliest_snapshot": "2026-05-01 10:00:00",
        "latest_snapshot": "2026-05-01 10:05:00",
    }
    assert manager.get_enrollment_summary(3) == {"OPEN": 1, "NEAR": 0, "FULL": 0}

    website_data = get_semester_data("Summer 2025", minify=False, database=manager)
    assert [item["id"] for item in website_data["snapshots"]] == [9, 3]
    assert (
        website_data["courses"]["CSCI 101"]["sections"]["1L"]["currentEnrollment"] == 12
    )
    assert len(compute_semester_hash("Summer 2025", database=manager)) == 12

    manager.store_enrollment_snapshot(_changed_snapshot())
    manager.add_reporting_log(3, True)
    with sqlite3.connect(source) as connection:
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 3
        )
        assert (
            connection.execute("SELECT count(*) FROM reporting_log_v2").fetchone()[0]
            == 3
        )


def test_website_payload_is_equal_across_legacy_v2_and_finalized_reads(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    legacy_manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    legacy_payload = get_semester_data(
        "Summer 2025", minify=False, database=legacy_manager
    )

    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    v2_manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    assert (
        get_semester_data("Summer 2025", minify=False, database=v2_manager)
        == legacy_payload
    )

    finalize_storage(
        source,
        semester="Summer 2025",
        report_path=tmp_path / "finalize.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )
    finalized_manager = DatabaseManager(db_path=str(source), semester="Summer 2025")
    assert (
        get_semester_data("Summer 2025", minify=False, database=finalized_manager)
        == legacy_payload
    )


def test_finalization_reuses_verified_candidate_after_interruption(
    tmp_path: Path,
) -> None:
    source = _migrated_v2_database(tmp_path)

    def interrupt(phase: str, boundary: str) -> None:
        if (phase, boundary) == ("finalization", "before_replace"):
            raise MigrationInterrupted("injected before finalization replacement")

    with pytest.raises(MigrationInterrupted):
        finalize_storage(
            source,
            semester="Summer 2025",
            report_path=tmp_path / "interrupted.json",
            rollback_dir=tmp_path / "rollback",
            authorized=True,
            phase_hook=interrupt,
        )

    candidate = source.with_name(f".{source.name}.finalized")
    assert candidate.is_file()
    with sqlite3.connect(source) as connection:
        assert (
            connection.execute(
                "SELECT migration_phase FROM storage_control WHERE singleton = 1"
            ).fetchone()[0]
            == "finalization:prepared"
        )

    resumed = finalize_storage(
        source,
        semester="Summer 2025",
        report_path=tmp_path / "resumed.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )
    assert resumed.status == "finalized"
    assert not candidate.exists()


def test_repeated_finalization_requires_the_rollback_archive(tmp_path: Path) -> None:
    source = _migrated_v2_database(tmp_path)
    result = finalize_storage(
        source,
        semester="Summer 2025",
        report_path=tmp_path / "finalize.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )
    result.archive_path.unlink()

    with pytest.raises(MigrationError, match="rollback archive"):
        finalize_storage(
            source,
            semester="Summer 2025",
            report_path=tmp_path / "repeat-finalize.json",
            rollback_dir=tmp_path / "rollback",
            authorized=True,
        )


def test_cleanup_is_rejected_in_v2_mode_without_diverging_storage(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="v2",
        report_path=tmp_path / "v2.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")

    with pytest.raises(
        RuntimeError,
        match="snapshot cleanup is only supported in legacy mode",
    ):
        manager.cleanup_old_snapshots(keep_count=1)

    with sqlite3.connect(source) as connection:
        legacy_count = connection.execute("SELECT count(*) FROM snapshots").fetchone()[
            0
        ]
        checkpointed_count = connection.execute(
            "SELECT count(*) FROM state_snapshot"
        ).fetchone()[0]
    assert legacy_count == checkpointed_count == 2


def test_shadow_constraint_failure_rolls_back_legacy_and_v2(
    tmp_path: Path,
) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    run_migration(_request(source, tmp_path, dry_run=False))
    transition_storage_mode(
        source,
        semester="Summer 2025",
        target_mode="shadow",
        report_path=tmp_path / "shadow.json",
    )
    manager = DatabaseManager(db_path=str(source), semester="Summer 2025")

    with pytest.raises(sqlite3.IntegrityError):
        manager.store_enrollment_snapshot(_changed_snapshot(capacity=0))

    with sqlite3.connect(source) as connection:
        assert connection.execute("SELECT count(*) FROM snapshots").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 2
        )


def test_operator_migration_order_requires_completed_prior_semesters(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    config = {
        "storage": {
            "migration_order": ["Summer 2025", "Spring 2025"],
        }
    }
    monkeypatch.setattr(
        "registrarmonitor.cli.commands.get_config",
        lambda: config,
    )

    with pytest.raises(ValueError, match="Summer 2025.*no database"):
        DatabaseCommands._validate_migration_order("Spring 2025", tmp_path)

    prior = tmp_path / "enrollment_summer_2025.db"
    with sqlite3.connect(prior) as connection:
        connection.executescript(
            """
            CREATE TABLE storage_control (
                singleton INTEGER PRIMARY KEY,
                migration_phase TEXT NOT NULL
            );
            INSERT INTO storage_control VALUES (1, 'complete');
            PRAGMA user_version = 2;
            """
        )

    DatabaseCommands._validate_migration_order("Spring 2025", tmp_path)


def test_dry_run_order_accepts_matching_completed_predecessor_candidate(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    evidence_dir = tmp_path / "evidence"
    data_dir.mkdir()
    evidence_dir.mkdir()
    config = {
        "storage": {
            "migration_order": ["Summer 2025", "Spring 2025"],
        }
    }
    monkeypatch.setattr(
        "registrarmonitor.cli.commands.get_config",
        lambda: config,
    )
    prior = _legacy_database(data_dir / "enrollment_summer_2025.db")
    candidate = evidence_dir / "summer-2025-candidate.db"
    run_migration(
        MigrationRequest(
            database=prior,
            semester="Summer 2025",
            target_version=2,
            metadata_mode=MetadataMode.LEGACY_PRESERVING,
            report_path=evidence_dir / "summer-2025.json",
            dry_run=True,
            candidate_path=candidate,
        )
    )

    DatabaseCommands._validate_migration_order(
        "Spring 2025",
        data_dir,
        completed_predecessor_dir=evidence_dir,
    )


def test_migration_order_accepts_finalized_predecessor(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    data_dir = tmp_path / "data"
    data_dir.mkdir()
    config = {
        "storage": {
            "migration_order": ["Summer 2025", "Spring 2025"],
        }
    }
    monkeypatch.setattr(
        "registrarmonitor.cli.commands.get_config",
        lambda: config,
    )
    prior = _migrated_v2_database(data_dir)
    prior.rename(data_dir / "enrollment_summer_2025.db")
    finalize_storage(
        data_dir / "enrollment_summer_2025.db",
        semester="Summer 2025",
        report_path=tmp_path / "finalize.json",
        rollback_dir=tmp_path / "rollback",
        authorized=True,
    )

    DatabaseCommands._validate_migration_order("Spring 2025", data_dir)


def test_rehearsal_recovers_every_real_runner_boundary(tmp_path: Path) -> None:
    source = _legacy_database(tmp_path / "legacy.db")
    source_hash = _sha256(source)
    report_path = tmp_path / "evidence" / "rehearsal.json"
    report = run_rehearsal(
        RehearsalRequest(
            database=source,
            semester="Summer 2025",
            target_version=2,
            metadata_mode=MetadataMode.LEGACY_PRESERVING,
            report_path=report_path,
            evidence_dir=tmp_path / "evidence" / "scenarios",
            workers=2,
            snapshot_stride=96,
        )
    )

    assert report["status"] == "passed"
    assert report["scenario_count"] == 12
    assert report["passed"] == 12
    assert report["failed"] == 0
    assert report["snapshot_stride"] == 96
    assert report["source_hash_unchanged"] is True
    assert _sha256(source) == source_hash
    assert report_path.is_file()
    assert report_path.with_suffix(".md").is_file()
    assert (
        len(list((tmp_path / "evidence" / "scenarios").glob("*-recovery.json"))) == 12
    )
