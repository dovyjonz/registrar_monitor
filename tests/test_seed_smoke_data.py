from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

import pytest

from registrarmonitor.data.database_manager import DatabaseManager
from registrarmonitor.data.migration import MetadataMode
from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.website.config import get_configured_semesters

SEED_SMOKE_DATA_PATH = Path(__file__).parent.parent / "scripts" / "seed_smoke_data.py"
SPEC = spec_from_file_location("seed_smoke_data", SEED_SMOKE_DATA_PATH)
assert SPEC is not None
assert SPEC.loader is not None
seed_smoke_data = module_from_spec(SPEC)
SPEC.loader.exec_module(seed_smoke_data)


def test_main_seeds_every_configured_semester():
    seeded_snapshots = []

    def fake_seed_semester(**kwargs):
        assert kwargs["snapshot"].semester == kwargs["semester"]
        seeded_snapshots.append(kwargs["snapshot"])

    with patch.object(seed_smoke_data, "_seed_semester", fake_seed_semester):
        seed_smoke_data.main(data_dir=Path("data"), report_dir=Path("output"))

    assert {snapshot.semester for snapshot in seeded_snapshots} == set(
        get_configured_semesters()
    )
    assert all(
        len(
            [snapshot for snapshot in seeded_snapshots if snapshot.semester == semester]
        )
        >= 4
        for semester in get_configured_semesters()
    )

    latest_fall = next(
        snapshot
        for snapshot in reversed(seeded_snapshots)
        if snapshot.semester == "Fall 2026"
    )
    assert {
        "ANT 101",
        "ANT 233",
        "BIOL 101",
        "HST 100",
        "HST 104",
        "KAZ 368",
        "LING 131",
        "MATH 161",
    } <= set(latest_fall.courses)
    assert set(latest_fall.courses["MATH 161"].sections) == {"1L", "1R", "3L"}

    latest_summer = next(
        snapshot
        for snapshot in reversed(seeded_snapshots)
        if snapshot.semester == "Summer 2026"
    )
    assert {"ANT 110", "BUS 101", "CHME 403"} <= set(latest_summer.courses)
    assert latest_summer.courses["CHME 403"].is_filled

    historical_codes = {
        snapshot.semester: set(snapshot.courses)
        for snapshot in seeded_snapshots
        if snapshot.semester != "Fall 2026"
    }
    assert all("ANT 233" not in codes for codes in historical_codes.values())


def test_main_builds_fixture_for_configured_storage_modes(tmp_path: Path):
    data_dir = tmp_path / "data"
    seed_smoke_data.main(data_dir=data_dir, report_dir=tmp_path / "reports")

    for semester in get_configured_semesters():
        database = data_dir / (
            f"enrollment_{DatabaseManager._sanitize_semester_name_static(semester)}.db"
        )
        manager = DatabaseManager(db_path=str(database), semester=semester)
        configured_mode, _ = seed_smoke_data._configured_storage(semester)
        assert manager.storage_mode == configured_mode
        latest_snapshot_id = manager.get_latest_snapshot_id()
        assert latest_snapshot_id is not None
        assert latest_snapshot_id >= 4
        snapshot = manager.get_snapshot_data(latest_snapshot_id)
        assert snapshot is not None
        assert {"MATH 161", "KAZ 368"} <= set(snapshot.courses)


@pytest.mark.parametrize("configured_mode", ["v2", "finalized"])
def test_seed_semester_initializes_configured_checkpointed_mode(
    tmp_path: Path,
    configured_mode: str,
):
    snapshot = EnrollmentSnapshot(
        timestamp="2026-07-29 00:00:00",
        semester="Fall 2026",
        overall_fill=0.6,
        courses={
            "TEST 101": Course(
                "TEST 101",
                "TEST",
                {"001": Section("001", "Lecture", 12, 20, 0.6, "Test Instructor")},
                0.6,
            )
        },
    )

    manager = seed_smoke_data._seed_semester(
        semester="Fall 2026",
        snapshot=snapshot,
        data_dir=tmp_path / "data",
        report_dir=tmp_path / "reports",
        configured_mode=configured_mode,
        metadata_mode=MetadataMode.LEGACY_PRESERVING,
    )

    assert manager.storage_mode == configured_mode
    assert manager.get_latest_snapshot_id() == 1
