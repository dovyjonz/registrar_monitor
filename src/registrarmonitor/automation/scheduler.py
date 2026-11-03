"""Poll scheduler.

Polling cadence is declarative: ``Schedule`` holds the registration milestones
from ``settings.toml`` and ``Cadence`` maps the last change score to the next
interval using the tables below.

Quiet mode (default) polls every 5/15/30 minutes by score and backs off by 1.5x
after each silent poll up to 2h by day and 4h by night. A score of 12 or more
enters burst mode (60s at 12+, 10s at 25+, 120s/180s while cooling down), which
ends after three polls under 5. Within 5 minutes before to 30 minutes after a
milestone the interval is at most 5 minutes, and a poll is always aligned to the
next window start or milestone.
"""

import asyncio
import datetime
import json
import subprocess
import sys
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path

from ..config import get_config, get_timezone
from ..core import get_logger
from ..data.database_manager import DatabaseManager
from ..heartbeat import send_heartbeat
from .downloader import DataDownloader

# ReportingService is imported lazily to avoid circular import
# (reporting_service imports the scheduler, scheduler imports ReportingService)
ReportingService = None  # type: ignore[misc, assignment]

# (minimum score, seconds), checked in order.
QUIET_BASE_INTERVALS = ((5, 300), (2, 900), (0, 1800))
BURST_INTERVALS = ((25, 10), (12, 60), (5, 120), (0, 180))
BURST_ENTRY_SCORE = 12.0
BURST_LOW_SCORE = 5.0  # polls below this count toward leaving burst mode
BURST_EXIT_COUNT = 3
QUIET_BACKOFF = 1.5
DAY_HOURS = range(8, 20)
DAY_CAP_SECONDS = 7200
NIGHT_CAP_SECONDS = 14400
RESET_INTERVAL_SECONDS = 300
HOT_WINDOW = (datetime.timedelta(minutes=-5), datetime.timedelta(minutes=30))
HOT_CAP_SECONDS = 300
COOLDOWN_SECONDS = 300.0


def _tier(table: tuple[tuple[int, int], ...], score: float) -> int:
    return next(seconds for minimum, seconds in table if score >= minimum)


def _registrar_datetime(value: str, cfg: dict) -> datetime.datetime:
    """Parse a configured time as registrar-local wall time."""
    parsed = datetime.datetime.fromisoformat(value)
    if parsed.tzinfo is not None:
        return parsed.astimezone(get_timezone(cfg)).replace(tzinfo=None)
    return parsed


def _registrar_now(cfg: dict | None = None) -> datetime.datetime:
    """Return current registrar-local wall time independent of the host timezone."""
    config = cfg if cfg is not None else get_config()
    now = datetime.datetime.now(datetime.UTC).astimezone(get_timezone(config))
    return now.replace(tzinfo=None)


@dataclass(frozen=True)
class Schedule:
    """Registration milestones and deadlines in registrar-local wall time."""

    milestones: tuple[datetime.datetime, ...] = ()

    @classmethod
    def from_config(cls, cfg: dict | None = None) -> "Schedule":
        config = cfg if cfg is not None else get_config()
        times = set()
        for semester in config.get("semesters", {}).values():
            entries = list(semester.get("deadlines", []))
            for milestones in semester.get("priorities", {}).values():
                entries.extend(milestones)
            times.update(_registrar_datetime(entry[0], config) for entry in entries)
        return cls(tuple(sorted(times)))

    def in_hot_zone(self, now: datetime.datetime) -> bool:
        before, after = HOT_WINDOW
        return any(m + before <= now <= m + after for m in self.milestones)

    def seconds_until_next_event(self, now: datetime.datetime) -> float | None:
        """Seconds to the next hot window start or milestone, if any."""
        starts = (m + HOT_WINDOW[0] for m in self.milestones)
        upcoming = [
            (t - now).total_seconds() for t in (*starts, *self.milestones) if t > now
        ]
        return min(upcoming, default=None)


@dataclass
class Decision:
    """Outcome of one cadence step."""

    interval: int
    reset: bool


class Cadence:
    """Quiet/burst polling state machine."""

    def __init__(self) -> None:
        self.mode = "quiet"
        self.consecutive_low = 0
        self.silent_polls = 0
        self.is_day: bool | None = None

    def step(
        self,
        score: float,
        now: datetime.datetime,
        schedule: Schedule,
        *,
        update: bool = True,
    ) -> Decision:
        """Return the wait before the next poll.

        With ``update`` False the interval is re-evaluated without recording the
        score (no new poll has completed since the last step).
        """
        is_day = now.hour in DAY_HOURS
        prev_mode, prev_silent, prev_is_day = self.mode, self.silent_polls, self.is_day
        mode, low, silent = self.mode, self.consecutive_low, self.silent_polls

        if update:
            low = low + 1 if prev_mode == "burst" and score < BURST_LOW_SCORE else 0
            if score >= BURST_ENTRY_SCORE:
                mode = "burst"
            elif prev_mode == "burst" and low >= BURST_EXIT_COUNT:
                mode = "quiet"
            silent = silent + 1 if mode == "quiet" and score == 0.0 else 0

        if mode == "quiet":
            interval = _tier(QUIET_BASE_INTERVALS, score) * (
                QUIET_BACKOFF ** max(0, silent - 1)
            )
        else:
            interval = _tier(BURST_INTERVALS, score)
        interval = min(interval, DAY_CAP_SECONDS if is_day else NIGHT_CAP_SECONDS)

        reset = (
            (score > 0.0 and prev_silent > 0)
            or (prev_is_day is not None and prev_is_day != is_day)
            or mode != prev_mode
        )
        if reset:
            interval = RESET_INTERVAL_SECONDS
            silent = 0
        if schedule.in_hot_zone(now):
            interval = min(interval, HOT_CAP_SECONDS)
        until_event = schedule.seconds_until_next_event(now)
        if until_event is not None:
            interval = min(interval, until_event)

        if update:
            self.mode, self.consecutive_low, self.silent_polls = mode, low, silent
            self.is_day = is_day
        return Decision(max(1, int(interval)), reset)


class _Coalesced:
    """Run one async job at a time; a trigger during a run reruns it once."""

    def __init__(self, name: str, job: Callable[[], Awaitable[object]]):
        self.name = name
        self.job = job
        self.task: asyncio.Task | None = None
        self.pending = False
        self.log = get_logger(__name__)

    def trigger(self) -> None:
        if self.task is None or self.task.done():
            self.task = asyncio.create_task(self._run())
        else:
            self.pending = True

    async def _run(self) -> None:
        while True:
            self.pending = False
            try:
                await self.job()
            except Exception as e:
                self.log.error(f"Error in background {self.name}: {e}")
            if not self.pending:
                return
            self.log.info(f"Another {self.name} is pending. Running it again.")

    async def cancel(self) -> None:
        if self.task and not self.task.done():
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass


def _pending_activity(comparison, current, previous) -> tuple[float, bool]:
    """Score unreported changes and report whether any section changed state."""
    score = len(comparison.new_courses) * 5.0 + len(comparison.removed_courses) * 5.0
    status_changed = bool(comparison.new_courses or comparison.removed_courses)
    for course in comparison.changed_courses:
        score += (len(course.added_sections) + len(course.removed_sections)) * 2.0
        status_changed |= bool(course.added_sections or course.removed_sections)
        now_course = current.courses.get(course.course_code)
        was_course = previous.courses.get(course.course_code)
        for change in course.modified_sections:
            score += (
                abs(
                    (change.current_enrollment or 0) - (change.previous_enrollment or 0)
                )
                / 5.0
            )
            if (
                change.current_capacity is not None
                and change.previous_capacity is not None
                and change.current_capacity != change.previous_capacity
            ):
                score += 3.0
            if now_course and was_course:
                now_sec = now_course.sections.get(change.section_id)
                was_sec = was_course.sections.get(change.section_id)
                if now_sec and was_sec:
                    status_changed |= _is_full(now_sec) != _is_full(was_sec)
    return score, status_changed


def _is_full(section) -> bool:
    return section.capacity > 0 and section.enrollment >= section.capacity


class TwoPhaseScheduler:
    """Poll the registrar on the declarative cadence and trigger reports."""

    def __init__(
        self,
        log_file: str | Path | None = None,
        no_telegram: bool = False,
    ):
        self.no_telegram = no_telegram
        self.logger_ops = get_logger(__name__)
        if log_file is None:
            log_dir = get_config().get("directories", {}).get("logs", "logs")
            log_file = Path(log_dir) / "scheduler_decisions.jsonl"
        self.log_file = Path(log_file)

        self.cadence = Cadence()
        self._new_poll_processed = False
        self._last_poll_time: datetime.datetime | None = None
        self._last_change_score = 0.0
        self._deferred = False  # an update was held back by a cooldown
        self.last_report_sent_time: datetime.datetime | None = None
        self.last_website_updated_time: datetime.datetime | None = None
        self.report_cooldown_seconds = COOLDOWN_SECONDS
        self.website_cooldown_seconds = COOLDOWN_SECONDS

        self._report_cycle_lock = asyncio.Lock()
        self._report_job = _Coalesced(
            "Telegram report", lambda: self._run_report_cycle(force_poll=False)
        )
        self._website_job = _Coalesced(
            "website update", lambda: asyncio.to_thread(self._run_website_update)
        )
        self.reporting_service = None
        self._reporting_service_class = None
        try:
            from ..services.reporting_service import ReportingService as RS

            self._reporting_service_class = RS  # type: ignore[assignment]
        except ImportError:
            print("⚠️  Warning: ReportingService unavailable")

        self.downloader = DataDownloader()
        self.pending_polls: asyncio.Queue = asyncio.Queue()

    @property
    def mode(self) -> str:
        return self.cadence.mode

    def get_next_poll_interval(
        self,
        last_change_score: float = 0,
        timestamp: datetime.datetime | None = None,
        update_state: bool = True,
    ) -> tuple[int, Decision]:
        """Return the wait before the next poll and log the decision."""
        now = timestamp if timestamp is not None else _registrar_now()
        decision = self.cadence.step(
            last_change_score, now, Schedule.from_config(), update=update_state
        )
        self._log_decision(
            {
                "timestamp": now.isoformat(),
                "change_score": last_change_score,
                "mode": self.cadence.mode,
                "consecutive_low": self.cadence.consecutive_low,
                "silent_polls": self.cadence.silent_polls,
                "final_interval_seconds": decision.interval,
                "reset_condition": decision.reset,
            }
        )
        return decision.interval, decision

    def _log_decision(self, entry: dict) -> None:
        """Append one decision to the JSON-lines log."""
        try:
            self.log_file.parent.mkdir(parents=True, exist_ok=True)
            with self.log_file.open("a", encoding="utf-8") as f:
                f.write(json.dumps(entry) + "\n")
        except OSError:
            self.logger_ops.warning(
                "Failed to write scheduler decision log %s", self.log_file
            )

    def _get_next_report_time(self) -> datetime.datetime:
        """Next :15 or :45 in registrar-local wall time."""
        now = _registrar_now()
        hour = now.replace(minute=0, second=0, microsecond=0)
        slots = [
            hour + datetime.timedelta(hours=h, minutes=m)
            for h in (0, 1)
            for m in (15, 45)
        ]
        return min(t for t in slots if t > now)

    def _run_website_update(self):
        """Generate in a disposable process, then deploy the completed output."""
        try:
            from ..services.website_service import WebsiteService

            website_config = get_config().get("website", {})
            project_name = website_config.get("pages_project_name", "registrar-monitor")
            generation_timeout_seconds = website_config["generation_timeout_seconds"]

            print(f"\n🌐 Starting Website Update (Project: {project_name})...")
            service = WebsiteService()
            if not service.is_any_semester_active():
                print("💤 Outside active registration windows. Skipping build/deploy.")
                return
            generation = subprocess.run(
                [sys.executable, "-m", "registrarmonitor.main", "deploy"],
                cwd=Path(__file__).resolve().parents[3],
                check=False,
                timeout=generation_timeout_seconds,
            )
            if generation.returncode != 0:
                self.logger_ops.error(
                    "Website generation child failed exit_code=%s",
                    generation.returncode,
                )
                return

            service.deploy(project_name=project_name)

        except Exception as e:
            print(f"❌ Website update failed: {e}")

    def _cooled_down(self, last, seconds: float, now: datetime.datetime) -> bool:
        return last is None or (now - last).total_seconds() >= seconds

    async def _check_and_trigger_updates(self):
        """Report and publish when unreported snapshots hold significant changes."""
        try:
            from ..cli.utils import detect_active_semester
            from ..data.snapshot_comparator import SnapshotComparator
        except ImportError:
            from registrarmonitor.cli.utils import detect_active_semester
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator

        try:
            self._deferred = False
            semester = await detect_active_semester()
            db_manager = DatabaseManager(semester=semester)
            latest_snapshot_id = db_manager.get_latest_snapshot_id()
            last_reported_id = db_manager.get_last_reported_snapshot_id()
            if not latest_snapshot_id:
                return

            # A single retained snapshot is only a baseline. If history exists,
            # compare the latest state against its first retained snapshot so
            # the initial reporting cycle does not discard changes.
            if not last_reported_id:
                first_snapshot_id = db_manager.get_first_snapshot_id()
                if first_snapshot_id is None or first_snapshot_id == latest_snapshot_id:
                    self.logger_ops.info(
                        "ℹ️  First run detected with one snapshot. Setting the "
                        f"baseline reported snapshot to {latest_snapshot_id}."
                    )
                    db_manager.add_reporting_log(
                        snapshot_id=latest_snapshot_id, changes_were_found=False
                    )
                    return
                self.logger_ops.info(
                    "ℹ️  First run detected with retained history. Comparing "
                    f"latest snapshot {latest_snapshot_id} against first "
                    f"snapshot {first_snapshot_id}."
                )
                last_reported_id = first_snapshot_id

            if latest_snapshot_id == last_reported_id:
                return

            current = db_manager.get_snapshot_data(latest_snapshot_id)
            previous = db_manager.get_snapshot_data(last_reported_id)
            if not current or not previous:
                return

            comparison = SnapshotComparator().compare_snapshots(current, previous)
            score, status_changed = _pending_activity(comparison, current, previous)
            if not (status_changed or score >= 1.0):
                # Minor changes accumulate; the reporting log is not advanced.
                self.logger_ops.info(
                    f"ℹ️  Minor activity detected (Pending Score: {score:.1f}). Accumulating changes."
                )
                return

            self.logger_ops.info(
                f"📢 Significant activity detected (Pending Score: {score:.1f}, Status Change: {status_changed})"
            )
            now = datetime.datetime.now()
            if not self.no_telegram:
                if self._cooled_down(
                    self.last_report_sent_time, self.report_cooldown_seconds, now
                ):
                    self.logger_ops.info("📝 Triggering Telegram Report...")
                    self._report_job.trigger()
                    self.last_report_sent_time = now
                else:
                    self._deferred = True
                    self.logger_ops.info(
                        "⏳ Telegram Report is on cooldown. Will report next cycle."
                    )
            if self._cooled_down(
                self.last_website_updated_time, self.website_cooldown_seconds, now
            ):
                self.logger_ops.info("🌐 Triggering Website Update...")
                self._website_job.trigger()
                self.last_website_updated_time = now
            else:
                self._deferred = True
                self.logger_ops.info(
                    "⏳ Website Update is on cooldown. Will update next cycle."
                )

        except Exception as e:
            self.logger_ops.error(f"❌ Error in check_and_trigger_updates: {e}")

    async def _single_poll_and_process(self) -> float:
        """Download and process the latest file once; return its change score."""
        file_path = await self.downloader.download(skip_unchanged=True)
        if not file_path:
            await send_heartbeat()  # an unchanged file is still a healthy poll
            return 0.0
        return await self._process_file(file_path)

    async def _process_file(self, file_path: str) -> float:
        try:
            from ..cli.commands import PollCommand
        except ImportError:
            from registrarmonitor.cli.commands import PollCommand

        result = await PollCommand(debug=False).run_with_result(file_path=file_path)
        if not result.success:
            # Fetch the same file again next time instead of treating it as seen.
            self.downloader.forget_last_download()
            return 0.0
        await send_heartbeat()
        return result.change_score

    async def _process_pending_polls_loop(self):
        """Process downloads in chronological FIFO order and trigger updates."""
        while True:
            try:
                download_task, poll_start_time = await self.pending_polls.get()
                try:
                    score = 0.0
                    file_path = await download_task
                    if file_path:
                        self.logger_ops.info(
                            f"🔄 Processing poll from {poll_start_time.strftime('%H:%M:%S')}..."
                        )
                        score = await self._process_file(file_path)
                        self._last_poll_time = datetime.datetime.now()
                        await self._check_and_trigger_updates()
                    else:
                        self.logger_ops.info("No registrar changes; skipping parse.")
                        await send_heartbeat()  # still a healthy poll
                        if self._deferred:
                            await self._check_and_trigger_updates()
                    self._last_change_score = score
                    self._new_poll_processed = True
                except Exception as e:
                    self.logger_ops.error(f"❌ Error processing pending poll: {e}")
                    self._last_change_score = 0.0
                    self._new_poll_processed = True
                finally:
                    self.pending_polls.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                print(f"❌ Error in process pending polls loop: {e}")
                await asyncio.sleep(1)

    async def _run_report_cycle(self, force_poll: bool = True) -> float:
        """Serialize stateful reporting across scheduled and event-driven triggers."""
        async with self._report_cycle_lock:
            return await self._run_report_cycle_unlocked(force_poll=force_poll)

    async def _run_report_cycle_unlocked(self, force_poll: bool = True) -> float:
        """Optionally poll, then generate and send the stateful report."""
        print("\n📝 Starting Scheduled Reporting Cycle...")
        if force_poll:
            start_time = time.time()
            change_score = await self._single_poll_and_process()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            self._new_poll_processed = True
            print(
                f"✅ Data fetched ({time.time() - start_time:.1f}s). "
                f"Activity: {change_score:.2f}, Mode: {self.mode}"
            )
        else:
            change_score = self._last_change_score

        if self._reporting_service_class and not self.reporting_service:
            try:
                from ..cli.utils import detect_active_semester
            except ImportError:
                from registrarmonitor.cli.utils import detect_active_semester

            semester = await detect_active_semester()
            self.reporting_service = self._reporting_service_class(semester=semester)
            print(f"📋 Using semester: {semester or 'default'}")

        if self.reporting_service:
            try:
                changes_found = await self.reporting_service.run_stateful_report_cycle(
                    send_telegram=not self.no_telegram,
                    debug_mode=False,
                )
                print(
                    "✅ Report generated and sent."
                    if changes_found
                    else "ℹ️  No significant changes to report."
                )
            except Exception as e:
                print(f"❌ Error during reporting: {e}")
        else:
            print("❌ ReportingService not initialized, skipping report.")
        return change_score

    async def _scheduled_report_loop(self) -> None:
        """Run stateful reports at :15 and :45 without forcing an extra poll."""
        while True:
            next_report_time = self._get_next_report_time()
            await asyncio.sleep(
                max(0.0, (next_report_time - _registrar_now()).total_seconds())
            )
            try:
                await self._run_report_cycle(force_poll=False)
            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger_ops.error(f"Scheduled stateful report failed: {e}")

    async def start(self):
        """Run the polling loop."""
        print("🚀 Starting Two-Phase Scheduler (Event-Driven Polling)")
        schedule = Schedule.from_config()
        upcoming = [m for m in schedule.milestones if m > _registrar_now()]
        print(
            f"   📅 {len(schedule.milestones)} milestones, next: "
            f"{upcoming[0].isoformat() if upcoming else 'none'}"
        )

        print("\n🔄 Performing Initial Sync...")
        try:
            self._last_change_score = await self._single_poll_and_process()
            self._last_poll_time = datetime.datetime.now()
            self._new_poll_processed = True
        except Exception as e:
            print(f"❌ Initial sync failed: {e}")
            self._last_change_score = 0.0
        await self._check_and_trigger_updates()

        processor_task = asyncio.create_task(self._process_pending_polls_loop())
        scheduled_report_task = asyncio.create_task(self._scheduled_report_loop())
        try:
            while True:
                wait, _ = self.get_next_poll_interval(
                    self._last_change_score, update_state=self._new_poll_processed
                )
                self._new_poll_processed = False
                print(
                    f"\n⏱️  Next poll in {wait // 60}m {wait % 60}s   Mode: {self.mode.upper()}"
                )
                sys.stdout.flush()
                await asyncio.sleep(wait)

                download_task = asyncio.create_task(
                    self.downloader.download(skip_unchanged=True)
                )
                await self.pending_polls.put((download_task, datetime.datetime.now()))
        except KeyboardInterrupt:
            print("\n⚠️  Scheduler interrupted by user.")
        finally:
            for task in (processor_task, scheduled_report_task):
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            await self._report_job.cancel()
            await self._website_job.cancel()
            print("📊 Scheduler stopped")
