"""Checkpointed v2 storage through its public interfaces."""

from __future__ import annotations

import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from registrarmonitor.data.checkpointed_state import CheckpointedStateStore
from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.models import Course, EnrollmentSnapshot, Section


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


def test_fresh_database_starts_v2_only(tmp_path: Path) -> None:
    database = tmp_path / "fall-2026.db"
    manager = DatabaseManager(db_path=str(database), semester="Fall 2026")
    first = replace(
        _changed_snapshot(),
        semester="Fall 2026",
        timestamp="2026-08-02 10:00:00",
    )
    manager.store_enrollment_snapshot(first)

    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 1
        )
        old_tables = connection.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name IN ('courses','sections','snapshots','enrollment_data',"
            "'reporting_log','instructor_changes')"
        ).fetchall()
        assert old_tables == []
        assert connection.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []
    stored = manager.get_snapshot_data(1)
    assert stored is not None
    assert stored.to_dict() == first.to_dict()

    reopened = DatabaseManager(db_path=str(database), semester="Fall 2026")
    stored = reopened.get_snapshot_data(1)
    assert stored is not None
    assert stored.to_dict() == first.to_dict()


def test_removal_and_reappearance_keep_section_identity(tmp_path: Path) -> None:
    database = tmp_path / "removal.db"
    manager = DatabaseManager(db_path=str(database), semester="Summer 2025")

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

    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 3
        )
        assert (
            connection.execute(
                "SELECT count(*) FROM section_catalog WHERE section_code = '1L'"
            ).fetchone()[0]
            == 1
        )

    latest_id = manager.get_latest_snapshot_id()
    assert latest_id is not None
    latest = manager.get_snapshot_data(latest_id)
    assert latest is not None
    assert latest.to_dict() == reappeared.to_dict()


def test_identical_poll_only_advances_last_seen(tmp_path: Path) -> None:
    database = tmp_path / "duplicate.db"
    manager = DatabaseManager(db_path=str(database), semester="Summer 2025")
    first = _changed_snapshot()
    manager.store_enrollment_snapshot(first)
    duplicate = _changed_snapshot()
    duplicate.timestamp = "2026-05-01 10:15:00"
    manager.store_enrollment_snapshot(duplicate)

    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 1
        )
        assert connection.execute(
            "SELECT observed_at, last_seen_at FROM state_snapshot"
        ).fetchone() == (first.timestamp, duplicate.timestamp)

    # Replaying an older identical file never moves last_seen_at backwards.
    older = _changed_snapshot()
    older.timestamp = first.timestamp
    manager.store_enrollment_snapshot(older)
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "SELECT last_seen_at FROM state_snapshot"
        ).fetchone() == (duplicate.timestamp,)


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


@pytest.mark.parametrize(
    ("first_instructor", "equivalent_instructor"),
    [
        ("Park, Chun Young", "Chun Young Park"),
        ("Arif, Syed Muhammad Umair", "Syed Muhammad Umair Arif"),
        ("Petrikin Jr, Ian Albert", "Ian Albert Petrikin Jr"),
        ("Mun, Ellina, Elkamhawy, Ahmed", "Ellina Mun, Ahmed Elkamhawy"),
    ],
)
def test_v2_does_not_event_formatting_only_instructor_changes(
    tmp_path: Path,
    first_instructor: str,
    equivalent_instructor: str,
) -> None:
    database = tmp_path / "checkpointed.db"
    store = CheckpointedStateStore(database)
    first = _changed_snapshot()
    first.courses["CSCI 101"].sections["1L"].instructor = first_instructor
    store.write_snapshot(first)

    equivalent = _changed_snapshot()
    equivalent.timestamp = "2026-05-01 10:15:00"
    equivalent.courses["CSCI 101"].sections["1L"].instructor = equivalent_instructor
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


def test_older_changed_file_is_refused_without_writing(tmp_path: Path) -> None:
    from registrarmonitor.data.checkpointed_state import OutOfOrderSnapshotError

    database = tmp_path / "order.db"
    manager = DatabaseManager(db_path=str(database), semester="Summer 2025")
    first = _changed_snapshot()
    manager.store_enrollment_snapshot(first)
    older = _changed_snapshot()
    older.timestamp = "2020-01-01 00:00:00"
    older.overall_fill = first.overall_fill + 1

    with pytest.raises(OutOfOrderSnapshotError, match="not newer"):
        manager.store_enrollment_snapshot(older)
    with sqlite3.connect(database) as connection:
        assert (
            connection.execute("SELECT count(*) FROM state_snapshot").fetchone()[0] == 1
        )
