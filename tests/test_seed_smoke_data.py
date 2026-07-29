from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path
from unittest.mock import patch

from registrarmonitor.website.config import ALL_SEMESTERS

SEED_SMOKE_DATA_PATH = Path(__file__).parent.parent / "scripts" / "seed_smoke_data.py"
SPEC = spec_from_file_location("seed_smoke_data", SEED_SMOKE_DATA_PATH)
assert SPEC is not None
assert SPEC.loader is not None
seed_smoke_data = module_from_spec(SPEC)
SPEC.loader.exec_module(seed_smoke_data)


def test_main_seeds_every_configured_semester():
    stored_snapshots = []

    class FakeDatabaseManager:
        def __init__(self, semester):
            self.semester = semester

        def store_enrollment_snapshot(self, snapshot):
            assert snapshot.semester == self.semester
            stored_snapshots.append(snapshot)

    with patch.object(seed_smoke_data, "DatabaseManager", FakeDatabaseManager):
        seed_smoke_data.main()

    assert [snapshot.semester for snapshot in stored_snapshots] == ALL_SEMESTERS
