import datetime
import json
import os
import subprocess
import sys
import time
from enum import Enum
from pathlib import Path


# ReportingService is imported lazily to avoid circular import
# (reporting_service imports HybridScheduler, scheduler imports ReportingService)
ReportingService = None  # type: ignore[misc, assignment]


def get_current_time_str() -> str:
    """Get current time as formatted string."""
    return datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


class SchedulingLevel(Enum):
    """Unified enum for scheduling levels (zones/tiers).

    Each level has a string value (for schedule.txt parsing) and an interval in seconds.
    Priority: EXTREME > HIGH > MODERATE > LOW
    """

    EXTREME = ("extreme", 12)  # 12 seconds - Fetch ASAP
    HIGH = ("high", 120)  # 2 minutes - High activity
    MODERATE = ("moderate", 300)  # 5 minutes - Moderate activity
    LOW = ("low", 1200)  # 20 minutes - Default/normal

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
    schedule_file: str = "schedule.txt",
    force_reload: bool = False,
) -> dict[ZoneType, list[tuple[datetime.datetime, datetime.datetime]]]:
    """
    Parse the schedule file and return zones organized by type.
    Uses caching to reduce I/O. Checks file modification time every _CACHE_TTL seconds.

    Args:
        schedule_file: Path to schedule file
        force_reload: If True, bypass cache and force reload from disk

    Returns:
        Dictionary mapping zone types to lists of (start_time, end_time) tuples.
        WARNING: The returned dictionary is cached. Do not modify it in place.
    """
    abs_path = os.path.abspath(schedule_file)
    now = time.time()

    # Check cache first
    if not force_reload and abs_path in _SCHEDULE_CACHE:
        cache_entry = _SCHEDULE_CACHE[abs_path]
        # If TTL hasn't expired, return cached data
        if now - cache_entry["last_check"] < _CACHE_TTL:
            return cache_entry["data"]

        # TTL expired, check file modification time
        try:
            current_mtime = os.path.getmtime(abs_path)
            if current_mtime == cache_entry["mtime"]:
                # File hasn't changed, update check time and return cache
                cache_entry["last_check"] = now
                return cache_entry["data"]
        except OSError:
            # File might have been deleted, proceed to reload (which handles missing file)
            pass

    # Reload from disk
    zones: dict[ZoneType, list[tuple[datetime.datetime, datetime.datetime]]] = {
        zone_type: [] for zone_type in ZoneType
    }

    try:
        # Capture mtime before reading to avoid race condition
        current_mtime = 0.0
        try:
            current_mtime = os.path.getmtime(abs_path)
        except OSError:
            pass  # File not found handled below

        with open(schedule_file, "r") as f:
            for line_num, line in enumerate(f, 1):
                line = line.strip()
                if not line or line.startswith("#"):
                    continue

                try:
                    parts = [part.strip() for part in line.split(",")]
                    if len(parts) != 3:
                        print(f"Warning: Invalid format on line {line_num}: {line}")
                        continue

                    zone_type_str, start_str, end_str = parts

                    # Parse zone type
                    try:
                        zone_type = SchedulingLevel.from_label(zone_type_str)
                    except ValueError:
                        print(
                            f"Warning: Unknown zone type '{zone_type_str}' on line {line_num}"
                        )
                        continue

                    # Parse datetimes
                    start_time = datetime.datetime.strptime(start_str, "%Y-%m-%d %H:%M")
                    end_time = datetime.datetime.strptime(end_str, "%Y-%m-%d %H:%M")

                    if start_time >= end_time:
                        print(
                            f"Warning: Start time must be before end time on line {line_num}"
                        )
                        continue

                    zones[zone_type].append((start_time, end_time))

                except ValueError as e:
                    print(f"Warning: Error parsing line {line_num}: {e}")
                    continue

        # Update cache
        if current_mtime > 0:
            _SCHEDULE_CACHE[abs_path] = {
                "data": zones,
                "mtime": current_mtime,
                "last_check": now,
            }

    except FileNotFoundError:
        print(f"Schedule file '{schedule_file}' not found. Using default scheduling.")
    except Exception as e:
        print(f"Error reading schedule file: {e}")

    return zones


def get_current_zone_type(schedule_file: str = "schedule.txt") -> SchedulingLevel:
    """
    Determine the current scheduling level based on the schedule file.

    Returns:
        SchedulingLevel.EXTREME if in extreme zone
        SchedulingLevel.HIGH if in high zone
        SchedulingLevel.MODERATE if in moderate zone
        SchedulingLevel.LOW if not in any special zone
    """
    now = datetime.datetime.now()
    zones = parse_schedule_file(schedule_file)

    # Check zones in priority order (most urgent first)
    for level in [
        SchedulingLevel.EXTREME,
        SchedulingLevel.HIGH,
        SchedulingLevel.MODERATE,
    ]:
        for start_time, end_time in zones[level]:
            if start_time <= now <= end_time:
                return level

    return SchedulingLevel.LOW


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
            from ..data.snapshot_processor import SnapshotProcessor
        except ImportError:
            from registrarmonitor.cli.commands import PollCommand
            from registrarmonitor.data.snapshot_comparator import SnapshotComparator
            from registrarmonitor.data.snapshot_processor import SnapshotProcessor

        # Run only the polling command
        poll_command = PollCommand(debug=False)
        success = await poll_command.run()
        if not success:
            return 0.0

        # Calculate change score based on the comparison
        snapshot_processor = SnapshotProcessor()
        comparator = SnapshotComparator()

        # Get the latest two snapshots for comparison
        latest_snapshot = snapshot_processor.get_latest_snapshot()
        if not latest_snapshot:
            return 0.0

        previous_snapshot = snapshot_processor.load_latest_snapshot(
            latest_snapshot.semester, latest_snapshot.timestamp
        )

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
                for line in f:
                    line = line.strip()
                    if line:
                        decisions.append(json.loads(line))
            return decisions[-count:]
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

    def _get_reactive_level(self, score: float) -> SchedulingLevel:
        """Convert activity score to scheduling level."""
        return SchedulingLevel.from_score(score)

    def _get_baseline_level(self) -> SchedulingLevel:
        """Get baseline level from schedule file (predictive component)."""
        return get_current_zone_type(self.schedule_file)

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

        # 4. Check for upcoming zone changes (from schedule.txt)
        try:
            next_change_time, next_zone = get_next_zone_change(self.schedule_file)
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

    async def _run_report_cycle(self) -> float:
        """
        Execute the reporting cycle:
        1. Force fresh poll
        2. Generate/Send report via ReportingService
        Returns the change score from the fresh poll.
        """
        print("\n📝 Starting Scheduled Reporting Cycle...")
        print("-" * 40)

        # 1. Fresh Poll
        print("🔄 Fetching fresh data for report...")
        start_time = time.time()
        change_score = await poll_and_get_change_score()
        self.current_heat = max(
            change_score, self.current_heat * self.heat_decay_factor
        )
        duration = time.time() - start_time
        print(
            f"✅ Data fetched ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
        )

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
        """The main execution loop for hybrid scheduling."""
        print("🚀 Starting Hybrid Scheduler (Polling + Reporting)")
        print("=" * 50)

        # Start caffeinate
        try:
            self.caffeinate_process = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-s", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("☕ Preventing macOS sleep mode (Display/Idle/System)")
        except Exception:
            print("⚠️  Could not start sleep prevention")

        self._show_schedule_status()
        next_report = self._get_next_report_time()
        print(f"📨 Next report at: {next_report.strftime('%H:%M')}")

        # Initial sync on startup
        print("\n🔄 Performing Initial Sync...")
        start_time = time.time()
        try:
            change_score = await poll_and_get_change_score()
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

        try:
            while True:
                # 1. Calculate Next Event Times
                next_report_time = self._get_next_report_time()
                wait_time_poll, decision = self.get_next_poll_interval(change_score)

                now = datetime.datetime.now()
                seconds_until_report = (next_report_time - now).total_seconds()

                # Calculate pre-report sync time (1 minute before report)
                PRE_REPORT_SYNC_SECONDS = 60
                seconds_until_pre_report_sync = (
                    seconds_until_report - PRE_REPORT_SYNC_SECONDS
                )

                # 2. Determine Sleep Duration
                # Priority: pre-report sync > report time > adaptive poll
                # Skip report-related wakeups if no_telegram is enabled
                if self.no_telegram:
                    # Only do adaptive polling when Telegram is disabled
                    time_to_sleep = wait_time_poll
                    wake_reason = "poll"
                elif (
                    seconds_until_pre_report_sync > 0
                    and seconds_until_pre_report_sync < wait_time_poll
                ):
                    # Pre-report sync is coming up and is sooner than next poll
                    time_to_sleep = seconds_until_pre_report_sync
                    wake_reason = "pre_report_sync"
                elif seconds_until_report <= wait_time_poll:
                    # Report time is sooner than poll
                    time_to_sleep = seconds_until_report
                    wake_reason = "report"
                else:
                    # Normal adaptive poll
                    time_to_sleep = wait_time_poll
                    wake_reason = "poll"

                # Ensure non-negative sleep
                time_to_sleep = max(0, time_to_sleep)

                print(
                    f"\n⏱️  Next activity in {int(time_to_sleep // 60)}m {int(time_to_sleep % 60)}s"
                )
                if wake_reason == "pre_report_sync":
                    print(
                        f"   (Pre-report sync before {next_report_time.strftime('%H:%M')} report)"
                    )
                elif wake_reason == "report":
                    print(
                        f"   (Waking for Scheduled Report at {next_report_time.strftime('%H:%M')})"
                    )
                else:
                    print(
                        f"   (Waking for Adaptive Poll - Zone: {decision.zone_type.label})"
                    )
                sys.stdout.flush()

                # 3. Sleep
                if time_to_sleep > 0:
                    remaining = time_to_sleep
                    while remaining > 0:
                        sleep_chunk = min(remaining, 1.0)
                        time.sleep(sleep_chunk)
                        remaining -= sleep_chunk

                # 4. Perform Action
                now = datetime.datetime.now()
                seconds_to_report = (next_report_time - now).total_seconds()

                if seconds_to_report <= 5:
                    # It's Reporting Time! (within 5s buffer)
                    change_score = await self._run_report_cycle()
                elif seconds_to_report <= PRE_REPORT_SYNC_SECONDS + 5:
                    # Pre-report sync window
                    print(
                        "\n📥 Pre-report Sync (ensuring fresh data for upcoming report)..."
                    )
                    start_time = time.time()
                    try:
                        change_score = await poll_and_get_change_score()
                        self.current_heat = max(
                            change_score, self.current_heat * self.heat_decay_factor
                        )
                        duration = time.time() - start_time
                        print(
                            f"✅ Pre-report sync done ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
                        )
                    except Exception as e:
                        print(f"❌ Pre-report sync failed: {e}")
                        change_score = 0.0
                else:
                    # Regular adaptive poll
                    print("\n🔄 Performing Adaptive Poll...")
                    start_time = time.time()
                    try:
                        change_score = await poll_and_get_change_score()
                        duration = time.time() - start_time
                        print(
                            f"✅ Poll done ({duration:.1f}s). Activity: {change_score:.2f}, Heat: {self.current_heat:.2f}"
                        )
                    except Exception as e:
                        print(f"❌ Poll failed: {e}")
                        change_score = 0.0

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

    def _get_baseline_level(self) -> SchedulingLevel:
        """Get baseline level from schedule file (predictive component)."""
        return get_current_zone_type(self.schedule_file)

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
            next_change_time, _ = get_next_zone_change(self.schedule_file)
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

    async def _run_report_cycle(self) -> float:
        """
        Execute the reporting cycle:
        1. Force fresh poll
        2. Generate/Send report via ReportingService
        Returns the change score from the fresh poll.
        """
        print("\n📝 Starting Scheduled Reporting Cycle...")
        print("-" * 40)

        # 1. Fresh Poll
        print("🔄 Fetching fresh data for report...")
        start_time = time.time()
        change_score = await poll_and_get_change_score()
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
        """The main execution loop for two-phase scheduling."""
        print("🚀 Starting Two-Phase Scheduler (Quiet/Burst Mode)")
        print("=" * 50)
        print(f"   📈 Burst entry threshold: {self.BURST_ENTRY_THRESHOLD}")
        print(f"   📉 Burst exit threshold: {self.BURST_EXIT_THRESHOLD}")
        print(f"   🔢 Burst exit count: {self.BURST_EXIT_COUNT}")

        # Start caffeinate
        try:
            self.caffeinate_process = subprocess.Popen(
                ["caffeinate", "-d", "-i", "-m", "-s", "-w", str(os.getpid())],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            print("☕ Preventing macOS sleep mode (Display/Idle/System)")
        except Exception:
            print("⚠️  Could not start sleep prevention")

        self._show_schedule_status()
        next_report = self._get_next_report_time()
        print(f"📨 Next report at: {next_report.strftime('%H:%M')}")

        # Initial sync on startup
        print("\n🔄 Performing Initial Sync...")
        start_time = time.time()
        try:
            change_score = await poll_and_get_change_score()
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

        try:
            while True:
                # 1. Calculate Next Event Times
                next_report_time = self._get_next_report_time()
                wait_time_poll, decision = self.get_next_poll_interval(change_score)

                now = datetime.datetime.now()
                seconds_until_report = (next_report_time - now).total_seconds()

                # Calculate pre-report sync time (1 minute before report)
                PRE_REPORT_SYNC_SECONDS = 60
                seconds_until_pre_report_sync = (
                    seconds_until_report - PRE_REPORT_SYNC_SECONDS
                )

                # 2. Determine Sleep Duration
                # Skip report-related wakeups if no_telegram is enabled
                if self.no_telegram:
                    # Only do adaptive polling when Telegram is disabled
                    time_to_sleep = wait_time_poll
                    wake_reason = "poll"
                elif (
                    seconds_until_pre_report_sync > 0
                    and seconds_until_pre_report_sync < wait_time_poll
                ):
                    time_to_sleep = seconds_until_pre_report_sync
                    wake_reason = "pre_report_sync"
                elif seconds_until_report <= wait_time_poll:
                    time_to_sleep = seconds_until_report
                    wake_reason = "report"
                else:
                    time_to_sleep = wait_time_poll
                    wake_reason = "poll"

                time_to_sleep = max(0, time_to_sleep)

                print(
                    f"\n⏱️  Next activity in {int(time_to_sleep // 60)}m {int(time_to_sleep % 60)}s"
                )
                mode_indicator = "🔥" if self.mode == "burst" else "😴"
                if wake_reason == "pre_report_sync":
                    print(
                        f"   {mode_indicator} Mode: {self.mode.upper()} (Pre-report sync before {next_report_time.strftime('%H:%M')})"
                    )
                elif wake_reason == "report":
                    print(
                        f"   {mode_indicator} Mode: {self.mode.upper()} (Report at {next_report_time.strftime('%H:%M')})"
                    )
                else:
                    print(
                        f"   {mode_indicator} Mode: {self.mode.upper()} (Adaptive Poll)"
                    )
                sys.stdout.flush()

                # 3. Sleep
                if time_to_sleep > 0:
                    remaining = time_to_sleep
                    while remaining > 0:
                        sleep_chunk = min(remaining, 1.0)
                        time.sleep(sleep_chunk)
                        remaining -= sleep_chunk

                # 4. Perform Action
                now = datetime.datetime.now()
                seconds_to_report = (next_report_time - now).total_seconds()

                if seconds_to_report <= 5:
                    # Report time
                    change_score = await self._run_report_cycle()
                elif seconds_to_report <= PRE_REPORT_SYNC_SECONDS + 5:
                    # Pre-report sync
                    print(
                        "\n📥 Pre-report Sync (ensuring fresh data for upcoming report)..."
                    )
                    start_time = time.time()
                    try:
                        change_score = await poll_and_get_change_score()
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
                            f"✅ Pre-report sync done ({duration:.1f}s). Activity: {change_score:.2f}, Mode: {self.mode}"
                        )
                    except Exception as e:
                        print(f"❌ Pre-report sync failed: {e}")
                        change_score = 0.0
                else:
                    # Regular adaptive poll
                    print("\n🔄 Performing Adaptive Poll...")
                    start_time = time.time()
                    try:
                        change_score = await poll_and_get_change_score()
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


def is_extreme_zone(schedule_file: str = "schedule.txt") -> bool:
    """
    Checks if the current time falls within any extreme zone.

    Args:
        schedule_file: Path to the schedule configuration file

    Returns:
        True if current time is in an extreme zone, False otherwise
    """
    return get_current_zone_type(schedule_file) == ZoneType.EXTREME


def is_hot_zone(schedule_file: str = "schedule.txt") -> bool:
    """
    Checks if the current time falls within any hot/high zone.

    Args:
        schedule_file: Path to the schedule configuration file

    Returns:
        True if current time is in a high zone, False otherwise
    """
    return get_current_zone_type(schedule_file) == SchedulingLevel.HIGH


def get_next_zone_change(
    schedule_file: str = "schedule.txt",
) -> tuple[datetime.datetime | None, ZoneType]:
    """
    Get the next time when the zone type will change.

    Args:
        schedule_file: Path to the schedule configuration file

    Returns:
        Tuple of (next_change_time, new_zone_type) or (None, current_zone) if no changes
    """
    now = datetime.datetime.now()
    zones = parse_schedule_file(schedule_file)
    current_zone = get_current_zone_type(schedule_file)

    # Collect all zone boundaries after current time
    future_events = []

    for zone_type in [
        SchedulingLevel.EXTREME,
        SchedulingLevel.HIGH,
        SchedulingLevel.MODERATE,
    ]:
        for start_time, end_time in zones[zone_type]:
            if start_time > now:
                future_events.append((start_time, zone_type))
            if end_time > now:
                future_events.append((end_time, SchedulingLevel.LOW))

    if not future_events:
        return None, current_zone

    # Sort by time and return the next event
    future_events.sort(key=lambda x: x[0])
    next_time, next_zone = future_events[0]

    # If we're currently in a zone and the next event is the end of that zone,
    # determine what zone we'll be in after
    if next_zone == SchedulingLevel.LOW:
        # Check if there's another zone starting at the same time
        for event_time, zone_type in future_events:
            if event_time == next_time and zone_type != SchedulingLevel.LOW:
                next_zone = zone_type
                break

    return next_time, next_zone


if __name__ == "__main__":
    import sys

    if len(sys.argv) > 1:
        if sys.argv[1] == "--summary":
            scheduler = HybridScheduler()
            zones = parse_schedule_file(scheduler.schedule_file)
            current_zone = get_current_zone_type(scheduler.schedule_file)
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
