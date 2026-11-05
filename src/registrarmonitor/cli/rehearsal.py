"""Rehearse one poll and report cycle on a scratch copy of a semester database.

The copy lives in a scratch directory that replaces every configured data path
(``DIRECTORIES__*`` environment overrides), while Telegram, Cloudflare,
heartbeat, and Sentry credentials are blanked for the duration. Nothing is
posted, queued, or deployed; the live database files are fingerprinted before
and after to prove they did not change.
"""

import os
import shutil
import sqlite3
import tempfile
from collections.abc import Generator
from contextlib import closing, contextmanager
from pathlib import Path

from ..config import Settings, get_settings
from ..core import get_logger
from ..data.database_manager import DatabaseManager
from ..models import EnrollmentSnapshot
from ..services import ReportingService
from .commands import PollCommand
from .utils import detect_active_semester

# Blank values count as unset, and the environment outranks ``.env``.
CLEARED_ENVIRONMENT = (
    "TELEGRAM_BOT_TOKEN",
    "TELEGRAM_CHAT_ID",
    "TELEGRAM_BOT_TEST_USER_ID",
    "TELEGRAM_WEBHOOK_URL",
    "TELEGRAM_WEBHOOK_SECRET",
    "CLOUDFLARE_API_TOKEN",
    "CLOUDFLARE_ACCOUNT_ID",
    "HEARTBEAT_URL",
    "SENTRY_DSN",
)

Fingerprint = dict[str, tuple[int, int]]


def semester_database_path(settings: Settings, semester: str) -> Path:
    slug = DatabaseManager._sanitize_semester_name_static(semester)
    return Path(settings.directories.data_storage) / f"enrollment_{slug}.db"


def fingerprint(settings: Settings) -> Fingerprint:
    """Size and mtime of every live enrollment and bot database file.

    ``-shm`` files and empty ``-wal`` files appear when a reader merely opens a
    WAL database, so they are not data and are skipped.
    """
    paths = list(Path(settings.directories.data_storage).glob("enrollment_*.db*"))
    bot = Path(settings.directories.telegram_bot_state)
    paths += [bot, bot.with_name(bot.name + "-wal")]
    found: Fingerprint = {}
    for path in sorted(set(paths)):
        if path.name.endswith("-shm") or not path.is_file():
            continue
        status = path.stat()
        if path.name.endswith("-wal") and status.st_size == 0:
            continue
        found[str(path)] = (status.st_size, status.st_mtime_ns)
    return found


def copy_database(source: Path, target: Path) -> None:
    """Consistent copy of a live SQLite database through a read-only connection."""
    target.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(f"file:{source}?mode=ro", uri=True)) as live:
        with closing(sqlite3.connect(target)) as scratch:
            live.backup(scratch)


@contextmanager
def scratch_settings(root: Path) -> Generator[Settings]:
    """Point every data path at ``root`` and clear credentials, then restore."""
    overrides = {
        "DIRECTORIES__DATA_STORAGE": str(root / "data"),
        "DIRECTORIES__TEXT_REPORTS": str(root / "changes"),
        "DIRECTORIES__RAW_DOWNLOADS": str(root / "downloads"),
        "DIRECTORIES__LOGS": str(root / "logs"),
        "DIRECTORIES__TELEGRAM_BOT_STATE": str(root / "telegram_bot.db"),
        **dict.fromkeys(CLEARED_ENVIRONMENT, ""),
    }
    saved = {name: os.environ.get(name) for name in overrides}
    os.environ.update(overrides)
    get_settings.cache_clear()
    try:
        settings = get_settings()
        if (
            Path(settings.directories.data_storage) != root / "data"
            or settings.telegram_bot_token is not None
            or settings.telegram_chat_id is not None
            or settings.cloudflare_api_token is not None
        ):
            raise RuntimeError("scratch settings were not applied; refusing to run")
        yield settings
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        get_settings.cache_clear()


class RehearseCommand:
    """Run one poll and report cycle against a scratch copy of the database."""

    def __init__(self, debug: bool = False, keep: bool = False):
        self.debug = debug
        self.keep = keep
        self.logger = get_logger(__name__)

    async def run(self, semester: str | None = None) -> bool:
        live_settings = get_settings()
        try:
            semester = semester or await detect_active_semester(self.debug)
            if not semester:
                raise ValueError("no semester database found; pass --semester")
            live = semester_database_path(live_settings, semester)
            if not live.is_file():
                raise ValueError(f"no database for {semester!r} at {live}")
            before = fingerprint(live_settings)
            scratch = Path(tempfile.mkdtemp(prefix="registrar-rehearsal-"))
            try:
                return await self._rehearse(
                    semester, live, scratch, before, live_settings
                )
            finally:
                if self.keep:
                    print(f"Scratch directory kept: {scratch}")
                else:
                    shutil.rmtree(scratch, ignore_errors=True)
        except Exception as error:
            print(f"❌ Rehearsal failed: {error}")
            self.logger.error(f"Rehearsal error: {error}")
            return False

    async def _rehearse(
        self,
        semester: str,
        live: Path,
        scratch: Path,
        before: Fingerprint,
        live_settings: Settings,
    ) -> bool:
        print(f"🧪 Rehearsing {semester} on a scratch copy of {live.name}")
        with scratch_settings(scratch) as settings:
            copy_database(live, semester_database_path(settings, semester))
            poll = await PollCommand(debug=self.debug).run_with_result(
                semester=semester
            )
            changes = False
            if poll.success:
                changes = await ReportingService(
                    semester=semester
                ).run_stateful_report_cycle(send_telegram=False)
            reports = list(Path(settings.directories.text_reports).glob("*.txt"))
            report_bytes = sum(path.stat().st_size for path in reports)

        untouched = fingerprint(live_settings) == before
        snapshot = poll.snapshot
        print(f"\nRehearsal summary for {semester}")
        print(f"   Download and parse: {'ok' if poll.success else 'FAILED'}")
        if isinstance(snapshot, EnrollmentSnapshot):
            sections = sum(len(course.sections) for course in snapshot.courses.values())
            print(f"   Parsed: {len(snapshot.courses)} courses, {sections} sections")
            stored = (
                f"stored as {poll.snapshot_id_after}"
                if poll.changed
                else f"unchanged (still {poll.snapshot_id_after})"
            )
            print(f"   Snapshot: {stored}")
        if poll.success:
            report = f"{report_bytes} bytes" if changes else "no reportable changes"
            print(f"   Report: {report} (generated locally, nothing sent)")
        print(f"   Live database: {'unchanged' if untouched else 'CHANGED'}")
        return poll.success and untouched
