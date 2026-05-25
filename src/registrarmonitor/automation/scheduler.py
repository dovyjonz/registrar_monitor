import asyncio
import datetime
import json
from collections import deque
import os
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path


from ..config import get_config
from ..data.database_manager import DatabaseManager
from .downloader import DataDownloader
from ..core import get_logger

# ReportingService is imported lazily to avoid circular import
# (reporting_service imports TwoPhaseScheduler, scheduler imports ReportingService)
ReportingService = None  # type: ignore[misc, assignment]


def get_current_time_str() -> str:
    """Get current time as formatted string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SchedulingLevel(Enum):
    """Unified enum for scheduling levels (zones/tiers).

    Each level has a string label and an interval in seconds.
    """

    HOT = ("hot", 300)  # 5 minutes - Inside active windows
    SLEEP = ("sleep", 3600)  # 1 hour - Outside all windows

    def __init__(self, label: str, interval: int):
        self._label = label
        self._interval = interval

    @property
    def label(self) -> str:
        """String label used in schedule.txt (e.g., 'hot', 'sleep')."""
        return self._label

    @property
    def interval(self) -> int:
        """Polling interval in seconds."""
        return self._interval

    @classmethod
    def from_label(cls, label: str) -> "SchedulingLevel":
        """Create SchedulingLevel from string label."""
        for level in cls:
            if level.label == label.lower():
                return level
        raise ValueError(f"Unknown scheduling level: {label}")

    @classmethod
    def from_score(cls, score: float) -> "SchedulingLevel":
        """Determine scheduling level from activity score."""
        if score >= 1.0:
            return cls.HOT
        else:
            return cls.SLEEP

    def is_more_urgent_than(self, other: "SchedulingLevel") -> bool:
        """Check if this level is more urgent (shorter interval) than another."""
        return self._interval < other._interval


# Cache storage
# Key: absolute file path
# Value: dict with keys:
#   - 'data': The parsed zones dict
#   - 'mtime': The modification time of the file
#   - 'last_check': Timestamp of the last check (for TTL)
_SCHEDULE_CACHE = {}
_CACHE_TTL = 60  # seconds


def merge_time_windows(
    windows: list[tuple[datetime.datetime, datetime.datetime]],
) -> list[tuple[datetime.datetime, datetime.datetime]]:
    """Sort and merge overlapping time windows."""
    if not windows:
        return []
    sorted_windows = sorted(windows, key=lambda x: x[0])
    merged = [sorted_windows[0]]
    for current_start, current_end in sorted_windows[1:]:
        last_start, last_end = merged[-1]
        if current_start <= last_end:
            merged[-1] = (last_start, max(last_end, current_end))
        else:
            merged.append((current_start, current_end))
    return merged


def parse_schedule_file(
    force_reload: bool = False,
) -> dict[SchedulingLevel, list[tuple[datetime.datetime, datetime.datetime]]]:
    """
    Build scheduler zones from milestones and deadlines defined in settings.toml.
    Both deadlines and milestones are treated as milestones and mapped to the HOT zone.

    HOT zone window: [time - 5 min, time + 30 min]
    """
    is_mocked = (
        hasattr(get_config, "mock_add_spec") or "Mock" in type(get_config).__name__
    )
    abs_path = os.path.abspath("settings.toml")
    now = time.time()

    # ---- cache check (same logic as before, keyed on settings.toml) ----
    if not force_reload and not is_mocked and abs_path in _SCHEDULE_CACHE:
        cache_entry = _SCHEDULE_CACHE[abs_path]
        if now - cache_entry["last_check"] < _CACHE_TTL:
            return cache_entry["data"]
        try:
            current_mtime = os.path.getmtime(abs_path)
            if current_mtime == cache_entry["mtime"]:
                cache_entry["last_check"] = now
                return cache_entry["data"]
        except OSError:
            pass

    # ---- load settings.toml ----
    zones: dict[SchedulingLevel, list[tuple[datetime.datetime, datetime.datetime]]] = {
        zone_type: [] for zone_type in SchedulingLevel
    }

    current_mtime = 0.0
    try:
        current_mtime = os.path.getmtime(abs_path)
    except OSError:
        pass

    try:
        cfg = get_config()
        semesters = cfg.get("semesters", {})

        milestone_times = []
        for sem_name, sem_data in semesters.items():
            if not isinstance(sem_data, dict):
                continue

            priorities = sem_data.get("priorities", {})
            for p_list in priorities.values():
                for m_data in p_list:
                    try:
                        milestone_times.append(
                            datetime.datetime.fromisoformat(m_data[0])
                        )
                    except (IndexError, ValueError) as e:
                        print(f"Warning: skipping milestone {m_data}: {e}")

            for d_data in sem_data.get("deadlines", []):
                try:
                    milestone_times.append(datetime.datetime.fromisoformat(d_data[0]))
                except (IndexError, ValueError) as e:
                    print(f"Warning: skipping deadline {d_data}: {e}")

        # Map milestones/deadlines to HOT window: [T - 5 min, T + 30 min]
        hot_windows = []
        for t in milestone_times:
            start = t - datetime.timedelta(minutes=5)
            end = t + datetime.timedelta(minutes=30)
            hot_windows.append((start, end))

        zones[SchedulingLevel.HOT] = merge_time_windows(hot_windows)

    except FileNotFoundError:
        print("settings.toml not found. Using default scheduling.")
    except Exception as e:
        print(f"Error reading settings.toml for schedule: {e}")

    # ---- update cache ----
    if not is_mocked and current_mtime > 0:
        _SCHEDULE_CACHE[abs_path] = {
            "data": zones,
            "mtime": current_mtime,
            "last_check": now,
        }

    return zones


def get_next_zone_start(
    now: datetime.datetime | None = None,
) -> datetime.datetime | None:
    """
    Find the start time of the next scheduled HOT zone window after now.
    """
    if now is None:
        now = datetime.datetime.now()
    zones = parse_schedule_file()
    next_start = None

    for start_time, _end_time in zones.get(SchedulingLevel.HOT, []):
        if start_time > now:
            if next_start is None or start_time < next_start:
                next_start = start_time

    return next_start


def get_current_zone_type(now: datetime.datetime | None = None) -> SchedulingLevel:
    """
    Determine the current scheduling level based on milestones in settings.toml.
    """
    if now is None:
        now = datetime.datetime.now()
    zones = parse_schedule_file()

    # Check if inside any HOT window
    for start_time, end_time in zones.get(SchedulingLevel.HOT, []):
        if start_time <= now <= end_time:
            return SchedulingLevel.HOT

    return SchedulingLevel.SLEEP


async def poll_and_get_change_score() -> float:
    """
    Polls the system and calculates a change score based on activity.

    Returns:
        Float score representing activity level:
        - 0: No changes
        - 1-9: Low to medium activity
        - 10-29: High activity
        - 30+: Extreme activity
    """
    try:
        # Import here to avoid circular imports
        try:
            from ..cli.commands import PollCommand
            from ..data.snapshot_comparator import SnapshotComparator
            from ..services.monitoring_service import MonitoringService
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.cli.commands import PollCommand
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator
            from registrarmonitor.services.monitoring_service import MonitoringService
            from registrarmonitor.cli.utils import detect_active_semester

        detected_semester = await detect_active_semester()
        db_manager = DatabaseManager(semester=detected_semester)

        # Get latest snapshot ID before poll to detect identical deduplication
        latest_id_before = db_manager.get_latest_snapshot_id()

        # Run only the polling command
        poll_command = PollCommand(debug=False)
        success = await poll_command.run()
        if not success:
            return 0.0

        latest_id_after = db_manager.get_latest_snapshot_id()
        if latest_id_before is not None and latest_id_before == latest_id_after:
            # Snapshot was completely identical, so database timestamp was updated
            # and no new snapshot ID was generated. Return 0.0 change score.
            return 0.0

        # Calculate change score based on the comparison
        monitoring_service = MonitoringService(semester=detected_semester)
        comparator = SnapshotComparator()

        # Get the latest two snapshots for comparison from the database
        latest_snapshot, previous_snapshot = (
            monitoring_service.get_snapshot_comparison()
        )
        if not latest_snapshot:
            return 0.0

        if not previous_snapshot:
            # First snapshot, consider it low activity
            return 1.0

        # Compare snapshots and calculate score
        comparison = comparator.compare_snapshots(latest_snapshot, previous_snapshot)

        score = 0.0

        # Points for structural changes
        score += len(comparison.new_courses) * 5.0  # New courses are significant
        score += (
            len(comparison.removed_courses) * 5.0
        )  # Removed courses are significant

        # Points for course changes
        for course_change in comparison.changed_courses:
            # Points for section changes
            score += len(course_change.added_sections) * 2.0
            score += len(course_change.removed_sections) * 2.0

            # Points for enrollment changes in sections
            for section_change in course_change.modified_sections:
                enrollment_delta = (
                    abs(
                        section_change.current_enrollment
                        - section_change.previous_enrollment
                    )
                    if section_change.current_enrollment is not None
                    and section_change.previous_enrollment is not None
                    else 0
                )

                # Scale enrollment changes (1 point per 5 students)
                score += enrollment_delta / 5.0

                # Bonus for capacity changes
                if (
                    section_change.current_capacity is not None
                    and section_change.previous_capacity is not None
                ):
                    if (
                        section_change.current_capacity
                        != section_change.previous_capacity
                    ):
                        score += 3.0

                # Bonus for instructor changes
                if (
                    section_change.current_instructor
                    != section_change.previous_instructor
                ):
                    score += 1.0

        return min(score, 100.0)  # Cap at 100 for sanity

    except Exception as e:
        print(f"ERROR: Failed to calculate change score: {e}")
        return 0.0


class SchedulingDecision:
    """Represents a scheduling decision for logging."""

    def __init__(
        self,
        timestamp: datetime.datetime,
        change_score: float,
        current_heat: float,
        baseline_level: SchedulingLevel,
        reactive_level: SchedulingLevel,
        final_level: SchedulingLevel,
        final_interval: int,
    ):
        self.timestamp = timestamp
        self.change_score = change_score
        self.current_heat = current_heat
        self.baseline_level = baseline_level
        self.reactive_level = reactive_level
        self.final_level = final_level
        self.final_interval = final_interval

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "change_score": self.change_score,
            "current_heat": round(self.current_heat, 2),
            "baseline_level": self.baseline_level.label,
            "reactive_level": self.reactive_level.label,
            "final_level": self.final_level.label,
            "final_interval_seconds": self.final_interval,
            "final_interval_minutes": round(self.final_interval / 60, 2),
        }


class DecisionLogger:
    """Logs scheduling decisions for later inspection."""

    def __init__(self, log_file: str = "scheduler_decisions.log"):
        self.log_file = Path(log_file)
        self.ensure_log_file_exists()

    def ensure_log_file_exists(self):
        """Create log file if it doesn't exist."""
        if not self.log_file.exists():
            self.log_file.touch()

    def log_decision(self, decision: "SchedulingDecision | TwoPhaseDecision"):
        """Log a scheduling decision."""
        try:
            with open(self.log_file, "a") as f:
                json.dump(decision.to_dict(), f)
                f.write("\n")
        except Exception as e:
            print(f"WARNING: Failed to log decision: {e}")

    def get_recent_decisions(self, count: int = 10) -> list[dict]:
        """Get the most recent scheduling decisions."""
        decisions = []
        try:
            with open(self.log_file, "r") as f:
                # Use deque to only keep the last `count` lines in memory
                last_lines = deque(f, maxlen=count)

            for line in last_lines:
                line = line.strip()
                if line:
                    decisions.append(json.loads(line))
            return decisions
        except Exception as e:
            print(f"WARNING: Failed to read decisions: {e}")
            return []


class TwoPhaseDecision:
    """Represents a two-phase scheduling decision for logging."""

    def __init__(
        self,
        timestamp: datetime.datetime,
        change_score: float,
        mode: str,
        consecutive_low: int,
        decay_counter: int,
        baseline_level: SchedulingLevel,
        final_interval: int,
        reset_condition: bool = False,
    ):
        self.timestamp = timestamp
        self.change_score = change_score
        self.mode = mode
        self.consecutive_low = consecutive_low
        self.decay_counter = decay_counter
        self.baseline_level = baseline_level
        self.final_interval = final_interval
        self.reset_condition = reset_condition

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "change_score": self.change_score,
            "mode": self.mode,
            "consecutive_low": self.consecutive_low,
            "decay_counter": self.decay_counter,
            "baseline_level": self.baseline_level.label,
            "final_interval_seconds": self.final_interval,
            "final_interval_minutes": round(self.final_interval / 60, 2),
            "reset_condition": self.reset_condition,
        }


class TwoPhaseScheduler:
    """
    Two-Phase scheduler that separates quiet mode from burst mode.

    This scheduler is optimized for bimodal activity patterns:
    - Quiet mode: Conservative polling when activity is low
    - Burst mode: Aggressive polling during registration waves

    The scheduler enters burst mode when a significant activity spike is detected,
    and exits only after sustained low activity (consecutive low-score polls).
    """

    # Thresholds for mode transitions (data-driven from log analysis)
    BURST_ENTRY_THRESHOLD = 12.0  # Score to enter burst mode
    BURST_EXIT_THRESHOLD = 3.0  # Score below this counts toward exit
    BURST_EXIT_COUNT = 3  # Consecutive low polls needed to exit burst mode

    # Quiet mode intervals (conservative)
    QUIET_INTERVALS = {
        "active": 5 * 60,  # score >= 5: something happening, check in 5 min
        "idle": 15 * 60,  # score 2-4: minor noise, check in 15 min
        "silent": 30 * 60,  # score < 2: completely quiet, check in 30 min
    }

    # Burst mode intervals (aggressive)
    BURST_INTERVALS = {
        "extreme": 10,  # score >= 25: rapid fire
        "high": 60,  # score >= 12: active period
        "moderate": 120,  # score >= 5: trailing activity
        "low": 180,  # score < 5: cooling down (stay elevated)
    }

    def __init__(
        self,
        schedule_file: str = "schedule.txt",
        log_file: str = "scheduler_decisions.log",
        no_telegram: bool = False,
    ):
        self.schedule_file = schedule_file
        self.logger = DecisionLogger(log_file)
        self.no_telegram = no_telegram
        self.logger_ops = get_logger(__name__)

        # Two-phase state
        self.mode: str = "quiet"  # "quiet" or "burst"
        self.consecutive_low: int = 0
        self.decay_counter: int = 0
        self._new_poll_processed: bool = False
        self.quiet_interval: float = 300.0
        self.last_is_day: bool | None = None

        # Concurrency tasks and pending flags
        self._website_update_task: asyncio.Task | None = None
        self._website_update_pending: bool = False
        self._telegram_report_task: asyncio.Task | None = None
        self._telegram_report_pending: bool = False

        # Initialize ReportingService (lazy import to avoid circular dep)
        self._detected_semester: str | None = None
        self.reporting_service = None
        self._reporting_service_class = None

        if not no_telegram:
            try:
                from ..services.reporting_service import ReportingService as RS

                self._reporting_service_class = RS  # type: ignore[assignment]
            except ImportError:
                try:
                    from registrarmonitor.services.reporting_service import (
                        ReportingService as RS,
                    )

                    self._reporting_service_class = RS  # type: ignore[assignment]
                except ImportError:
                    print("⚠️  Warning: ReportingService unavailable")

        # Initialize caffeinate process for sleep prevention
        self.caffeinate_process = None

        # Website update configuration
        self.website_interval_minutes = 30
        try:
            config = get_config()
            self.website_interval_minutes = config.get("website", {}).get(
                "update_interval", 30
            )
        except Exception:
            pass
        self.last_website_update = datetime.datetime.now() - datetime.timedelta(
            minutes=self.website_interval_minutes
        )

        # Cooldown and event-driven tracking
        self._last_poll_time: datetime.datetime | None = None
        self._last_change_score: float = 0.0
        self.last_report_sent_time: datetime.datetime | None = None
        self.last_website_updated_time: datetime.datetime | None = None
        self.report_cooldown_seconds: float = 300.0  # 5 minutes
        self.website_cooldown_seconds: float = 300.0  # 5 minutes

        # Initialize queue and downloader for parallel asynchronous polling & FIFO commits
        self.downloader = DataDownloader()
        self.pending_polls: asyncio.Queue = asyncio.Queue()

    def _run_website_update(self):
        """Run website generation and deployment."""
        try:
            # Lazy import to avoid circular dependencies if any
            from ..services.website_service import WebsiteService

            config = get_config()
            website_config = config.get("website", {})
            project_name = website_config.get("pages_project_name", "registrar-monitor")

            print(f"\n🌐 Starting Website Update (Project: {project_name})...")
            service = WebsiteService()

            # Generate (incremental)
            if service.generate():
                # Deploy
                service.deploy(project_name=project_name)

        except Exception as e:
            print(f"❌ Website update failed: {e}")

    async def _run_report_cycle_async(self, force_poll: bool = True):
        self.logger_ops.info("Background Telegram report task started.")
        while True:
            self._telegram_report_pending = False
            try:
                await self._run_report_cycle(force_poll=force_poll)
            except Exception as e:
                self.logger_ops.error(f"Error in background Telegram report cycle: {e}")

            if not self._telegram_report_pending:
                break
            self.logger_ops.info(
                "Another Telegram report is pending. Running report cycle again."
            )
        self.logger_ops.info("Background Telegram report task finished.")

    async def _run_website_update_async(self):
        self.logger_ops.info("Background website update task started.")
        while True:
            self._website_update_pending = False
            try:
                await asyncio.to_thread(self._run_website_update)
            except Exception as e:
                self.logger_ops.error(f"Error in background website update: {e}")

            if not self._website_update_pending:
                break
            self.logger_ops.info(
                "Another website update is pending. Running website update again."
            )
        self.logger_ops.info("Background website update task finished.")

    async def _check_and_trigger_updates(self):
        """
        Check if the database contains new snapshots that haven't been reported yet.
        If there are new snapshots, compare them and determine if the changes are significant
        enough to warrant a report / website update.
        """
        try:
            from ..data.snapshot_comparator import SnapshotComparator
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator
            from registrarmonitor.cli.utils import detect_active_semester

        try:
            semester = await detect_active_semester()
            db_manager = DatabaseManager(semester=semester)
            comparator = SnapshotComparator()

            latest_snapshot_id = db_manager.get_latest_snapshot_id()
            last_reported_id = db_manager.get_last_reported_snapshot_id()

            if not latest_snapshot_id:
                return

            # If this is the first run, initialize the reporting log with latest snapshot
            if not last_reported_id:
                self.logger_ops.info(
                    f"ℹ️  First run detected. Setting baseline reported snapshot to {latest_snapshot_id}."
                )
                db_manager.add_reporting_log(
                    snapshot_id=latest_snapshot_id, changes_were_found=False
                )
                return

            if latest_snapshot_id == last_reported_id:
                return

            # Fetch snapshot data
            current_snapshot = db_manager.get_snapshot_data(latest_snapshot_id)
            previous_snapshot = db_manager.get_snapshot_data(last_reported_id)

            if not current_snapshot or not previous_snapshot:
                return

            # Compare snapshots
            comparison = comparator.compare_snapshots(
                current_snapshot, previous_snapshot
            )

            # Determine if there is a status change or a high activity score
            score = 0.0
            score += len(comparison.new_courses) * 5.0
            score += len(comparison.removed_courses) * 5.0
            for course_change in comparison.changed_courses:
                score += len(course_change.added_sections) * 2.0
                score += len(course_change.removed_sections) * 2.0
                for section_change in course_change.modified_sections:
                    enrollment_delta = abs(
                        (section_change.current_enrollment or 0)
                        - (section_change.previous_enrollment or 0)
                    )
                    score += enrollment_delta / 5.0
                    if (
                        section_change.current_capacity is not None
                        and section_change.previous_capacity is not None
                        and section_change.current_capacity
                        != section_change.previous_capacity
                    ):
                        score += 3.0

            # Check for section status changes (open <-> full)
            status_changed = False
            if comparison.new_courses or comparison.removed_courses:
                status_changed = True
            else:
                for course_change in comparison.changed_courses:
                    if course_change.added_sections or course_change.removed_sections:
                        status_changed = True
                        break
                    # Check modified sections for open/full changes
                    current_course = current_snapshot.courses.get(
                        course_change.course_code
                    )
                    previous_course = previous_snapshot.courses.get(
                        course_change.course_code
                    )
                    if current_course and previous_course:
                        for sec_mod in course_change.modified_sections:
                            curr_sec = current_course.sections.get(sec_mod.section_id)
                            prev_sec = previous_course.sections.get(sec_mod.section_id)
                            if curr_sec and prev_sec:
                                was_full = (
                                    prev_sec.enrollment >= prev_sec.capacity
                                    if prev_sec.capacity > 0
                                    else False
                                )
                                is_full = (
                                    curr_sec.enrollment >= curr_sec.capacity
                                    if curr_sec.capacity > 0
                                    else False
                                )
                                if was_full != is_full:
                                    status_changed = True
                                    break
                    if status_changed:
                        break

            # Define thresholds:
            is_worth_updating = status_changed or score >= 1.0

            if is_worth_updating:
                now = datetime.datetime.now()
                self.logger_ops.info(
                    f"📢 Significant activity detected (Pending Score: {score:.1f}, Status Change: {status_changed})"
                )

                # 1. Trigger Report
                if not self.no_telegram:
                    # Check report cooldown
                    seconds_since_last_report = (
                        (now - self.last_report_sent_time).total_seconds()
                        if self.last_report_sent_time
                        else None
                    )
                    if (
                        seconds_since_last_report is None
                        or seconds_since_last_report >= self.report_cooldown_seconds
                    ):
                        self.logger_ops.info("📝 Triggering Telegram Report...")
                        if (
                            self._telegram_report_task is None
                            or self._telegram_report_task.done()
                        ):
                            self._telegram_report_task = asyncio.create_task(
                                self._run_report_cycle_async(force_poll=False)
                            )
                        else:
                            self._telegram_report_pending = True
                        self.last_report_sent_time = now
                    else:
                        cooldown_remaining = int(
                            self.report_cooldown_seconds - seconds_since_last_report
                        )
                        self.logger_ops.info(
                            f"⏳ Telegram Report is on cooldown ({cooldown_remaining}s remaining). Will report next cycle."
                        )

                # 2. Trigger Website Update
                seconds_since_last_website = (
                    (now - self.last_website_updated_time).total_seconds()
                    if self.last_website_updated_time
                    else None
                )
                if (
                    seconds_since_last_website is None
                    or seconds_since_last_website >= self.website_cooldown_seconds
                ):
                    self.logger_ops.info("🌐 Triggering Website Update...")
                    if (
                        self._website_update_task is None
                        or self._website_update_task.done()
                    ):
                        self._website_update_task = asyncio.create_task(
                            self._run_website_update_async()
                        )
                    else:
                        self._website_update_pending = True
                    self.last_website_updated_time = now
                else:
                    cooldown_remaining = int(
                        self.website_cooldown_seconds - seconds_since_last_website
                    )
                    self.logger_ops.info(
                        f"⏳ Website Update is on cooldown ({cooldown_remaining}s remaining). Will update next cycle."
                    )
            else:
                # If changes are minor, print notice and let them accumulate (do not update reporting log)
                self.logger_ops.info(
                    f"ℹ️  Minor activity detected (Pending Score: {score:.1f}). Accumulating changes."
                )

        except Exception as e:
            self.logger_ops.error(f"❌ Error in check_and_trigger_updates: {e}")

    def _get_baseline_level(self) -> SchedulingLevel:
        """Get baseline level from configuration (predictive component)."""
        return get_current_zone_type()

    def _quiet_interval(self, score: float) -> int:
        """Calculate interval in quiet mode (conservative)."""
        if score >= 5:
            return self.QUIET_INTERVALS["active"]
        elif score >= 2:
            return self.QUIET_INTERVALS["idle"]
        else:
            return self.QUIET_INTERVALS["silent"]

    def _burst_interval(self, score: float) -> int:
        """Calculate interval in burst mode (aggressive)."""
        if score >= 25:
            return self.BURST_INTERVALS["extreme"]
        elif score >= 12:
            return self.BURST_INTERVALS["high"]
        elif score >= 5:
            return self.BURST_INTERVALS["moderate"]
        else:
            return self.BURST_INTERVALS["low"]

    def get_all_milestones(self) -> list[datetime.datetime]:
        """Extract all milestone and deadline datetimes from the settings.toml."""
        milestone_times = []
        try:
            cfg = get_config()
            semesters = cfg.get("semesters", {})
            for sem_data in semesters.values():
                if not isinstance(sem_data, dict):
                    continue

                priorities = sem_data.get("priorities", {})
                for p_list in priorities.values():
                    for m_data in p_list:
                        try:
                            milestone_times.append(
                                datetime.datetime.fromisoformat(m_data[0])
                            )
                        except (IndexError, ValueError):
                            pass

                for d_data in sem_data.get("deadlines", []):
                    try:
                        milestone_times.append(
                            datetime.datetime.fromisoformat(d_data[0])
                        )
                    except (IndexError, ValueError):
                        pass
        except Exception as e:
            print(f"Warning: failed to read milestones from config: {e}")

        return sorted(list(set(milestone_times)))

    async def _calculate_change_score_for_poll(self, semester: str) -> float:
        """Calculate the change score for the latest poll compared to the previous one."""
        try:
            try:
                from ..data.snapshot_comparator import SnapshotComparator
                from ..services.monitoring_service import MonitoringService
            except ImportError:
                from registrarmonitor.data.snapshot_comparator import SnapshotComparator
                from registrarmonitor.services.monitoring_service import (
                    MonitoringService,
                )

            monitoring_service = MonitoringService(semester=semester)
            comparator = SnapshotComparator()

            latest_snapshot, previous_snapshot = (
                monitoring_service.get_snapshot_comparison()
            )
            if not latest_snapshot:
                return 0.0

            if not previous_snapshot:
                return 1.0

            comparison = comparator.compare_snapshots(
                latest_snapshot, previous_snapshot
            )
            score = 0.0

            score += len(comparison.new_courses) * 5.0
            score += len(comparison.removed_courses) * 5.0

            for course_change in comparison.changed_courses:
                score += len(course_change.added_sections) * 2.0
                score += len(course_change.removed_sections) * 2.0

                for section_change in course_change.modified_sections:
                    enrollment_delta = abs(
                        (section_change.current_enrollment or 0)
                        - (section_change.previous_enrollment or 0)
                    )
                    score += enrollment_delta / 5.0

                    if (
                        section_change.current_capacity is not None
                        and section_change.previous_capacity is not None
                    ):
                        if (
                            section_change.current_capacity
                            != section_change.previous_capacity
                        ):
                            score += 3.0

                    if (
                        section_change.current_instructor
                        != section_change.previous_instructor
                    ):
                        score += 1.0

            return min(score, 100.0)

        except Exception as e:
            print(f"ERROR: Failed to calculate change score: {e}")
            return 0.0

    async def _single_poll_and_process(self) -> float:
        """Downloads data and processes it sequentially, returning the change score."""
        try:
            from ..cli.commands import PollCommand
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.cli.commands import PollCommand
            from registrarmonitor.cli.utils import detect_active_semester

        file_path = await self.downloader.download()
        if not file_path:
            return 0.0

        semester = await detect_active_semester()
        db_manager = DatabaseManager(semester=semester)
        latest_id_before = db_manager.get_latest_snapshot_id()

        poll_command = PollCommand(debug=False)
        success = await poll_command.run(file_path=file_path)
        if not success:
            return 0.0

        latest_id_after = db_manager.get_latest_snapshot_id()
        if latest_id_before is not None and latest_id_before == latest_id_after:
            return 0.0

        return await self._calculate_change_score_for_poll(semester)

    async def _process_pending_polls_loop(self):
        """Processes downloaded files in strict chronological FIFO order, committing and triggering updates."""
        while True:
            try:
                download_task, poll_start_time = await self.pending_polls.get()
                try:
                    file_path = await download_task
                    if not file_path:
                        self.logger_ops.error(
                            "❌ Download failed (returned None). Skipping process."
                        )
                        self._last_change_score = 0.0
                        self._new_poll_processed = True
                        continue

                    self.logger_ops.info(
                        f"🔄 Sequentially processing poll from {poll_start_time.strftime('%H:%M:%S')}..."
                    )

                    try:
                        from ..cli.commands import PollCommand
                        from ..cli.utils import detect_active_semester
                    except ImportError:
                        from registrarmonitor.cli.commands import PollCommand
                        from registrarmonitor.cli.utils import detect_active_semester

                    detected_semester = await detect_active_semester()
                    db_manager = DatabaseManager(semester=detected_semester)
                    latest_id_before = db_manager.get_latest_snapshot_id()

                    poll_command = PollCommand(debug=False)
                    success = await poll_command.run(file_path=file_path)

                    if success:
                        self._last_poll_time = datetime.datetime.now()
                        latest_id_after = db_manager.get_latest_snapshot_id()

                        if (
                            latest_id_before is not None
                            and latest_id_before == latest_id_after
                        ):
                            change_score = 0.0
                        else:
                            change_score = await self._calculate_change_score_for_poll(
                                detected_semester
                            )

                        self._last_change_score = change_score
                        self._new_poll_processed = True

                        self.logger_ops.info(
                            f"✅ Sequentially processed poll. Score: {change_score:.2f}"
                        )
                        await self._check_and_trigger_updates()
                    else:
                        self.logger_ops.error("❌ Poll command execution failed.")
                        self._last_change_score = 0.0
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

    def get_next_poll_interval(
        self,
        last_change_score: float = 0,
        timestamp: datetime.datetime | None = None,
        update_state: bool = True,
    ) -> tuple[int, TwoPhaseDecision]:
        """
        Determine how long to wait before the NEXT poll based on two-phase logic.

        Returns:
            Tuple of (interval_seconds, TwoPhaseDecision)
        """
        if timestamp is None:
            timestamp = datetime.datetime.now()

        current_is_day = 8 <= timestamp.hour < 20

        # Step 1: Mode Transition and Counters Evaluation
        previous_mode = self.mode
        previous_decay = self.decay_counter
        previous_consecutive_low = self.consecutive_low
        previous_is_day = self.last_is_day

        if update_state:
            # 1. Update b_n (consecutive_low)
            if previous_mode == "burst" and last_change_score < 5.0:
                self.consecutive_low = previous_consecutive_low + 1
            else:
                self.consecutive_low = 0

            # 2. Update Mode_n
            if last_change_score >= 12.0:
                self.mode = "burst"
            elif previous_mode == "burst" and self.consecutive_low >= 3:
                self.mode = "quiet"
            else:
                self.mode = previous_mode

            # 3. Update k_n (decay_counter)
            if self.mode == "quiet" and last_change_score == 0.0:
                self.decay_counter = previous_decay + 1
            else:
                self.decay_counter = 0

            # 4. Update diurnal tracker
            self.last_is_day = current_is_day

            self.logger_ops.info(
                f"[Scheduler Step 1] State updated: Mode {previous_mode} -> {self.mode}, "
                f"Low count b: {previous_consecutive_low} -> {self.consecutive_low}, "
                f"Decay k: {previous_decay} -> {self.decay_counter}"
            )
        else:
            self.logger_ops.debug(
                f"[Scheduler Step 1] Re-evaluating interval without updating state. "
                f"Current Mode: {self.mode}, b: {self.consecutive_low}, k: {self.decay_counter}"
            )

        # Step 2: Calculate Mode-Specific Base Interval
        if self.mode == "quiet":
            if last_change_score >= 5.0:
                i_base = 300.0
            elif last_change_score >= 2.0:
                i_base = 900.0
            else:
                i_base = 1800.0

            decay_exponent = max(0, self.decay_counter - 1)
            i_mode = i_base * (1.5**decay_exponent)

            self.logger_ops.debug(
                f"[Scheduler Step 2] Quiet base calculation: i_base={i_base}s, decay_exp={decay_exponent}, i_mode={i_mode}s"
            )
        else:
            if last_change_score >= 25.0:
                i_mode = 10.0
            elif last_change_score >= 12.0:
                i_mode = 60.0
            elif last_change_score >= 5.0:
                i_mode = 120.0
            else:
                i_mode = 180.0

            self.logger_ops.debug(
                f"[Scheduler Step 2] Burst base calculation: i_mode={i_mode}s"
            )

        # Step 3: Apply Diurnal Caps and Reset Overrides
        c_tn = 7200.0 if current_is_day else 14400.0
        i_capped = min(i_mode, c_tn)

        reset_condition = False
        reset_reasons = []

        # Predicate 1: Significant activity resumes (Sn > 0) and (kn-1 > 0)
        if last_change_score > 0.0 and previous_decay > 0:
            reset_condition = True
            reset_reasons.append(
                f"activity resumption (S={last_change_score:.1f}, prev_k={previous_decay})"
            )

        # Predicate 2: Diurnal shift
        if previous_is_day is not None and previous_is_day != current_is_day:
            reset_condition = True
            reset_reasons.append(
                f"diurnal shift ({previous_is_day} -> {current_is_day})"
            )

        # Predicate 3: Mode boundary change
        if self.mode != previous_mode:
            reset_condition = True
            reset_reasons.append(f"mode change ({previous_mode} -> {self.mode})")

        if reset_condition:
            i_reactive = 300.0
            if update_state:
                self.quiet_interval = 300.0
                self.decay_counter = 0
            self.logger_ops.info(
                f"[Scheduler Step 3] Reset Condition Met due to: {', '.join(reset_reasons)}. Interval set to 300.0s"
            )
        else:
            i_reactive = i_capped
            if update_state:
                self.quiet_interval = i_reactive
            self.logger_ops.debug(
                f"[Scheduler Step 3] Cap={c_tn}s, Capped={i_capped}s, Reactive={i_reactive}s"
            )

        # Step 4: Integrate Predictive Component (Zone Scaling)
        baseline_level = self._get_baseline_level()
        zone_type = get_current_zone_type(timestamp)
        if zone_type == SchedulingLevel.HOT:
            i_sleep = min(i_reactive, 300.0)
            self.logger_ops.debug(
                f"[Scheduler Step 4] HOT Zone active: i_sleep scaled to {i_sleep}s (original reactive={i_reactive}s)"
            )
        else:
            i_sleep = i_reactive
            self.logger_ops.debug(
                f"[Scheduler Step 4] SLEEP Zone active: i_sleep={i_sleep}s"
            )

        # Step 5: Boundary Preemption Alignment
        final_interval = i_sleep
        preemption_reasons = []

        # T_next_hot - t_n
        next_hot_start = get_next_zone_start(timestamp)
        if next_hot_start is not None:
            seconds_until_next_hot = (next_hot_start - timestamp).total_seconds()
            if seconds_until_next_hot > 0:
                if seconds_until_next_hot < final_interval:
                    final_interval = seconds_until_next_hot
                    preemption_reasons.append(
                        f"next HOT zone start alignment ({seconds_until_next_hot:.1f}s remaining)"
                    )

        # T_milestone - t_n
        milestones = self.get_all_milestones()
        closest_milestone_diff = None
        closest_milestone = None
        for milestone in milestones:
            if milestone > timestamp:
                seconds_until_milestone = (milestone - timestamp).total_seconds()
                if seconds_until_milestone > 0:
                    if (
                        closest_milestone_diff is None
                        or seconds_until_milestone < closest_milestone_diff
                    ):
                        closest_milestone_diff = seconds_until_milestone
                        closest_milestone = milestone

        if closest_milestone_diff is not None and closest_milestone is not None:
            if closest_milestone_diff < final_interval:
                final_interval = closest_milestone_diff
                preemption_reasons.append(
                    f"closest milestone alignment at {closest_milestone.isoformat()} ({closest_milestone_diff:.1f}s remaining)"
                )

        final_interval_int = max(1, int(final_interval))

        if preemption_reasons:
            self.logger_ops.info(
                f"[Scheduler Step 5] Preemption Applied: interval shortened to {final_interval_int}s due to: {', '.join(preemption_reasons)}"
            )
        else:
            self.logger_ops.debug(
                f"[Scheduler Step 5] No preemption applied. Final interval: {final_interval_int}s"
            )

        # Log decision
        decision = TwoPhaseDecision(
            timestamp=timestamp,
            change_score=last_change_score,
            mode=self.mode,
            consecutive_low=self.consecutive_low,
            decay_counter=self.decay_counter,
            baseline_level=baseline_level,
            final_interval=final_interval_int,
            reset_condition=reset_condition,
        )
        self.logger.log_decision(decision)

        return final_interval_int, decision

    def _get_next_report_time(self) -> datetime.datetime:
        """
        Calculate the next scheduled report time (:15 or :45).
        Returns a datetime object for the next occurrence.
        """
        now = datetime.datetime.now()
        candidates = []

        for minute in [15, 45]:
            t = now.replace(minute=minute, second=0, microsecond=0)
            if t > now:
                candidates.append(t)
            t_next = (now + datetime.timedelta(hours=1)).replace(
                minute=minute, second=0, microsecond=0
            )
            candidates.append(t_next)

        return min(candidates)

    async def _run_report_cycle(self, force_poll: bool = True) -> float:
        """
        Execute the reporting cycle:
        1. Force fresh poll (if force_poll is True)
        2. Generate/Send report via ReportingService
        Returns the change score.
        """
        print("\n📝 Starting Scheduled Reporting Cycle...")
        print("-" * 40)

        if force_poll:
            # 1. Fresh Poll (runs sequentially)
            print("🔄 Fetching fresh data for report...")
            start_time = time.time()
            change_score = await self._single_poll_and_process()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            self._new_poll_processed = True
            duration = time.time() - start_time

            print(
                f"✅ Data fetched ({duration:.1f}s). Activity: {change_score:.2f}, Mode: {self.mode}"
            )
        else:
            print("ℹ️  Using fresh data from recent poll (skipping redundant fetch).")
            change_score = self._last_change_score

        # 2. Detect semester and initialize ReportingService if needed
        if self._reporting_service_class and not self.reporting_service:
            try:
                from ..cli.utils import detect_active_semester
            except ImportError:
                from registrarmonitor.cli.utils import detect_active_semester

            self._detected_semester = await detect_active_semester()
            self.reporting_service = self._reporting_service_class(
                semester=self._detected_semester
            )
            print(f"📋 Using semester: {self._detected_semester or 'default'}")

        # 3. Run Stateful Report
        if self.reporting_service:
            print("📊 Generating report (if needed)...")
            try:
                changes_found = await self.reporting_service.run_stateful_report_cycle(
                    debug_mode=False
                )
                if changes_found:
                    print("✅ Report generated and sent.")
                else:
                    print("ℹ️  No significant changes to report.")
            except Exception as e:
                print(f"❌ Error during reporting: {e}")
        else:
            print("❌ ReportingService not initialized, skipping report.")

        print("-" * 40)
        return change_score

    async def start(self):
        """The main execution loop for two-phase scheduling (event-driven)."""
        print("🚀 Starting Two-Phase Scheduler (Event-Driven Polling)")
        print("=" * 50)
        print(f"   📈 Burst entry threshold: {self.BURST_ENTRY_THRESHOLD}")
        print(f"   📉 Burst exit threshold: {self.BURST_EXIT_THRESHOLD}")
        print(f"   🔢 Burst exit count: {self.BURST_EXIT_COUNT}")
        print(f"   📢 Report cooldown: {int(self.report_cooldown_seconds // 60)}m")
        print(f"   🌐 Website cooldown: {int(self.website_cooldown_seconds // 60)}m")

        # Start caffeinate
        try:
            self.caffeinate_process = await asyncio.create_subprocess_exec(
                "caffeinate",
                "-d",
                "-i",
                "-m",
                "-s",
                "-w",
                str(os.getpid()),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("☕ Preventing macOS sleep mode (Display/Idle/System)")
        except Exception:
            print("⚠️  Could not start sleep prevention")

        self._show_schedule_status()

        # Initial sync on startup (synchronously/sequentially before background loops start)
        print("\n🔄 Performing Initial Sync...")
        start_time = time.time()
        try:
            change_score = await self._single_poll_and_process()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            self._new_poll_processed = True
            duration = time.time() - start_time
            print(
                f"✅ Initial sync done ({duration:.1f}s). Activity: {change_score:.2f}, Mode: {self.mode}"
            )
        except Exception as e:
            print(f"❌ Initial sync failed: {e}")
            change_score = 0.0

        # Initialize reporting baseline after first poll
        await self._check_and_trigger_updates()

        # Start background processor for sequential processing in FIFO order
        processor_task = asyncio.create_task(self._process_pending_polls_loop())

        try:
            while True:
                # 1. Determine adaptive sleep duration based on the last processed score
                wait_time_poll, decision = self.get_next_poll_interval(
                    self._last_change_score,
                    update_state=self._new_poll_processed,
                )
                self._new_poll_processed = False

                mode_indicator = "🔥" if self.mode == "burst" else "😴"
                print(
                    f"\n⏱️  Next poll in {int(wait_time_poll // 60)}m {int(wait_time_poll % 60)}s"
                    f"   {mode_indicator} Mode: {self.mode.upper()}"
                )
                sys.stdout.flush()

                # 2. Sleep
                await asyncio.sleep(wait_time_poll)

                # 3. Trigger Async Polling and Queue it
                print("\n🔄 Triggering async poll download...")
                download_task = asyncio.create_task(self.downloader.download())
                await self.pending_polls.put((download_task, datetime.datetime.now()))

        except KeyboardInterrupt:
            print("\n⚠️  Scheduler interrupted by user.")
        finally:
            processor_task.cancel()
            try:
                await processor_task
            except asyncio.CancelledError:
                pass
            for task in (self._website_update_task, self._telegram_report_task):
                if task and not task.done():
                    task.cancel()
                    try:
                        await task
                    except asyncio.CancelledError:
                        pass
            if self.caffeinate_process:
                self.caffeinate_process.terminate()
            print("📊 Scheduler stopped")

    def _show_schedule_status(self):
        """Show current schedule status and upcoming zones."""
        now = datetime.datetime.now()
        current_zone = get_current_zone_type()

        print(f"📅 Schedule Status (Current time: {now.strftime('%Y-%m-%d %H:%M')})")
        print(f"   Current zone: {current_zone.label.upper()}")

        zones = parse_schedule_file(self.schedule_file)
        active_zones = []
        upcoming_zones = []

        for zone_type, time_ranges in zones.items():
            if zone_type == SchedulingLevel.SLEEP:
                continue

            for start_time, end_time in time_ranges:
                if start_time <= now <= end_time:
                    active_zones.append(
                        f"{zone_type.label} ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})"
                    )
                elif start_time > now:
                    time_until = start_time - now
                    if time_until.total_seconds() < 86400:
                        upcoming_zones.append(
                            f"{zone_type.label} in {int(time_until.total_seconds() // 60)}m ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})"
                        )

        if active_zones:
            print(f"   Active: {', '.join(active_zones)}")
        if upcoming_zones:
            print(f"   Upcoming: {', '.join(upcoming_zones[:3])}")
        if not active_zones and not upcoming_zones:
            print("   No hot zones scheduled for today")

    def print_status(self):
        """Print current scheduler status and recent decisions."""
        print("🔍 Two-Phase Scheduler Status")
        print("=" * 30)

        current_zone = get_current_zone_type()
        baseline_level = self._get_baseline_level()

        print(f"Current Mode: {self.mode.upper()}")
        print(f"Consecutive Low: {self.consecutive_low}")
        print(f"Current Zone: {current_zone.label}")
        print(f"Baseline Level: {baseline_level.label}")

        print("\n📋 Recent Decisions:")
        recent_decisions = self.logger.get_recent_decisions(10)
        if recent_decisions:
            for i, decision in enumerate(recent_decisions[-5:], 1):
                timestamp = datetime.datetime.fromisoformat(decision["timestamp"])
                mode = decision.get("mode", "N/A")
                print(
                    f"  {i}. {timestamp.strftime('%m/%d %H:%M')} | "
                    f"Score: {decision['change_score']:5.1f} | "
                    f"Mode: {mode:5} | "
                    f"{decision['final_interval_minutes']:5.1f}m"
                )
        else:
            print("  No decisions logged yet.")


def is_extreme_zone() -> bool:
    """
    Checks if the current time falls within any extreme zone.

    Returns:
        True if current time is in an extreme zone, False otherwise
    """
    return get_current_zone_type() == SchedulingLevel.HOT


def is_hot_zone() -> bool:
    """
    Checks if the current time falls within any hot/high zone.

    Returns:
        True if current time is in a high zone, False otherwise
    """
    return get_current_zone_type() == SchedulingLevel.HOT


def get_next_zone_change() -> tuple[datetime.datetime | None, SchedulingLevel]:
    """
    Get the next time when the zone type will change.

    Returns:
        Tuple of (next_change_time, new_zone_type) or (None, current_zone) if no changes
    """
    now = datetime.datetime.now()
    zones = parse_schedule_file()
    current_zone = get_current_zone_type()

    # Collect all zone boundaries after current time
    future_events = []

    for start_time, end_time in zones.get(SchedulingLevel.HOT, []):
        if start_time > now:
            future_events.append((start_time, SchedulingLevel.HOT))
        if end_time > now:
            future_events.append((end_time, SchedulingLevel.SLEEP))

    if not future_events:
        return None, current_zone

    # Sort by time and return the next event
    future_events.sort(key=lambda x: x[0])
    next_time, next_zone = future_events[0]

    return next_time, next_zone


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "--summary":
            scheduler = TwoPhaseScheduler()
            zones = parse_schedule_file()
            current_zone = get_current_zone_type()
            baseline_level = scheduler._get_baseline_level()

            print("=== Two-Phase Scheduler Summary ===")
            print(f"Current level: {current_zone.label}")
            print(f"Baseline level: {baseline_level.label}")
            print(f"Baseline interval: {baseline_level.interval}s")
            print()

            for zone_type in SchedulingLevel:
                zone_list = zones[zone_type]
                if zone_list:
                    print(
                        f"{zone_type.label.capitalize()} zones ({len(zone_list)} configured):"
                    )
                    for start_time, end_time in zone_list:
                        print(
                            f"  - {start_time.strftime('%Y-%m-%d %H:%M')} to {end_time.strftime('%Y-%m-%d %H:%M')}"
                        )
                else:
                    print(f"{zone_type.label.capitalize()} zones: None configured")
        elif sys.argv[1] == "--status":
            scheduler = TwoPhaseScheduler()
            scheduler.print_status()
        else:
            print("Usage: python scheduler.py [--summary|--status]")
            print("  --summary     Show schedule configuration summary")
            print("  --status      Show current scheduler status")
    else:
        # Default to TwoPhaseScheduler
        scheduler = TwoPhaseScheduler()
        scheduler.start()
