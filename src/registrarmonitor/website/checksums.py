"""Checksum computation for incremental website updates."""

import hashlib
import json
from pathlib import Path

from registrarmonitor.data.database_manager import DatabaseManager

from .config import ALL_SEMESTERS, OUTPUT_DIR

CHECKSUMS_FILE = OUTPUT_DIR / ".checksums.json"


def compute_semester_hash(
    semester: str, *, database: DatabaseManager | None = None
) -> str:
    """
    Compute a hash representing the current state of semester data.

    Uses snapshot count and last snapshot timestamp as the hash basis.
    This is fast and avoids loading all enrollment data.
    """
    db = database or DatabaseManager(semester=semester)
    if db.storage_mode in {"v2", "finalized"}:
        table = "state_snapshot"
        timestamp_column = "observed_at"
    else:
        table = "snapshots"
        timestamp_column = "timestamp"

    with db.get_connection() as conn:
        row = conn.execute(
            f"SELECT count(*), max({timestamp_column}) FROM {table}"
        ).fetchone()
        snapshot_count = row[0] if row else 0
        last_timestamp = row[1] if row else "none"

    # Combine into hash
    hash_input = f"{semester}:{snapshot_count}:{last_timestamp}"
    return hashlib.md5(hash_input.encode()).hexdigest()[:12]


def load_checksums(checksums_file: Path | None = None) -> dict[str, str]:
    """Load stored checksums from file."""
    checksums_file = checksums_file or CHECKSUMS_FILE
    if not checksums_file.exists():
        return {}
    try:
        return json.loads(checksums_file.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_checksums(
    checksums: dict[str, str], checksums_file: Path | None = None
) -> None:
    """Save checksums to file."""
    checksums_file = checksums_file or CHECKSUMS_FILE
    checksums_file.parent.mkdir(parents=True, exist_ok=True)
    checksums_file.write_text(json.dumps(checksums, indent=2))


def get_semesters_needing_update(
    force: bool = False, checksums_file: Path | None = None
) -> list[str]:
    """
    Determine which semesters need their pages regenerated.

    Args:
        force: If True, return all semesters regardless of checksums

    Returns:
        List of semester names needing update
    """
    if force:
        return list(ALL_SEMESTERS)

    stored = load_checksums(checksums_file or CHECKSUMS_FILE)
    needs_update = []

    for semester in ALL_SEMESTERS:
        current_hash = compute_semester_hash(semester)
        stored_hash = stored.get(semester)

        if current_hash != stored_hash:
            needs_update.append(semester)

    return needs_update


def update_checksum(semester: str, checksums_file: Path | None = None) -> None:
    """Update the stored checksum for a semester after regeneration."""
    checksums_file = checksums_file or CHECKSUMS_FILE
    checksums = load_checksums(checksums_file)
    checksums[semester] = compute_semester_hash(semester)
    save_checksums(checksums, checksums_file)
