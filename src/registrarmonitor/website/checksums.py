"""Checksum computation for incremental website updates."""

import hashlib
import json
from pathlib import Path

from registrarmonitor.data.database_manager import DatabaseManager

from .config import OUTPUT_DIR, get_configured_semesters, get_milestones

CHECKSUMS_FILE = OUTPUT_DIR / ".checksums.json"


def compute_semester_hash(
    semester: str,
    *,
    database: DatabaseManager | None = None,
    milestones: list[dict[str, str]] | None = None,
    archived: bool = False,
) -> str:
    """
    Compute a hash representing everything a semester's pages are built from.

    Combines the snapshot count and last snapshot timestamp with the semester's
    milestones and its archived state, so a settings edit or a newer semester
    regenerates the pages even when no snapshot changed. Counting snapshots is
    fast and avoids loading all enrollment data.

    Call this before reading the data being published: a snapshot stored while
    the build runs then makes the next run regenerate instead of being recorded
    as already published.
    """
    db = database or DatabaseManager(semester=semester)
    with db.get_connection() as conn:
        row = conn.execute(
            "SELECT count(*), max(observed_at) FROM state_snapshot"
        ).fetchone()
        snapshot_count = row[0] if row else 0
        last_timestamp = row[1] if row else "none"

    if milestones is None:
        milestones = get_milestones(semester)
    milestone_state = json.dumps(milestones, sort_keys=True, separators=(",", ":"))
    hash_input = (
        f"{semester}:{snapshot_count}:{last_timestamp}:"
        f"{'archived' if archived else 'current'}:{milestone_state}"
    )
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
    content = json.dumps(checksums, indent=2)
    if checksums_file.is_file() and checksums_file.read_text() == content:
        return
    checksums_file.write_text(content)


def get_semesters_needing_update(
    force: bool = False,
    checksums_file: Path | None = None,
    *,
    semesters: list[str] | None = None,
) -> list[str]:
    """
    Determine which semesters need their pages regenerated.

    Args:
        force: If True, return all semesters regardless of checksums

    Returns:
        List of semester names needing update
    """
    candidates = semesters if semesters is not None else get_configured_semesters()
    if force:
        return list(candidates)

    stored = load_checksums(checksums_file or CHECKSUMS_FILE)
    needs_update = []

    for position, semester in enumerate(candidates):
        # Candidates are newest first; every semester but the newest is archived.
        current_hash = compute_semester_hash(semester, archived=position > 0)
        stored_hash = stored.get(semester)

        if current_hash != stored_hash:
            needs_update.append(semester)

    return needs_update


def update_checksum(
    semester: str, checksum: str, checksums_file: Path | None = None
) -> None:
    """Record the checksum computed before a semester's data was read."""
    checksums_file = checksums_file or CHECKSUMS_FILE
    checksums = load_checksums(checksums_file)
    checksums[semester] = checksum
    save_checksums(checksums, checksums_file)
