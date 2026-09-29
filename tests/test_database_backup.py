import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from manage_database import backup_database

from registrarmonitor.data.database_manager import DatabaseManager


def test_backup_includes_committed_wal_data(tmp_path):
    source = tmp_path / "source.db"
    manager = DatabaseManager(str(source))
    destination = tmp_path / "backups" / "enrollment.db"
    with sqlite3.connect(source) as writer:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("CREATE TABLE backup_probe (value TEXT)")
        writer.execute("INSERT INTO backup_probe VALUES ('committed')")
        writer.commit()
        assert backup_database(manager, str(destination)) == 0
        with sqlite3.connect(destination) as restored:
            assert restored.execute("SELECT value FROM backup_probe").fetchall() == [
                ("committed",)
            ]
            assert restored.execute("PRAGMA integrity_check").fetchone() == ("ok",)
            assert restored.execute("PRAGMA foreign_key_check").fetchall() == []


def test_backup_refuses_source_and_existing_destination(tmp_path):
    source = tmp_path / "source.db"
    manager = DatabaseManager(str(source))
    original = source.read_bytes()
    assert backup_database(manager, str(source)) == 1
    assert source.read_bytes() == original
    destination = tmp_path / "existing.db"
    destination.write_bytes(b"keep existing backup")
    assert backup_database(manager, str(destination)) == 1
    assert destination.read_bytes() == b"keep existing backup"


def test_backup_validation_failure_publishes_no_file(tmp_path):
    source = tmp_path / "source.db"
    manager = DatabaseManager(str(source))
    destination = tmp_path / "backups" / "enrollment.db"
    with sqlite3.connect(source) as writer:
        writer.execute("CREATE TABLE backup_parent (id INTEGER PRIMARY KEY)")
        writer.execute(
            "CREATE TABLE backup_child (parent_id REFERENCES backup_parent(id))"
        )
        writer.execute("INSERT INTO backup_child VALUES (1)")
        writer.commit()
    assert backup_database(manager, str(destination)) == 1
    assert not destination.exists()
    assert list(destination.parent.iterdir()) == []
