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

# ReportingService is imported lazily to avoid circular import
# (reporting_service imports HybridScheduler, scheduler imports ReportingService)
ReportingService = None  # type: ignore[misc, assignment]


def get_current_time_str() -> str:
    """Get current time as formatted string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SchedulingLevel(Enum):
    """Unified enum for scheduling levels (zones/tiers).

    Each level has a string label and an interval in seconds.
    Priority: EXTREME > HIGH > MODERATE > LOW
    """

    EXTREME = ("extreme", 12)  # 12 seconds - Fetch ASAP
    HIGH = ("high", 120)  # 2 minutes - High activity
    MODERATE = ("moderate", 300)  # 5 minutes - Moderate activity
    LOW = ("low", 1200)  # 20 minutes - Default/normal
    SLEEP = ("sleep", 3600)  # 1 hour - Outside all registration windows

    def __init__(self, label: str, interval: int):
        self._label = label
        self._interval = interval

    @property
    def label(self) -> str:
        """String label used in schedule.txt (e.g., 'extreme', 'high')."""
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
        if score >= 30:
            return cls.EXTREME
        elif score >= 10:
            return cls.HIGH
        elif score >= 1:
            return cls.MODERATE
        else:
            return cls.LOW

    def is_more_urgent_than(self, other: "SchedulingLevel") -> bool:
        """Check if this level is more urgent (shorter interval) than another."""
        return self._interval < other._interval


# Backwards compatibility aliases
ZoneType = SchedulingLevel
ActivityTier = SchedulingLevel


# Cache storage
# Key: absolute file path
# Value: dict with keys:
#   - 'data': The parsed zones dict
#   - 'mtime': The modification time of the file
#   - 'last_check': Timestamp of the last check (for TTL)
_SCHEDULE_CACHE = {}
_CACHE_TTL = 60  # seconds


def parse_schedule_file(
    force_reload: bool = False,
) -> dict[ZoneType, list[tuple[datetime.datetime, datetime.datetime]]]:
    """
    Build scheduler zones from milestones defined in settings.toml.

    Zone inference rules (per milestone):
        extreme = [time − 5 min,  time + 10 min]
        high    = [time + 10 min, time + 30 min]
    Per deadline:
        moderate = [time − 30 min, time + 30 min]

    Returns:
        Dictionary mapping zone types to lists of (start_time, end_time) tuples.
    """
    is_mocked = hasattr(get_config, "mock_add_spec") or "Mock" in type(get_config).__name__
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
    zones: dict[ZoneType, list[tuple[datetime.datetime, datetime.datetime]]] = {
        zone_type: [] for zone_type in ZoneType
    }

    current_mtime = 0.0
    try:
        current_mtime = os.path.getmtime(abs_path)
    except OSError:
        pass

    try:
        cfg = get_config()
        semesters = cfg.get("semesters", {})

        # ---- Process milestones → extreme + high zones ----
        for sem_name, sem_data in semesters.items():
            if not isinstance(sem_data, dict):
                continue

            priorities = sem_data.get("priorities", {})
            for p_list in priorities.values():
                for m_data in p_list:
                    try:
                        t = datetime.datetime.fromisoformat(m_data[0])
                        # extreme: -5min to +10min
                        zones[SchedulingLevel.EXTREME].append(
                            (t - datetime.timedelta(minutes=5), t + datetime.timedelta(minutes=10))
                        )
                        # high: +10min to +30min
                        zones[SchedulingLevel.HIGH].append(
                            (t + datetime.timedelta(minutes=10), t + datetime.timedelta(minutes=30))
                        )
                    except (IndexError, ValueError) as e:
                        print(f"Warning: skipping milestone {m_data}: {e}")

            # ---- Process deadlines → moderate zones ----
            for d_data in sem_data.get("deadlines", []):
                try:
                    t = datetime.datetime.fromisoformat(d_data[0])
                    # moderate: -30min to +30min
                    zones[SchedulingLevel.MODERATE].append(
                        (t - datetime.timedelta(minutes=30), t + datetime.timedelta(minutes=30))
                    )
                except (IndexError, ValueError) as e:
                    print(f"Warning: skipping deadline {d_data}: {e}")

        # Sort zones by start time
        for zone_type in zones:
            zones[zone_type].sort(key=lambda x: x[0])

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


def get_next_zone_start() -> datetime.datetime | None:
    """
    Find the start time of the next scheduled zone window after now.

    Returns:
        The nearest future zone start time, or None if no future zones exist.
    """
    now = datetime.datetime.now()
    zones = parse_schedule_file()
    next_start = None

    for level in [SchedulingLevel.EXTREME, SchedulingLevel.HIGH, SchedulingLevel.MODERATE]:
        for start_time, _end_time in zones[level]:
            if start_time > now:
                if next_start is None or start_time < next_start:
                    next_start = start_time

    return next_start


def get_current_zone_type() -> SchedulingLevel:
    """
    Determine the current scheduling level based on milestones in settings.toml.

    Returns:
        SchedulingLevel.EXTREME if in extreme zone
        SchedulingLevel.HIGH if in high zone
        SchedulingLevel.MODERATE if in moderate zone
        SchedulingLevel.SLEEP if outside all defined windows (and zones exist)
        SchedulingLevel.LOW if no zones are configured
    """
    now = datetime.datetime.now()
    zones = parse_schedule_file()

    # Check if there are any configured zones
    has_any_zones = any(
        len(zones[level]) > 0
        for level in [SchedulingLevel.EXTREME, SchedulingLevel.HIGH, SchedulingLevel.MODERATE]
    )
    if not has_any_zones:
        return SchedulingLevel.LOW

    # Check zones in priority order (most urgent first)
    for level in [
        SchedulingLevel.EXTREME,
        SchedulingLevel.HIGH,
        SchedulingLevel.MODERATE,
    ]:
        for start_time, end_time in zones[level]:
            if start_time <= now <= end_time:
                return level

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
        except ImportError:
            from registrarmonitor.cli.commands import PollCommand
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator
            from registrarmonitor.services.monitoring_service import MonitoringService

        # Run only the polling command
        poll_command = PollCommand(debug=False)
        success = await poll_command.run()
        if not success:
            return 0.0

        # Detect the active semester so we query the correct database
        try:
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.cli.utils import detect_active_semester
            
        detected_semester = await detect_active_semester()

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
        # Backwards compatibility aliases
        self.predicted_tier = baseline_level
        self.reactive_tier = reactive_level
        self.final_tier = final_level
        self.zone_type = final_level

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


class HybridScheduler:
    """
    Single hybrid scheduler that handles both data polling and reporting.

    It controls two main activities:
    1. Polling: Uses adaptive logic (heat/tiers) to poll data frequently when active.
    2. Reporting: Ensures reports are generated and sent at specific times (:15, :45).

    The scheduler manages the sleep loop to respect both the adaptive polling
    needs and the strict reporting deadlines.
    """

    def __init__(
        self,
        schedule_file: str = "schedule.txt",
        log_file: str = "scheduler_decisions.log",
        heat_decay_factor: float = 0.8,
        no_telegram: bool = False,
    ):
        self.schedule_file = schedule_file
        self.logger = DecisionLogger(log_file)
        self.no_telegram = no_telegram

        # Initialize ReportingService with detected semester (lazy import to avoid circular dep)
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

        # Heat decay: retains memory of recent activity to prevent rapid cooling
        self.current_heat: float = 0.0
        self.heat_decay_factor = heat_decay_factor  # 0.8 = ~50% heat after 3 cycles

        # Website update configuration
        self.website_interval_minutes = 30
        try:
            config = get_config()
            self.website_interval_minutes = config.get("website", {}).get(
                "update_interval", 30
            )
        except Exception:
            pass
        # Initialize so it runs soon after startup
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

    async def _check_and_trigger_updates(self):
        """
        Check if the database contains new snapshots that haven't been reported yet.
        If there are new snapshots, compare them and determine if the changes are significant
        enough to warrant a report / website update.
        """
        try:
            from ..data.database_manager import DatabaseManager
            from ..data.snapshot_comparator import SnapshotComparator
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.data.database_manager import DatabaseManager
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator
            from registrarmonitor.cli.utils import detect_active_semester

        try:
            semester = await detect_active_semester()
            db_manager = DatabaseManager(semester=semester)
            comparator = SnapshotComparator()

            latest_snapshot_id = db_manager.get_latest_snapshot_id()
            last_reported_id = db_manager.get_last_reported_snapshot_id()
            last_website_processed_id = getattr(self, "_last_website_processed_snapshot_id", None)

            if not latest_snapshot_id:
                return

            # If this is the first run, initialize the reporting log with latest snapshot
            # and align the website-processing baseline so the same snapshot is not
            # repeatedly treated as "new" when reporting is disabled or delayed.
            if not last_reported_id:
                print(f"ℹ️  First run detected. Setting baseline reported snapshot to {latest_snapshot_id}.")
                db_manager.add_reporting_log(snapshot_id=latest_snapshot_id, changes_were_found=False)
                self._last_website_processed_snapshot_id = latest_snapshot_id
                return

            if latest_snapshot_id == last_website_processed_id:
                return

            # Fetch snapshot data
            current_snapshot = db_manager.get_snapshot_data(latest_snapshot_id)
            previous_snapshot = db_manager.get_snapshot_data(last_reported_id)

            if current_snapshot and previous_snapshot:
                self._last_website_processed_snapshot_id = latest_snapshot_id

            if not current_snapshot or not previous_snapshot:
                return

            # Compare snapshots
            comparison = comparator.compare_snapshots(current_snapshot, previous_snapshot)

            # Determine if there is a status change or a high activity score
            score = 0.0
            score += len(comparison.new_courses) * 5.0
            score += len(comparison.removed_courses) * 5.0
            for course_change in comparison.changed_courses:
                score += len(course_change.added_sections) * 2.0
                score += len(course_change.removed_sections) * 2.0
                for section_change in course_change.modified_sections:
                    enrollment_delta = (
                        abs((section_change.current_enrollment or 0) - (section_change.previous_enrollment or 0))
                    )
                    score += enrollment_delta / 5.0
                    if (
                        section_change.current_capacity is not None
                        and section_change.previous_capacity is not None
                        and section_change.current_capacity != section_change.previous_capacity
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
                    current_course = current_snapshot.courses.get(course_change.course_code)
                    previous_course = previous_snapshot.courses.get(course_change.course_code)
                    if current_course and previous_course:
                        for sec_mod in course_change.modified_sections:
                            curr_sec = current_course.sections.get(sec_mod.section_id)
                            prev_sec = previous_course.sections.get(sec_mod.section_id)
                            if curr_sec and prev_sec:
                                was_full = prev_sec.enrollment >= prev_sec.capacity if prev_sec.capacity > 0 else False
                                is_full = curr_sec.enrollment >= curr_sec.capacity if curr_sec.capacity > 0 else False
                                if was_full != is_full:
                                    status_changed = True
                                    break
                    if status_changed:
                        break

            # Define thresholds:
            is_worth_updating = status_changed or score >= 1.0

            if is_worth_updating:
                now = datetime.datetime.now()
                print(f"\n📢 Significant activity detected (Pending Score: {score:.1f}, Status Change: {status_changed})")

                # 1. Trigger Report
                if not self.no_telegram:
                    # Check report cooldown
                    seconds_since_last_report = (
                        (now - self.last_report_sent_time).total_seconds()
                        if self.last_report_sent_time
                        else None
                    )
                    if seconds_since_last_report is None or seconds_since_last_report >= self.report_cooldown_seconds:
                        print("📝 Triggering Telegram Report...")
                        await self._run_report_cycle(force_poll=False)
                        self.last_report_sent_time = now
                    else:
                        cooldown_remaining = int(self.report_cooldown_seconds - seconds_since_last_report)
                        print(f"⏳ Telegram Report is on cooldown ({cooldown_remaining}s remaining). Will report next cycle.")

                # 2. Trigger Website Update
                seconds_since_last_website = (
                    (now - self.last_website_updated_time).total_seconds()
                    if self.last_website_updated_time
                    else None
                )
                if seconds_since_last_website is None or seconds_since_last_website >= self.website_cooldown_seconds:
                    print("🌐 Triggering Website Update...")
                    await asyncio.to_thread(self._run_website_update)
                    self.last_website_updated_time = now
                else:
                    cooldown_remaining = int(self.website_cooldown_seconds - seconds_since_last_website)
                    print(f"⏳ Website Update is on cooldown ({cooldown_remaining}s remaining). Will update next cycle.")
            else:
                # If changes are minor, print notice and let them accumulate (do not update reporting log)
                print(f"ℹ️  Minor activity detected (Pending Score: {score:.1f}). Accumulating changes.")

        except Exception as e:
            print(f"❌ Error in check_and_trigger_updates: {e}")

    def _get_reactive_level(self, score: float) -> SchedulingLevel:
        """Convert activity score to scheduling level."""
        return SchedulingLevel.from_score(score)

    def _get_baseline_level(self) -> SchedulingLevel:
        """Get baseline level from configuration (predictive component)."""
        return get_current_zone_type()

    def _select_final_level(
        self, baseline: SchedulingLevel, reactive: SchedulingLevel
    ) -> SchedulingLevel:
        """
        Hybrid decision logic: reactive can override baseline.
        - Baseline sets the minimum expectation
        - Reactive can escalate but never de-escalate below baseline
        """
        # Take the more aggressive (shorter interval) of the two
        if reactive.is_more_urgent_than(baseline):
            return reactive
        else:
            return baseline

    def _get_next_report_time(self) -> datetime.datetime:
        """
        Calculate the next scheduled report time (:15 or :45).
        Returns a datetime object for the next occurrence.
        """
        now = datetime.datetime.now()
        candidates = []

        # Generate candidates for this hour and same time next hour
        for minute in [15, 45]:
            # This hour
            t = now.replace(minute=minute, second=0, microsecond=0)
            if t > now:
                candidates.append(t)
            # Next hour
            t_next = (now + datetime.timedelta(hours=1)).replace(
                minute=minute, second=0, microsecond=0
            )
            candidates.append(t_next)

        return min(candidates)

    def get_next_poll_interval(
        self, last_change_score: float = 0
    ) -> tuple[int, SchedulingDecision]:
        """
        Determine how long to wait before the NEXT poll based on adaptive logic.
        This does NOT account for reporting deadlines yet - the start loop handles that.
        """
        timestamp = datetime.datetime.now()

        # 1. Predictive Baseline
        baseline_level = self._get_baseline_level()

        # 2. Reactive Adjustment
        self.current_heat = max(
            last_change_score, self.current_heat * self.heat_decay_factor
        )
        reactive_level = self._get_reactive_level(self.current_heat)

        # 3. Hybrid Decision
        final_level = self._select_final_level(baseline_level, reactive_level)
        final_interval = final_level.interval

        # 4. Check for upcoming zone changes (from settings.toml)
        try:
            next_change_time, next_zone = get_next_zone_change()
            if next_change_time:
                seconds_until_change = int(
                    (next_change_time - timestamp).total_seconds()
                )
                # If zone change is sooner than our interval, wait just until the change
                if 0 < seconds_until_change < final_interval:
                    final_interval = max(60, seconds_until_change + 30)
        except Exception:
            pass  # Fallback to calculated interval on error

        # Create decision object
        decision = SchedulingDecision(
            timestamp=timestamp,
            change_score=last_change_score,
            current_heat=self.current_heat,
            baseline_level=baseline_level,
            reactive_level=reactive_level,
            final_level=final_level,
            final_interval=final_interval,
        )
        self.logger.log_decision(decision)

        return final_interval, decision

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
            # 1. Fresh Poll
            print("🔄 Fetching fresh data for report...")
            start_time = time.time()
            change_score = await poll_and_get_change_score()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            self.current_heat = max(
                change_score, self.current_heat * self.heat_decay_factor
            )
            duration = time.time() - start_time
            print(
                f"✅ Data fetched ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
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
        """The main execution loop for hybrid scheduling (event-driven)."""
        print("🚀 Starting Hybrid Scheduler (Event-Driven Polling)")
        print("=" * 50)
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

        # Initial sync on startup
        print("\n🔄 Performing Initial Sync...")
        start_time = time.time()
        try:
            change_score = await poll_and_get_change_score()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            self.current_heat = max(
                change_score, self.current_heat * self.heat_decay_factor
            )
            duration = time.time() - start_time
            print(
                f"✅ Initial sync done ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
            )
        except Exception as e:
            print(f"❌ Initial sync failed: {e}")
            change_score = 0.0

        # Initialize reporting baseline after first poll
        await self._check_and_trigger_updates()

        try:
            while True:
                # 1. Determine adaptive sleep duration
                wait_time_poll, decision = self.get_next_poll_interval(change_score)

                print(
                    f"\n⏱️  Next poll in {int(wait_time_poll // 60)}m {int(wait_time_poll % 60)}s"
                    f"   (Zone: {decision.zone_type.label}, Heat: {self.current_heat:.1f})"
                )
                sys.stdout.flush()

                # 2. Sleep
                await asyncio.sleep(wait_time_poll)

                # 3. Perform Adaptive Poll
                print("\n🔄 Performing Adaptive Poll...")
                start_time = time.time()
                try:
                    change_score = await poll_and_get_change_score()
                    self._last_poll_time = datetime.datetime.now()
                    self._last_change_score = change_score
                    self.current_heat = max(
                        change_score, self.current_heat * self.heat_decay_factor
                    )
                    duration = time.time() - start_time
                    print(
                        f"✅ Poll done ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
                    )
                except Exception as e:
                    print(f"❌ Poll failed: {e}")
                    change_score = 0.0

                # 4. Check if anything significant happened and trigger updates
                await self._check_and_trigger_updates()

        except KeyboardInterrupt:
            print("\n⚠️  Scheduler interrupted by user.")
        finally:
            if self.caffeinate_process:
                self.caffeinate_process.terminate()
            print("📊 Scheduler stopped")


    def _show_schedule_status(self):
        """Show current schedule status and upcoming zones."""
        now = datetime.datetime.now()
        current_zone = get_current_zone_type(self.schedule_file)

        print(f"📅 Schedule Status (Current time: {now.strftime('%Y-%m-%d %H:%M')})")
        print(f"   Current zone: {current_zone.label.upper()}")

        # Show active zones
        zones = parse_schedule_file(self.schedule_file)
        active_zones = []
        upcoming_zones = []

        for zone_type, time_ranges in zones.items():
            if zone_type == SchedulingLevel.LOW:
                continue

            for start_time, end_time in time_ranges:
                if start_time <= now <= end_time:
                    active_zones.append(
                        f"{zone_type.label} ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})"
                    )
                elif start_time > now:
                    time_until = start_time - now
                    if time_until.total_seconds() < 86400:  # Within 24 hours
                        upcoming_zones.append(
                            f"{zone_type.label} in {int(time_until.total_seconds() // 60)}m ({start_time.strftime('%H:%M')}-{end_time.strftime('%H:%M')})"
                        )

        if active_zones:
            print(f"   Active: {', '.join(active_zones)}")
        if upcoming_zones:
            print(f"   Upcoming: {', '.join(upcoming_zones[:3])}")  # Show next 3
        if not active_zones and not upcoming_zones:
            print("   No hot zones scheduled for today")

    def _show_next_schedule_change(self):
        """Show information about the next scheduled zone change."""
        now = datetime.datetime.now()
        zones = parse_schedule_file(self.schedule_file)

        next_changes = []
        for zone_type, time_ranges in zones.items():
            if zone_type == SchedulingLevel.LOW:
                continue

            for start_time, end_time in time_ranges:
                if start_time > now:
                    time_until = start_time - now
                    if time_until.total_seconds() < 3600:  # Within 1 hour
                        next_changes.append(
                            (
                                time_until.total_seconds(),
                                zone_type.label,
                                start_time,
                                end_time,
                            )
                        )
                elif start_time <= now <= end_time:
                    time_until_end = end_time - now
                    if time_until_end.total_seconds() < 3600:  # Ending within 1 hour
                        next_changes.append(
                            (
                                time_until_end.total_seconds(),
                                f"end of {zone_type.label}",
                                end_time,
                                None,
                            )
                        )

        if next_changes:
            next_changes.sort()
            time_seconds, zone_info, change_time, end_time = next_changes[0]
            minutes = int(time_seconds // 60)
            if zone_info.startswith("end of"):
                print(
                    f"📋 Next: {zone_info} in {minutes}m at {change_time.strftime('%H:%M')}"
                )
            else:
                print(
                    f"📋 Next: {zone_info} zone starts in {minutes}m at {change_time.strftime('%H:%M')}"
                )

    def print_status(self):
        """Print current scheduler status and recent decisions."""
        print("🔍 Hybrid Scheduler Status")
        print("=" * 30)

        current_zone = get_current_zone_type(self.schedule_file)
        baseline_level = self._get_baseline_level()

        print(f"Current Level: {current_zone.label}")
        print(f"Baseline Level: {baseline_level.label}")
        print(f"Baseline Interval: {baseline_level.interval}s")

        print("\n📋 Recent Decisions:")
        recent_decisions = self.logger.get_recent_decisions(10)
        if recent_decisions:
            for i, decision in enumerate(recent_decisions[-5:], 1):
                timestamp = datetime.datetime.fromisoformat(decision["timestamp"])
                print(
                    f"  {i}. {timestamp.strftime('%m/%d %H:%M')} | "
                    f"Score: {decision['change_score']:5.1f} | "
                    f"{decision.get('final_level', decision.get('final_tier', 'N/A')):7} | "
                    f"{decision['final_interval_minutes']:5.1f}m"
                )
        else:
            print("  No decisions logged yet.")


# Alias for backward compatibility
TaskScheduler = HybridScheduler


class TwoPhaseDecision:
    """Represents a two-phase scheduling decision for logging."""

    def __init__(
        self,
        timestamp: datetime.datetime,
        change_score: float,
        mode: str,
        consecutive_low: int,
        baseline_level: SchedulingLevel,
        final_interval: int,
    ):
        self.timestamp = timestamp
        self.change_score = change_score
        self.mode = mode
        self.consecutive_low = consecutive_low
        self.baseline_level = baseline_level
        self.final_interval = final_interval

    def to_dict(self) -> dict:
        """Convert to dictionary for JSON logging."""
        return {
            "timestamp": self.timestamp.isoformat(),
            "change_score": self.change_score,
            "mode": self.mode,
            "consecutive_low": self.consecutive_low,
            "baseline_level": self.baseline_level.label,
            "final_interval_seconds": self.final_interval,
            "final_interval_minutes": round(self.final_interval / 60, 2),
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
        "extreme": 15,  # score >= 25: rapid fire
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

        # Two-phase state
        self.mode: str = "quiet"  # "quiet" or "burst"
        self.consecutive_low: int = 0

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

    async def _check_and_trigger_updates(self):
        """
        Check if the database contains new snapshots that haven't been reported yet.
        If there are new snapshots, compare them and determine if the changes are significant
        enough to warrant a report / website update.
        """
        try:
            from ..data.database_manager import DatabaseManager
            from ..data.snapshot_comparator import SnapshotComparator
            from ..cli.utils import detect_active_semester
        except ImportError:
            from registrarmonitor.data.database_manager import DatabaseManager
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
                print(f"ℹ️  First run detected. Setting baseline reported snapshot to {latest_snapshot_id}.")
                db_manager.add_reporting_log(snapshot_id=latest_snapshot_id, changes_were_found=False)
                return

            if latest_snapshot_id == last_reported_id:
                return

            # Fetch snapshot data
            current_snapshot = db_manager.get_snapshot_data(latest_snapshot_id)
            previous_snapshot = db_manager.get_snapshot_data(last_reported_id)

            if not current_snapshot or not previous_snapshot:
                return

            # Compare snapshots
            comparison = comparator.compare_snapshots(current_snapshot, previous_snapshot)

            # Determine if there is a status change or a high activity score
            score = 0.0
            score += len(comparison.new_courses) * 5.0
            score += len(comparison.removed_courses) * 5.0
            for course_change in comparison.changed_courses:
                score += len(course_change.added_sections) * 2.0
                score += len(course_change.removed_sections) * 2.0
                for section_change in course_change.modified_sections:
                    enrollment_delta = (
                        abs((section_change.current_enrollment or 0) - (section_change.previous_enrollment or 0))
                    )
                    score += enrollment_delta / 5.0
                    if (
                        section_change.current_capacity is not None
                        and section_change.previous_capacity is not None
                        and section_change.current_capacity != section_change.previous_capacity
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
                    current_course = current_snapshot.courses.get(course_change.course_code)
                    previous_course = previous_snapshot.courses.get(course_change.course_code)
                    if current_course and previous_course:
                        for sec_mod in course_change.modified_sections:
                            curr_sec = current_course.sections.get(sec_mod.section_id)
                            prev_sec = previous_course.sections.get(sec_mod.section_id)
                            if curr_sec and prev_sec:
                                was_full = prev_sec.enrollment >= prev_sec.capacity if prev_sec.capacity > 0 else False
                                is_full = curr_sec.enrollment >= curr_sec.capacity if curr_sec.capacity > 0 else False
                                if was_full != is_full:
                                    status_changed = True
                                    break
                    if status_changed:
                        break

            # Define thresholds:
            is_worth_updating = status_changed or score >= 1.0

            if is_worth_updating:
                now = datetime.datetime.now()
                print(f"\n📢 Significant activity detected (Pending Score: {score:.1f}, Status Change: {status_changed})")

                # 1. Trigger Report
                if not self.no_telegram:
                    # Check report cooldown
                    seconds_since_last_report = (
                        (now - self.last_report_sent_time).total_seconds()
                        if self.last_report_sent_time
                        else None
                    )
                    if seconds_since_last_report is None or seconds_since_last_report >= self.report_cooldown_seconds:
                        print("📝 Triggering Telegram Report...")
                        await self._run_report_cycle(force_poll=False)
                        self.last_report_sent_time = now
                    else:
                        cooldown_remaining = int(self.report_cooldown_seconds - seconds_since_last_report)
                        print(f"⏳ Telegram Report is on cooldown ({cooldown_remaining}s remaining). Will report next cycle.")

                # 2. Trigger Website Update
                seconds_since_last_website = (
                    (now - self.last_website_updated_time).total_seconds()
                    if self.last_website_updated_time
                    else None
                )
                if seconds_since_last_website is None or seconds_since_last_website >= self.website_cooldown_seconds:
                    print("🌐 Triggering Website Update...")
                    await asyncio.to_thread(self._run_website_update)
                    self.last_website_updated_time = now
                else:
                    cooldown_remaining = int(self.website_cooldown_seconds - seconds_since_last_website)
                    print(f"⏳ Website Update is on cooldown ({cooldown_remaining}s remaining). Will update next cycle.")
            else:
                # If changes are minor, print notice and let them accumulate (do not update reporting log)
                print(f"ℹ️  Minor activity detected (Pending Score: {score:.1f}). Accumulating changes.")

        except Exception as e:
            print(f"❌ Error in check_and_trigger_updates: {e}")

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

    def get_next_poll_interval(
        self, last_change_score: float = 0
    ) -> tuple[int, TwoPhaseDecision]:
        """
        Determine how long to wait before the NEXT poll based on two-phase logic.

        Returns:
            Tuple of (interval_seconds, TwoPhaseDecision)
        """
        timestamp = datetime.datetime.now()
        baseline_level = self._get_baseline_level()

        # State machine: quiet <-> burst transitions
        if self.mode == "quiet":
            if last_change_score >= self.BURST_ENTRY_THRESHOLD:
                # Enter burst mode
                self.mode = "burst"
                self.consecutive_low = 0
                calculated_interval = self._burst_interval(last_change_score)
            else:
                # Stay in quiet mode
                calculated_interval = self._quiet_interval(last_change_score)
        else:  # burst mode
            if last_change_score < self.BURST_EXIT_THRESHOLD:
                self.consecutive_low += 1
            else:
                self.consecutive_low = 0

            if self.consecutive_low >= self.BURST_EXIT_COUNT:
                # Exit burst mode
                self.mode = "quiet"
                self.consecutive_low = 0
                calculated_interval = self._quiet_interval(last_change_score)
            else:
                # Stay in burst mode
                calculated_interval = self._burst_interval(last_change_score)

        # Respect baseline level from schedule.txt (take shorter of the two)
        final_interval = min(calculated_interval, baseline_level.interval)

        # Check for upcoming zone changes
        try:
            next_change_time, _ = get_next_zone_change()
            if next_change_time:
                seconds_until_change = int(
                    (next_change_time - timestamp).total_seconds()
                )
                if 0 < seconds_until_change < final_interval:
                    final_interval = max(60, seconds_until_change + 30)
        except Exception:
            pass

        # Log decision
        decision = TwoPhaseDecision(
            timestamp=timestamp,
            change_score=last_change_score,
            mode=self.mode,
            consecutive_low=self.consecutive_low,
            baseline_level=baseline_level,
            final_interval=final_interval,
        )
        self.logger.log_decision(decision)

        return final_interval, decision

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
            # 1. Fresh Poll
            print("🔄 Fetching fresh data for report...")
            start_time = time.time()
            change_score = await poll_and_get_change_score()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            duration = time.time() - start_time

            # Update mode based on score
            if change_score >= self.BURST_ENTRY_THRESHOLD:
                self.mode = "burst"
                self.consecutive_low = 0
            elif change_score < self.BURST_EXIT_THRESHOLD:
                self.consecutive_low += 1
                if self.consecutive_low >= self.BURST_EXIT_COUNT:
                    self.mode = "quiet"
                    self.consecutive_low = 0

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

        # Initial sync on startup
        print("\n🔄 Performing Initial Sync...")
        start_time = time.time()
        try:
            change_score = await poll_and_get_change_score()
            self._last_poll_time = datetime.datetime.now()
            self._last_change_score = change_score
            duration = time.time() - start_time

            # Update mode based on initial score
            if change_score >= self.BURST_ENTRY_THRESHOLD:
                self.mode = "burst"
                self.consecutive_low = 0
            print(
                f"✅ Initial sync done ({duration:.1f}s). Activity: {change_score:.2f}, Mode: {self.mode}"
            )
        except Exception as e:
            print(f"❌ Initial sync failed: {e}")
            change_score = 0.0

        # Initialize reporting baseline after first poll
        await self._check_and_trigger_updates()

        try:
            while True:
                # 1. Determine adaptive sleep duration
                wait_time_poll, decision = self.get_next_poll_interval(change_score)

                mode_indicator = "🔥" if self.mode == "burst" else "😴"
                print(
                    f"\n⏱️  Next poll in {int(wait_time_poll // 60)}m {int(wait_time_poll % 60)}s"
                    f"   {mode_indicator} Mode: {self.mode.upper()}"
                )
                sys.stdout.flush()

                # 2. Sleep
                await asyncio.sleep(wait_time_poll)

                # 3. Perform Adaptive Poll
                print("\n🔄 Performing Adaptive Poll...")
                start_time = time.time()
                try:
                    change_score = await poll_and_get_change_score()
                    self._last_poll_time = datetime.datetime.now()
                    self._last_change_score = change_score
                    duration = time.time() - start_time

                    # Update mode
                    if change_score >= self.BURST_ENTRY_THRESHOLD:
                        self.mode = "burst"
                        self.consecutive_low = 0
                    elif change_score < self.BURST_EXIT_THRESHOLD:
                        self.consecutive_low += 1
                        if self.consecutive_low >= self.BURST_EXIT_COUNT:
                            self.mode = "quiet"
                            self.consecutive_low = 0

                    print(
                        f"✅ Poll done ({duration:.1f}s). Activity: {change_score:.2f}, Mode: {self.mode}"
                    )
                except Exception as e:
                    print(f"❌ Poll failed: {e}")
                    change_score = 0.0

                # 4. Check if anything significant happened and trigger updates
                await self._check_and_trigger_updates()

        except KeyboardInterrupt:
            print("\n⚠️  Scheduler interrupted by user.")
        finally:
            if self.caffeinate_process:
                self.caffeinate_process.terminate()
            print("📊 Scheduler stopped")

    def _show_schedule_status(self):
        """Show current schedule status and upcoming zones."""
        now = datetime.datetime.now()
        current_zone = get_current_zone_type(self.schedule_file)

        print(f"📅 Schedule Status (Current time: {now.strftime('%Y-%m-%d %H:%M')})")
        print(f"   Current zone: {current_zone.label.upper()}")

        zones = parse_schedule_file(self.schedule_file)
        active_zones = []
        upcoming_zones = []

        for zone_type, time_ranges in zones.items():
            if zone_type == SchedulingLevel.LOW:
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

        current_zone = get_current_zone_type(self.schedule_file)
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
    return get_current_zone_type() == SchedulingLevel.EXTREME


def is_hot_zone() -> bool:
    """
    Checks if the current time falls within any hot/high zone.

    Returns:
        True if current time is in a high zone, False otherwise
    """
    return get_current_zone_type() == SchedulingLevel.HIGH


def get_next_zone_change() -> tuple[datetime.datetime | None, ZoneType]:
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

    # Check if there are any configured zones
    has_any_zones = any(
        len(zones[level]) > 0
        for level in [SchedulingLevel.EXTREME, SchedulingLevel.HIGH, SchedulingLevel.MODERATE]
    )
    default_idle_zone = SchedulingLevel.SLEEP if has_any_zones else SchedulingLevel.LOW

    for zone_type in [
        SchedulingLevel.EXTREME,
        SchedulingLevel.HIGH,
        SchedulingLevel.MODERATE,
    ]:
        for start_time, end_time in zones[zone_type]:
            if start_time > now:
                future_events.append((start_time, zone_type))
            if end_time > now:
                future_events.append((end_time, default_idle_zone))

    if not future_events:
        return None, current_zone

    # Sort by time and return the next event
    future_events.sort(key=lambda x: x[0])
    next_time, next_zone = future_events[0]

    # If we're currently in a zone and the next event is the end of that zone,
    # determine what zone we'll be in after
    if next_zone == default_idle_zone:
        # Check if there's another zone starting at the same time
        for event_time, zone_type in future_events:
            if event_time == next_time and zone_type != default_idle_zone:
                next_zone = zone_type
                break

    return next_time, next_zone


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "--summary":
            scheduler = HybridScheduler()
            zones = parse_schedule_file()
            current_zone = get_current_zone_type()
            baseline_level = scheduler._get_baseline_level()

            print("=== Hybrid Scheduler Summary ===")
            print(f"Current level: {current_zone.label}")
            print(f"Baseline level: {baseline_level.label}")
            print(f"Baseline interval: {baseline_level.interval}s")
            print()

            for zone_type in ZoneType:
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
            scheduler = HybridScheduler()
            scheduler.print_status()
        elif sys.argv[1] == "--hybrid":
            # Legacy support - still works
            scheduler = HybridScheduler()
            scheduler.start()
        else:
            print("Usage: python scheduler.py [--summary|--status|--hybrid]")
            print("  --summary     Show schedule configuration summary")
            print("  --status      Show current scheduler status")
            print("  --hybrid      Run hybrid scheduler (baseline + activity)")
    else:
        # Default to hybrid mode
        scheduler = HybridScheduler()
        scheduler.start()
