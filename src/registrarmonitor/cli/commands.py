"""Command implementations for the registrarmonitor CLI."""

import asyncio
import datetime as dt
import logging
import resource
import sqlite3
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from ..config import get_settings
from ..core.exceptions import FileProcessingError, ReportGenerationError
from ..data.checkpointed_state import (
    CHECKPOINTED_SCHEMA_VERSION,
    CheckpointedStateStore,
)
from ..data.database_manager import DatabaseManager
from ..data.excel_reader import ExcelReader
from ..data.snapshot_comparator import SnapshotComparator
from ..models import EnrollmentSnapshot
from ..services import MonitoringService, ReportingService, WebsiteService
from ..utils import get_section_sort_key
from ..website.config import get_configured_semesters
from .utils import detect_active_semester

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PollResult:
    """Structured result from a poll/process operation."""

    success: bool
    snapshot: object | None = None
    semester: str | None = None
    snapshot_id_before: int | None = None
    snapshot_id_after: int | None = None
    changed: bool = False
    change_score: float = 0.0
    # The parsed rows matched ``previous_digest``: nothing was stored or compared.
    unchanged: bool = False
    rows_digest: str | None = None
    # Handed on so the rest of the poll reads the same manager and snapshots.
    db_manager: DatabaseManager | None = None
    latest_snapshot: EnrollmentSnapshot | None = None
    previous_snapshot: EnrollmentSnapshot | None = None
    previous_snapshot_id: int | None = None


def _peak_rss_mb() -> float:
    """Peak resident set size of this process in MB (kilobytes on Linux)."""
    peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return peak / (1024 * 1024 if sys.platform == "darwin" else 1024)


class PollCommand:
    """Command for polling/downloading enrollment data."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    async def run(
        self, file_path: str | None = None, semester: str | None = None
    ) -> bool:
        """
        Run the polling command.

        Args:
            file_path: Optional specific file to process
            semester: Semester to poll; skips file and database detection

        Returns:
            bool: True if successful, False otherwise
        """
        result = await self.run_with_result(file_path=file_path, semester=semester)
        return result.success

    async def run_with_result(
        self,
        file_path: str | None = None,
        *,
        semester: str | None = None,
        previous_digest: str | None = None,
    ) -> PollResult:
        """Run polling and return a structured result for workflow callers.

        Args:
            file_path: Optional downloaded file to process instead of downloading
            semester: The known active semester; skips detection across databases
            previous_digest: Digest of the last processed rows. With ``file_path``,
                a file whose parsed rows match it is skipped entirely.
        """
        started = time.perf_counter()
        result = await self._run(file_path, semester, previous_digest)
        status = (
            "unchanged"
            if result.unchanged
            else "changed"
            if result.changed
            else "stored"
            if result.success
            else "failed"
        )
        logger.info(
            "Poll %s duration=%.2fs peak_rss=%.1fMB",
            status,
            time.perf_counter() - started,
            _peak_rss_mb(),
        )
        return result

    async def _resolve_semester(self, file_path: str | None) -> str | None:
        """The configured semester named by ``file_path``, else the freshest stored."""
        if file_path:
            try:
                label = ExcelReader().read_semester(file_path)
            except Exception:
                logger.debug("Could not read a semester from the file", exc_info=True)
            else:
                if label in get_configured_semesters():
                    return label
        return await detect_active_semester(self.debug)

    async def _run(
        self, file_path: str | None, semester: str | None, previous_digest: str | None
    ) -> PollResult:

        try:
            detected_semester = semester or await self._resolve_semester(file_path)
            if not detected_semester:
                raise FileProcessingError(
                    "no semester to poll; pass --semester or initialize storage "
                    'with `monitor db initialize --semester "<semester>"`'
                )
            monitoring_service = MonitoringService(semester=detected_semester)
            db_manager = monitoring_service.db_manager
            snapshot_id_before = db_manager.get_latest_snapshot_id()
            digest = None

            if file_path:
                # Process specific file
                logger.info("Poll started source=%s", Path(file_path).name)
                processed = await monitoring_service.process_specific_file(
                    file_path, previous_digest
                )
                success, snapshot, digest = (
                    processed.success,
                    processed.snapshot,
                    processed.digest,
                )
                if processed.unchanged:
                    logger.info("Registrar data unchanged; nothing stored")
                    return PollResult(
                        success=True,
                        semester=detected_semester,
                        snapshot_id_before=snapshot_id_before,
                        snapshot_id_after=snapshot_id_before,
                        unchanged=True,
                        rows_digest=digest,
                        db_manager=db_manager,
                    )
            else:
                # Download and process latest
                (
                    success,
                    snapshot,
                    _,
                ) = await monitoring_service.download_and_process_latest()

            if success and snapshot:
                logger.info(
                    "Poll processed semester=%s courses=%d overall_fill=%.1f%% timestamp=%s",
                    snapshot.semester,
                    len(snapshot.courses),
                    snapshot.overall_fill * 100,
                    snapshot.timestamp,
                )

                target_db_manager = monitoring_service.db_for(snapshot.semester)
                snapshot_id_after = target_db_manager.get_latest_snapshot_id()
                changed = (
                    snapshot.semester != detected_semester
                    or snapshot_id_before is None
                    or snapshot_id_after is None
                    or snapshot_id_before != snapshot_id_after
                )
                latest, previous, previous_id = (None, None, None)
                if changed:
                    latest, previous, previous_id = await asyncio.to_thread(
                        self._latest_pair, target_db_manager, snapshot_id_after
                    )
                change_score = self._calculate_change_score(latest, previous)

                return PollResult(
                    success=True,
                    snapshot=snapshot,
                    semester=snapshot.semester,
                    snapshot_id_before=snapshot_id_before,
                    snapshot_id_after=snapshot_id_after,
                    changed=changed,
                    change_score=change_score,
                    rows_digest=digest,
                    db_manager=target_db_manager,
                    latest_snapshot=latest,
                    previous_snapshot=previous,
                    previous_snapshot_id=previous_id,
                )
            else:
                logger.error("Poll failed: no data downloaded or processed")
                return PollResult(
                    success=False,
                    semester=detected_semester,
                    snapshot_id_before=snapshot_id_before,
                )

        except FileProcessingError:
            logger.exception("Poll file processing error")
            return PollResult(success=False)
        except Exception:
            logger.exception("Poll failed unexpectedly")
            return PollResult(success=False)

    @staticmethod
    def _latest_pair(
        db_manager: DatabaseManager, latest_id: int | None
    ) -> tuple[EnrollmentSnapshot | None, EnrollmentSnapshot | None, int | None]:
        """Read the latest stored snapshot and the one before it, once."""
        if latest_id is None:
            return None, None, None
        previous_id = db_manager.get_previous_snapshot_id(latest_id)
        latest = db_manager.get_snapshot_data(latest_id)
        previous = (
            db_manager.get_snapshot_data(previous_id)
            if previous_id is not None
            else None
        )
        return latest, previous, previous_id

    def _calculate_change_score(
        self,
        current_snapshot: EnrollmentSnapshot | None,
        previous_snapshot: EnrollmentSnapshot | None,
    ) -> float:
        """Score the latest poll's activity from the stored snapshots.

        Zero when nothing changed (no snapshots were read).
        """
        if not current_snapshot:
            return 0.0
        if not previous_snapshot:
            return 1.0

        comparison = SnapshotComparator().compare_snapshots(
            current_snapshot, previous_snapshot
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
                    and section_change.current_capacity
                    != section_change.previous_capacity
                ):
                    score += 3.0
                if (
                    section_change.current_instructor
                    != section_change.previous_instructor
                ):
                    score += 1.0

        return min(score, 100.0)


class ReportCommand:
    """Command for generating and optionally sending reports."""

    def __init__(
        self, debug: bool = False, no_telegram: bool = False, stateful: bool = False
    ):
        self.debug = debug
        self.no_telegram = no_telegram
        self.stateful = stateful

    async def run(self) -> bool:
        """
        Run the reporting command.

        Returns:
            bool: True if successful, False otherwise
        """
        logger.info(
            "Report started stateful=%s no_telegram=%s", self.stateful, self.no_telegram
        )

        try:
            # Try to detect active semester first
            detected_semester = await detect_active_semester(self.debug)
            # Create services
            # Note: MonitoringService is used for getting snapshots in standard mode
            monitoring_service = MonitoringService(semester=detected_semester)
            reporting_service = ReportingService(semester=detected_semester)

            # Handle stateful reporting
            if self.stateful:
                logger.debug("Running stateful reporting cycle")
                try:
                    # Run the cycle; exceptions will bubble up on actual failure
                    # Return value (bool) just indicates if reports were sent or not,
                    # but for the command CLI, "completed successfully" is what matters.
                    await reporting_service.run_stateful_report_cycle(
                        send_telegram=not self.no_telegram,
                        debug_mode=self.debug,
                    )
                    return True
                except Exception:
                    logger.exception("Stateful reporting failed")
                    return False

            # Standard Reporting Flow
            # Get latest snapshots
            current_snapshot, previous_snapshot = (
                monitoring_service.get_snapshot_comparison()
            )

            if not current_snapshot:
                logger.error("Report failed: no snapshots found in database")
                return False

            logger.debug(
                "Generating reports semester=%s current=%s previous=%s",
                current_snapshot.semester,
                current_snapshot.timestamp,
                previous_snapshot.timestamp if previous_snapshot else None,
            )

            # Generate reports with appropriate settings
            send_telegram = not self.no_telegram
            (
                success,
                generated_files,
            ) = await reporting_service.generate_and_send_reports(
                current_snapshot,
                previous_snapshot,
                send_telegram=send_telegram,
                debug_mode=self.debug,
            )

            if success:
                logger.info(
                    "Reports generated semester=%s count=%d telegram=%s files=%s",
                    current_snapshot.semester,
                    len(generated_files),
                    send_telegram and bool(generated_files),
                    [str(f) for f in generated_files],
                )
                if not generated_files:
                    logger.info("No comparison report generated")
            else:
                logger.error("Report generation failed")

            return success

        except ReportGenerationError:
            logger.exception("Reporting error")
            return False
        except Exception:
            logger.exception("Reporting failed unexpectedly")
            return False


class RunCommand:
    """Command for running the complete process."""

    def __init__(
        self,
        debug: bool = False,
        no_telegram: bool = False,
        deploy: bool = False,
    ):
        self.debug = debug
        self.no_telegram = no_telegram
        self.deploy = deploy

    async def run(self) -> bool:
        """
        Run the complete process command.

        Returns:
            bool: True if successful, False otherwise
        """
        logger.info(
            "Run started no_telegram=%s deploy=%s", self.no_telegram, self.deploy
        )

        try:
            # Step 1: Poll for data
            logger.debug("Step 1/3: polling")
            poll_command = PollCommand(debug=self.debug)
            poll_result = await poll_command.run_with_result()

            if not poll_result.success:
                logger.error("Run aborted: polling failed")
                return False

            logger.debug("Polling completed activity=%.2f", poll_result.change_score)

            # Step 2: Generate and send reports
            logger.debug("Step 2/3: reporting")
            report_command = ReportCommand(
                debug=self.debug, no_telegram=self.no_telegram
            )
            report_success = await report_command.run()

            if not report_success:
                logger.error("Run aborted: reporting failed")
                return False

            # Step 3: Generate website, and deploy only when explicitly requested.
            logger.debug("Step 3/3: website")
            website_service = WebsiteService()
            website_success = website_service.generate(
                semester_key=None,
                force=False,
                minify=True,
            )
            if not website_success:
                logger.error("Run aborted: website generation failed")
                return False
            if self.deploy:
                if getattr(website_service, "last_generation_skipped", False):
                    logger.error(
                        "Website generation was skipped; refusing stale deploy"
                    )
                    return False
                if not website_service.deploy():
                    return False

            logger.info("Run finished successfully")

            return True

        except Exception:
            logger.exception("Run failed unexpectedly")
            return False


class ScheduleCommand:
    """Command for running the scheduler."""

    def __init__(
        self,
        debug: bool = False,
        scheduler_type: str = "two-phase",
        no_telegram: bool = False,
    ):
        self.debug = debug
        self.no_telegram = no_telegram

    async def run(self) -> None:
        """Run the scheduler command."""
        try:
            from ..automation.scheduler import TwoPhaseScheduler

            scheduler = TwoPhaseScheduler(no_telegram=self.no_telegram)

            now = dt.datetime.now()
            poll_interval, _ = scheduler.get_next_poll_interval(0, update_state=False)
            next_sync_time = now + dt.timedelta(seconds=poll_interval)
            next_report_time = scheduler._get_next_report_time()
            pre_report_sync_time = next_report_time - dt.timedelta(seconds=60)

            # Determine which sync comes first
            if pre_report_sync_time > now and pre_report_sync_time < next_sync_time:
                next_sync = f"{pre_report_sync_time:%H:%M:%S} (pre-report)"
            else:
                next_sync = f"{next_sync_time:%H:%M:%S}"
            logger.info(
                "Scheduler starting no_telegram=%s next_sync=%s next_report=%s",
                self.no_telegram,
                next_sync,
                f"{next_report_time:%H:%M}",
            )

            await scheduler.start()

        except KeyboardInterrupt:
            logger.info("Scheduler stopped by user")
        except Exception:
            logger.exception("Scheduler failed")


class DatabaseCommands:
    """Commands for database operations."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    async def initialize_fresh_storage(
        self,
        *,
        semester: str,
        database: Path | None = None,
    ) -> bool:
        """Create one empty semester database on the v2 schema."""
        try:
            if not semester.strip():
                raise ValueError("fresh-semester initialization requires a semester")
            if database is None:
                data_dir = Path(get_settings().directories.data_storage)
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            if database.exists() and database.stat().st_size > 0:
                with sqlite3.connect(database) as connection:
                    version = int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    )
                if version != CHECKPOINTED_SCHEMA_VERSION:
                    raise ValueError(
                        f"{database} exists with unsupported schema version {version}"
                    )
                status = "already_initialized"
            else:
                CheckpointedStateStore(database)
                status = "initialized"
            logger.info(
                "Fresh storage %s semester=%s database=%s", status, semester, database
            )
            return True
        except Exception:
            logger.exception(
                "Fresh storage initialization failed semester=%s database=%s",
                semester,
                database,
            )
            return False

    async def stats(self) -> bool:
        """Show database statistics."""
        try:
            # Try to detect active semester for more relevant stats
            detected_semester = await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)
            stats = monitoring_service.get_database_stats()

            if stats:
                print("Database Statistics:")
                print(f"   Snapshots: {stats.get('snapshots', 0)}")
                print(f"   Courses: {stats.get('courses', 0)}")
                print(f"   Sections: {stats.get('sections', 0)}")
                print(
                    f"   Date range: {stats.get('earliest_snapshot', 'N/A')} to {stats.get('latest_snapshot', 'N/A')}"
                )

                return True
            else:
                logger.error("Unable to retrieve database statistics")
                return False

        except Exception:
            logger.exception("Database stats failed")
            return False


class BaselineCommand:
    """Mark the latest stored snapshot as reported without sending anything."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    async def run(self, semester: str | None = None) -> bool:
        try:
            semester = semester or await detect_active_semester(self.debug)
            if not semester:
                raise ValueError("no semester database found; pass --semester")
            slug = DatabaseManager._sanitize_semester_name_static(semester)
            data_dir = Path(get_settings().directories.data_storage)
            if not (data_dir / f"enrollment_{slug}.db").is_file():
                raise ValueError(f"no database for {semester!r}")
            db_manager = DatabaseManager(semester=semester)
            latest = db_manager.get_latest_snapshot_id()
            if latest is None:
                raise ValueError(f"{semester} has no stored snapshots")
            previous = db_manager.get_last_reported_snapshot_id()
            if previous == latest:
                print(f"{semester}: snapshot {latest} is already the baseline")
                return True
            db_manager.add_reporting_log(snapshot_id=latest, changes_were_found=False)
            print(
                f"{semester}: last reported snapshot {previous} -> {latest}. "
                "Nothing was sent."
            )
            return True
        except Exception as error:
            logger.warning("Baseline failed: %s", error)
            return False


class StatusCommand:
    """Command for checking status of specific courses."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    async def run(self, courses: list[str], semester: str | None = None) -> bool:
        """
        Run the status command.

        Args:
            courses: List of course codes to check
            semester: Optional specific semester
        """
        try:
            detected_semester = semester or await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)

            # Get latest snapshot
            snapshot = monitoring_service.get_latest_snapshot()
            if not snapshot:
                logger.error("No data found semester=%s", detected_semester)
                return False

            print(f"Course Status for {snapshot.semester}")
            print(f"   (Data from {snapshot.timestamp})")
            print("-" * 50)

            found_any = False
            for course_code in courses:
                course = snapshot.courses.get(course_code)
                if course:
                    found_any = True
                    self._print_course_status(course)
                else:
                    logger.warning(
                        "Course not found course=%s semester=%s",
                        course_code,
                        snapshot.semester,
                    )

            return found_any

        except Exception:
            logger.exception("Status check failed")
            return False

    def _print_course_status(self, course) -> None:
        """Print detailed status for a course."""
        print(f"\n{course.course_code}: {course.course_title or 'No Title'}")
        print(
            f"   Total Enrollment: {course.total_enrollment}/{course.total_capacity} ({course.average_fill:.1%})"
        )

        # Sort sections by type priority (Lectures first) and then natural sort of ID
        sorted_sections = sorted(
            course.sections.values(),
            key=lambda s: get_section_sort_key(s.section_id, s.section_type),
        )

        for section in sorted_sections:
            status_icon = (
                "🔴" if section.is_filled else "🟡" if section.is_near_filled else "🟢"
            )
            print(
                f"   {status_icon} Section {section.section_id} ({section.section_type}): {section.enrollment}/{section.capacity}"
            )


class DeployCommand:
    """Command for generating the website and deploying it as Worker assets."""

    def __init__(self, debug: bool = False):
        self.debug = debug

    def run(
        self,
        deploy: bool = False,
        semester: str | None = None,
        force: bool = False,
        minify: bool = True,
        output_dir: Path | None = None,
        dry_run: bool = False,
        upload_only: bool = False,
    ) -> bool:
        """Run the deploy command.

        ``dry_run`` still generates, then runs ``wrangler deploy --dry-run``.
        ``upload_only`` skips generation and deploys the existing output.
        """
        if (deploy or dry_run or upload_only) and output_dir is not None:
            logger.error("Isolated website output is generation-only; refusing deploy")
            return False
        if upload_only and (semester or force):
            logger.error("--upload-only skips generation; drop --semester and --force")
            return False

        service = (
            WebsiteService(output_dir=output_dir)
            if output_dir is not None
            else WebsiteService()
        )

        if upload_only:
            return service.deploy(dry_run=dry_run)

        # Step 1: Generate
        success = service.generate(semester_key=semester, force=force, minify=minify)
        if not success:
            return False

        # Step 2: Deploy if requested. A dry run uploads nothing, so a skipped
        # generation does not make it stale.
        if deploy or dry_run:
            if not dry_run and getattr(service, "last_generation_skipped", False):
                logger.error("Website generation was skipped; refusing stale deploy")
                return False
            return service.deploy(dry_run=dry_run)

        return True
