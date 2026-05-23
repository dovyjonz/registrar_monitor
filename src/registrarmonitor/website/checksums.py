"""Checksum computation for incremental website updates."""

import hashlib
import json

from registrarmonitor.data.database_manager import DatabaseManager

from .config import ALL_SEMESTERS, OUTPUT_DIR

CHECKSUMS_FILE = OUTPUT_DIR / ".checksums.json"


def compute_semester_hash(semester: str) -> str:
    """
    Compute a hash representing the current state of semester data.

    Uses snapshot count and last snapshot timestamp as the hash basis.
    This is fast and avoids loading all enrollment data.
    """
    db = DatabaseManager(semester=semester)

    with db.get_connection() as conn:
        cursor = conn.cursor()

        # Get snapshot count and last timestamp
        cursor.execute("""
            SELECT COUNT(*), MAX(timestamp)
            FROM snapshots
        """)
        row = cursor.fetchone()
        snapshot_count = row[0] if row else 0
        last_timestamp = row[1] if row else "none"

    # Combine into hash
    hash_input = f"{semester}:{snapshot_count}:{last_timestamp}"
    return hashlib.md5(hash_input.encode()).hexdigest()[:12]


def load_checksums() -> dict[str, str]:
    """Load stored checksums from file."""
    if not CHECKSUMS_FILE.exists():
        return {}
    try:
        return json.loads(CHECKSUMS_FILE.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def save_checksums(checksums: dict[str, str]) -> None:
    """Save checksums to file."""
    CHECKSUMS_FILE.parent.mkdir(parents=True, exist_ok=True)
    CHECKSUMS_FILE.write_text(json.dumps(checksums, indent=2))


def get_semesters_needing_update(force: bool = False) -> list[str]:
    """
    Determine which semesters need their pages regenerated.

    Args:
        force: If True, return all semesters regardless of checksums

    Returns:
        List of semester names needing update
    """
    if force:
        return list(ALL_SEMESTERS)

    stored = load_checksums()
    needs_update = []

    for semester in ALL_SEMESTERS:
        current_hash = compute_semester_hash(semester)
        stored_hash = stored.get(semester)

        if current_hash != stored_hash:
            needs_update.append(semester)

    return needs_update


def update_checksum(semester: str) -> None:
    """Update the stored checksum for a semester after regeneration."""
    checksums = load_checksums()
    checksums[semester] = compute_semester_hash(semester)
    save_checksums(checksums)
