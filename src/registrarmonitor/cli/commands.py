"""Command implementations for the registrarmonitor CLI."""

import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from ..config import get_config
from ..core import get_logger
from ..core.exceptions import FileProcessingError, ReportGenerationError
from ..data.database_manager import DatabaseManager
from ..data.migration import (
    MetadataMode,
    MigrationRequest,
    _legacy_fingerprint,
    finalize_storage,
    run_migration,
    transition_storage_mode,
)
from ..data.migration import (
    initialize_fresh_storage as initialize_fresh_storage_database,
)
from ..data.migration_rehearsal import RehearsalRequest, run_rehearsal
from ..data.snapshot_comparator import SnapshotComparator
from ..services import MonitoringService, ReportingService, WebsiteService
from ..utils import get_section_sort_key
from ..website.config import OUTPUT_DIR, semester_to_slug
from ..website.static_manifest import rollback_semester_pointer
from .utils import detect_active_semester


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


class PollCommand:
    """Command for polling/downloading enrollment data."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    async def run(self, file_path: str | None = None) -> bool:
        """
        Run the polling command.

        Args:
            file_path: Optional specific file to process

        Returns:
            bool: True if successful, False otherwise
        """
        result = await self.run_with_result(file_path=file_path)
        return result.success

    async def run_with_result(self, file_path: str | None = None) -> PollResult:
        """Run polling and return a structured result for workflow callers."""
        if self.debug:
            print("🔍 DEBUG MODE: Polling for enrollment data")

        self.logger.info("Starting polling command")

        try:
            # Try to detect active semester first
            detected_semester = await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)
            db_manager_before = DatabaseManager(semester=detected_semester)
            snapshot_id_before = db_manager_before.get_latest_snapshot_id()

            if file_path:
                # Process specific file
                print(f"📁 Processing specific file: {Path(file_path).name}")
                success, snapshot = monitoring_service.process_specific_file(file_path)
            else:
                # Download and process latest
                print("📥 Downloading and processing latest enrollment data...")
                (
                    success,
                    snapshot,
                    _,
                ) = await monitoring_service.download_and_process_latest()

            if success and snapshot:
                print(
                    f"✅ Successfully processed {len(snapshot.courses)} courses for {snapshot.semester}"
                )
                if self.debug:
                    print(f"   📈 Overall fill: {snapshot.overall_fill:.1%}")
                    print(f"   🔍 DEBUG: Timestamp: {snapshot.timestamp}")
                    print(f"   🔍 DEBUG: Semester: {snapshot.semester}")

                target_db_manager = DatabaseManager.create_for_semester(
                    snapshot.semester
                )
                snapshot_id_after = target_db_manager.get_latest_snapshot_id()
                changed = (
                    snapshot.semester != detected_semester
                    or snapshot_id_before is None
                    or snapshot_id_after is None
                    or snapshot_id_before != snapshot_id_after
                )
                change_score = self._calculate_change_score(snapshot.semester, changed)

                return PollResult(
                    success=True,
                    snapshot=snapshot,
                    semester=snapshot.semester,
                    snapshot_id_before=snapshot_id_before,
                    snapshot_id_after=snapshot_id_after,
                    changed=changed,
                    change_score=change_score,
                )
            else:
                print("❌ Failed to download or process data")
                return PollResult(
                    success=False,
                    semester=detected_semester,
                    snapshot_id_before=snapshot_id_before,
                )

        except FileProcessingError as e:
            print(f"❌ File processing error: {e}")
            self.logger.error(f"File processing error in polling: {e}")
            return PollResult(success=False)
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            self.logger.error(f"Unexpected error in polling: {e}")
            return PollResult(success=False)

    def _calculate_change_score(self, semester: str, changed: bool) -> float:
        """Calculate the latest poll's activity score using the stored snapshots."""
        if not changed:
            return 0.0

        monitoring_service = MonitoringService(semester=semester)
        current_snapshot, previous_snapshot = (
            monitoring_service.get_snapshot_comparison()
        )
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
        self.logger = get_logger(__name__)

    async def run(self) -> bool:
        """
        Run the reporting command.

        Returns:
            bool: True if successful, False otherwise
        """
        if self.debug:
            mode_str = "stateful" if self.stateful else "standard"
            telegram_str = "(no Telegram)" if self.no_telegram else "(with Telegram)"
            print(
                f"🔍 DEBUG MODE: Generating reports - Mode: {mode_str} {telegram_str}"
            )

        self.logger.info(
            f"Starting reporting command (stateful={self.stateful}, no_telegram={self.no_telegram})"
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
                if self.debug:
                    print("🔄 Running stateful reporting cycle...")

                try:
                    # Run the cycle; exceptions will bubble up on actual failure
                    # Return value (bool) just indicates if reports were sent or not,
                    # but for the command CLI, "completed successfully" is what matters.
                    await reporting_service.run_stateful_report_cycle(
                        send_telegram=not self.no_telegram,
                        debug_mode=self.debug,
                    )
                    return True
                except Exception as e:
                    print(f"❌ Stateful reporting failed: {e}")
                    # Re-raise to let the outer exception handler log it too if needed
                    raise e

            # Standard Reporting Flow
            # Get latest snapshots
            current_snapshot, previous_snapshot = (
                monitoring_service.get_snapshot_comparison()
            )

            if not current_snapshot:
                print("❌ No snapshots found in database")
                return False

            if self.debug:
                print(f"📊 Generating reports for {current_snapshot.semester}")
                if previous_snapshot:
                    print("📊 Previous snapshot available for comparison")
                    print(
                        f"   🔍 DEBUG: Current snapshot timestamp: {current_snapshot.timestamp}"
                    )
                    print(
                        f"   🔍 DEBUG: Previous snapshot timestamp: {previous_snapshot.timestamp}"
                    )
                else:
                    print("⚠️  No previous snapshot for comparison")

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
                print(f"✅ Generated {len(generated_files)} reports:")
                for file_path in generated_files:
                    print(f"   📄 {file_path}")

                if not generated_files:
                    print("ℹ️  No comparison report generated")
                elif send_telegram:
                    print("📱 Reports sent to Telegram")
                else:
                    print("💾 Reports saved locally (Telegram disabled)")

                if self.debug:
                    print("🔍 DEBUG: Report generation complete")

            else:
                print("❌ Failed to generate reports")

            return success

        except ReportGenerationError as e:
            print(f"❌ Reporting error: {e}")
            self.logger.error(f"Reporting error: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            self.logger.error(f"Unexpected error in reporting: {e}")
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
        self.logger = get_logger(__name__)

    async def run(self) -> bool:
        """
        Run the complete process command.

        Returns:
            bool: True if successful, False otherwise
        """
        if self.debug:
            print(
                f"🔍 DEBUG MODE: Running complete process {'(no Telegram)' if self.no_telegram else '(with Telegram)'}"
            )

        self.logger.info("Starting complete process workflow")

        try:
            if self.debug:
                print("🚀 Starting complete process: Poll → Report → Website")
                print("=" * 50)

            # Step 1: Poll for data
            if self.debug:
                print("📥 Step 1/3: Polling for enrollment data...")
            poll_command = PollCommand(debug=self.debug)
            poll_result = await poll_command.run_with_result()

            if not poll_result.success:
                print("❌ Polling failed. Aborting complete process.")
                return False

            if self.debug:
                print(
                    f"✅ Polling completed successfully (activity: {poll_result.change_score:.2f})"
                )
                print("-" * 30)

            # Step 2: Generate and send reports
            if self.debug:
                print("📊 Step 2/3: Generating reports...")
            report_command = ReportCommand(
                debug=self.debug, no_telegram=self.no_telegram
            )
            report_success = await report_command.run()

            if not report_success:
                print("❌ Reporting failed")
                return False

            # Step 3: Generate website, and deploy only when explicitly requested.
            if self.debug:
                print("🌐 Step 3/3: Generating website...")
            website_service = WebsiteService()
            website_success = website_service.generate(
                semester_key=None,
                force=False,
                minify=True,
            )
            if not website_success:
                print("❌ Website generation failed")
                return False
            if self.deploy:
                if getattr(website_service, "last_generation_skipped", False):
                    print("❌ Website generation was skipped; refusing stale deploy.")
                    return False
                if not website_service.deploy():
                    return False

            print("✅ Complete process finished successfully!")

            return True

        except Exception as e:
            print(f"❌ Unexpected error in complete process: {e}")
            self.logger.error(f"Unexpected error in complete process: {e}")
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
        self.logger = get_logger(__name__)

    async def run(self) -> None:
        """Run the scheduler command."""
        if self.debug:
            print("🔍 DEBUG MODE: Starting scheduler")

        self.logger.info("Starting scheduler")

        try:
            from ..automation.scheduler import TwoPhaseScheduler

            print("⏰ Starting Two-Phase Scheduler...")
            print("   📅 Schedule: settings.toml milestones")
            print("   🔄 Two-phase mode: Quiet/Burst separation (with quiet decay)")
            if self.no_telegram:
                print("   📵 Telegram reports: DISABLED")
            print("   🛑 Press Ctrl+C to stop")
            scheduler = TwoPhaseScheduler(no_telegram=self.no_telegram)

            if self.debug:
                print("🔍 DEBUG: Scheduler will show detailed logs")

            # Display next sync and report times
            import datetime

            now = datetime.datetime.now()
            poll_interval, _ = scheduler.get_next_poll_interval(0)
            next_sync_time = now + datetime.timedelta(seconds=poll_interval)
            next_report_time = scheduler._get_next_report_time()
            pre_report_sync_time = next_report_time - datetime.timedelta(seconds=60)

            # Determine which sync comes first
            if pre_report_sync_time > now and pre_report_sync_time < next_sync_time:
                print(
                    f"   📥 Pre-report sync at: {pre_report_sync_time.strftime('%H:%M:%S')}",
                    flush=True,
                )
            else:
                print(
                    f"   🔄 Next sync at: {next_sync_time.strftime('%H:%M:%S')}",
                    flush=True,
                )
            print(
                f"   📨 Next report at: {next_report_time.strftime('%H:%M')}",
                flush=True,
            )

            await scheduler.start()

        except KeyboardInterrupt:
            print("\n🛑 Scheduler stopped by user")
            self.logger.info("Scheduler stopped by user")
        except Exception as e:
            print(f"❌ Scheduler error: {e}")
            self.logger.error(f"Scheduler error: {e}")


class DatabaseCommands:
    """Commands for database operations."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    @staticmethod
    def _storage_config(semester: str) -> dict[str, object]:
        config = get_config()
        storage = config.get("storage", {})
        semesters = storage.get("semesters", {}) if isinstance(storage, dict) else {}
        semester_config = (
            semesters.get(semester) if isinstance(semesters, dict) else None
        )
        if semester_config is None:
            raise ValueError(f"no storage rollout configuration for {semester!r}")
        if not isinstance(semester_config, dict):
            raise TypeError(f"invalid storage configuration for {semester!r}")
        return semester_config

    @staticmethod
    def _validate_migration_order(
        semester: str,
        data_dir: Path,
        completed_predecessor_dir: Path | None = None,
    ) -> None:
        """Require every earlier configured semester to be fully migrated."""
        config = get_config()
        storage = config.get("storage", {})
        order = storage.get("migration_order", []) if isinstance(storage, dict) else []
        if not isinstance(order, list) or not all(
            isinstance(item, str) for item in order
        ):
            raise TypeError("storage.migration_order must be a list of semester names")
        if semester not in order:
            raise ValueError(f"{semester!r} is not in storage.migration_order")

        for prior_semester in order[: order.index(semester)]:
            slug = DatabaseManager._sanitize_semester_name_static(prior_semester)
            prior_database = data_dir / f"enrollment_{slug}.db"
            if not prior_database.is_file():
                raise ValueError(
                    f"migration order blocked: {prior_semester!r} has no database"
                )
            completed = False
            try:
                with sqlite3.connect(
                    f"{prior_database.resolve().as_uri()}?mode=ro",
                    uri=True,
                ) as connection:
                    version = int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    )
                    control = connection.execute(
                        "SELECT migration_phase FROM storage_control "
                        "WHERE singleton = 1"
                    ).fetchone()
                completed = version == 2 and control in {
                    ("complete",),
                    ("finalized",),
                }
            except sqlite3.Error:
                completed = False

            if completed:
                continue

            if completed_predecessor_dir is None:
                raise ValueError(
                    f"migration order blocked: {prior_semester!r} is incomplete"
                )

            candidate_name = f"{slug.replace('_', '-')}-candidate.db"
            candidate = completed_predecessor_dir / candidate_name
            try:
                with sqlite3.connect(
                    f"{candidate.resolve().as_uri()}?mode=ro",
                    uri=True,
                ) as connection:
                    candidate_version = int(
                        connection.execute("PRAGMA user_version").fetchone()[0]
                    )
                    candidate_control = connection.execute(
                        "SELECT semester, migration_phase, legacy_fingerprint "
                        "FROM storage_control WHERE singleton = 1"
                    ).fetchone()
            except sqlite3.Error as error:
                raise ValueError(
                    "migration order blocked: verified candidate missing or invalid "
                    f"for {prior_semester!r}: {candidate}"
                ) from error
            expected_control = (
                prior_semester,
                "complete",
                _legacy_fingerprint(prior_database),
            )
            if candidate_version != 2 or candidate_control != expected_control:
                raise ValueError(
                    "migration order blocked: predecessor candidate does not match "
                    f"the current {prior_semester!r} source"
                )

    async def initialize_fresh_storage(
        self,
        *,
        semester: str,
        report_path: Path,
        database: Path | None = None,
    ) -> bool:
        """Create one empty semester database in the controlled shadow mode."""
        try:
            semester_config = self._storage_config(semester)
            if semester_config.get("mode") != "shadow":
                raise ValueError(
                    "fresh-semester initialization requires the approved mode "
                    "to be shadow"
                )
            metadata_mode = MetadataMode(str(semester_config["metadata_mode"]))
            if database is None:
                config = get_config()
                data_dir = Path(config["directories"]["data_storage"])
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            result = initialize_fresh_storage_database(
                database,
                semester=semester,
                metadata_mode=metadata_mode,
                report_path=report_path,
            )
            print(
                f"✅ Fresh storage {result.status}: {semester}; "
                f"mode={result.active_mode}; report={result.report_path}"
            )
            return True
        except Exception as error:
            print(f"❌ Fresh storage initialization failed: {error}")
            self.logger.error(f"Fresh storage initialization error: {error}")
            return False

    async def stats(self) -> bool:
        """Show database statistics."""
        try:
            # Try to detect active semester for more relevant stats
            detected_semester = await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)
            stats = monitoring_service.get_database_stats()

            if stats:
                print("\n📊 Database Statistics:")
                print(f"   Snapshots: {stats.get('snapshots', 0)}")
                print(f"   Courses: {stats.get('courses', 0)}")
                print(f"   Sections: {stats.get('sections', 0)}")
                print(
                    f"   Date range: {stats.get('earliest_snapshot', 'N/A')} to {stats.get('latest_snapshot', 'N/A')}"
                )

                if self.debug:
                    print("🔍 DEBUG: Additional database info available")
                    if detected_semester:
                        print(
                            f"🔍 DEBUG: Active semester detected: {detected_semester}"
                        )

                return True
            else:
                print("❌ Unable to retrieve database statistics")
                return False

        except Exception as e:
            print(f"❌ Error getting database stats: {e}")
            self.logger.error(f"Database stats error: {e}")
            return False

    async def cleanup(self, keep_count: int = 50) -> bool:
        """Clean up old snapshots from the database."""
        try:
            if self.debug:
                print(f"🔍 DEBUG: Cleaning up database, keeping {keep_count} snapshots")

            # Try to detect active semester for cleanup
            detected_semester = await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)
            deleted_count = monitoring_service.cleanup_old_data(keep_count)

            if deleted_count > 0:
                print(f"✅ Cleaned up {deleted_count} old snapshots")
                print(f"   📊 Kept {keep_count} most recent snapshots")
            else:
                print("✅ No old snapshots to clean up")

            return True

        except Exception as e:
            print(f"❌ Error cleaning up snapshots: {e}")
            self.logger.error(f"Database cleanup error: {e}")
            return False

    async def dedupe_instructor_changes(self, *, dry_run: bool = False) -> bool:
        """Remove consecutive duplicate instructor change records."""
        try:
            detected_semester = await detect_active_semester(self.debug)
            db_manager = DatabaseManager(semester=detected_semester)
            duplicate_count = db_manager.dedupe_instructor_changes(dry_run=dry_run)

            if dry_run:
                print(
                    f"✅ Dry run complete: {duplicate_count} duplicate instructor change row(s) found"
                )
            else:
                print(
                    f"✅ Removed {duplicate_count} duplicate instructor change row(s)"
                )
            return True

        except Exception as e:
            print(f"❌ Error deduping instructor changes: {e}")
            self.logger.error(f"Instructor change dedupe error: {e}")
            return False

    async def migrate(
        self,
        *,
        semester: str,
        target_version: int,
        metadata_mode: str,
        report_path: Path,
        dry_run: bool,
        authorized: bool = False,
        database: Path | None = None,
        candidate: Path | None = None,
        backup_dir: Path | None = None,
        raw_dir: Path | None = None,
        completed_predecessor_dir: Path | None = None,
    ) -> bool:
        """Run or resume one explicitly scoped schema migration."""
        try:
            if not dry_run and not authorized:
                raise ValueError(
                    "migration apply requires explicit operator authorization"
                )
            semester_config = self._storage_config(semester)
            if semester_config.get("metadata_mode") != metadata_mode:
                raise ValueError(
                    "requested metadata mode disagrees with settings.toml: "
                    f"{metadata_mode!r} != "
                    f"{semester_config.get('metadata_mode')!r}"
                )
            if semester_config.get("mode") != "legacy":
                raise ValueError(
                    "schema migration requires the semester's approved mode "
                    "to remain legacy"
                )
            config = get_config()
            data_dir = Path(config["directories"]["data_storage"])
            if completed_predecessor_dir is not None and not dry_run:
                raise ValueError(
                    "predecessor candidates are accepted only for dry runs"
                )
            self._validate_migration_order(
                semester,
                data_dir,
                completed_predecessor_dir=completed_predecessor_dir,
            )
            if database is None:
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            result = run_migration(
                MigrationRequest(
                    database=database,
                    semester=semester,
                    target_version=target_version,
                    metadata_mode=MetadataMode(metadata_mode),
                    report_path=report_path,
                    dry_run=dry_run,
                    authorized=authorized,
                    candidate_path=candidate,
                    backup_dir=backup_dir,
                    raw_dir=raw_dir,
                )
            )
            print(
                f"✅ Migration {result.status}: {semester}; report={result.report_path}"
            )
            if result.backup_path is not None:
                print(f"   Verified backup: {result.backup_path}")
            return True
        except Exception as error:
            print(f"❌ Migration failed: {error}")
            self.logger.error(f"Database migration error: {error}")
            return False

    async def transition_mode(
        self,
        *,
        semester: str,
        target_mode: str,
        report_path: Path,
        database: Path | None = None,
    ) -> bool:
        """Audit and change one semester's compatibility mode."""
        try:
            semester_config = self._storage_config(semester)
            if semester_config.get("mode") != target_mode:
                raise ValueError(
                    "target mode disagrees with settings.toml; update the "
                    "approved semester mode before transitioning"
                )
            if database is None:
                config = get_config()
                data_dir = Path(config["directories"]["data_storage"])
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            result = transition_storage_mode(
                database,
                semester=semester,
                target_mode=target_mode,
                report_path=report_path,
            )
            print(
                f"✅ Storage mode {result.status}: "
                f"{result.previous_mode} → {result.active_mode}; "
                f"report={result.report_path}"
            )
            return True
        except Exception as error:
            print(f"❌ Storage mode transition failed: {error}")
            self.logger.error(f"Storage mode transition error: {error}")
            return False

    async def finalize_storage(
        self,
        *,
        semester: str,
        report_path: Path,
        authorized: bool,
        database: Path | None = None,
        rollback_dir: Path | None = None,
    ) -> bool:
        """Compact one v2 database after explicit operator authorization."""
        try:
            configured_mode = self._storage_config(semester).get("mode")
            if configured_mode not in {"v2", "finalized"}:
                raise ValueError(
                    "finalization requires the semester's approved mode to be "
                    "v2 or finalized"
                )
            if database is None:
                config = get_config()
                data_dir = Path(config["directories"]["data_storage"])
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            result = finalize_storage(
                database,
                semester=semester,
                report_path=report_path,
                rollback_dir=rollback_dir,
                authorized=authorized,
            )
            print(
                f"✅ Storage finalization {result.status}: {semester}; "
                f"archive={result.archive_path}; report={result.report_path}"
            )
            return True
        except Exception as error:
            print(f"❌ Storage finalization failed: {error}")
            self.logger.error(f"Storage finalization error: {error}")
            return False

    async def rehearse_migration(
        self,
        *,
        semester: str,
        target_version: int,
        metadata_mode: str,
        report_path: Path,
        evidence_dir: Path,
        database: Path | None = None,
        raw_dir: Path | None = None,
        completed_predecessor_dir: Path | None = None,
        workers: int = 1,
        snapshot_stride: int = 1,
    ) -> bool:
        """Exercise every restart boundary on disposable database copies."""
        try:
            semester_config = self._storage_config(semester)
            if semester_config.get("metadata_mode") != metadata_mode:
                raise ValueError(
                    "requested metadata mode disagrees with settings.toml: "
                    f"{metadata_mode!r} != "
                    f"{semester_config.get('metadata_mode')!r}"
                )
            if semester_config.get("mode") != "legacy":
                raise ValueError(
                    "migration rehearsal requires the semester's approved "
                    "mode to remain legacy"
                )
            config = get_config()
            data_dir = Path(config["directories"]["data_storage"])
            self._validate_migration_order(
                semester,
                data_dir,
                completed_predecessor_dir=completed_predecessor_dir,
            )
            if database is None:
                slug = DatabaseManager._sanitize_semester_name_static(semester)
                database = data_dir / f"enrollment_{slug}.db"
            report = run_rehearsal(
                RehearsalRequest(
                    database=database,
                    semester=semester,
                    target_version=target_version,
                    metadata_mode=MetadataMode(metadata_mode),
                    report_path=report_path,
                    evidence_dir=evidence_dir,
                    raw_dir=raw_dir,
                    workers=workers,
                    snapshot_stride=snapshot_stride,
                )
            )
            print(
                f"✅ Migration rehearsal {report['status']}: {semester}; "
                f"{report['passed']}/{report['scenario_count']} scenarios passed; "
                f"report={report_path}"
            )
            return report["status"] == "passed"
        except Exception as error:
            print(f"❌ Migration rehearsal failed: {error}")
            self.logger.error(f"Migration rehearsal error: {error}")
            return False

    async def rollback_manifest(
        self,
        *,
        semester: str,
        report_path: Path,
        output_dir: Path | None = None,
    ) -> bool:
        """Restore a semester's prior pointer without deleting artifacts."""
        try:
            result = rollback_semester_pointer(
                output_dir or OUTPUT_DIR,
                semester_slug=semester_to_slug(semester),
            )
            report = {
                "status": result.status,
                "semester": semester,
                "build_id": result.build_id,
                "pointer": str(result.pointer_path),
                "manifest": str(result.manifest_path),
            }
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(
                json.dumps(report, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            print(
                f"✅ Static pointer rolled back: {semester}; "
                f"build={result.build_id}; report={report_path}"
            )
            return True
        except Exception as error:
            print(f"❌ Static pointer rollback failed: {error}")
            self.logger.error(f"Static pointer rollback error: {error}")
            return False


class StatusCommand:
    """Command for checking status of specific courses."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    async def run(self, courses: list[str], semester: str | None = None) -> bool:
        """
        Run the status command.

        Args:
            courses: List of course codes to check
            semester: Optional specific semester
        """
        if self.debug:
            print(f"🔍 DEBUG MODE: Checking status for {courses}")

        try:
            detected_semester = semester or await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)

            # Get latest snapshot
            snapshot = monitoring_service.get_latest_snapshot()
            if not snapshot:
                print(f"❌ No data found for semester {detected_semester}")
                return False

            print(f"📊 Course Status for {snapshot.semester}")
            print(f"   (Data from {snapshot.timestamp})")
            print("-" * 50)

            found_any = False
            for course_code in courses:
                course = snapshot.courses.get(course_code)
                if course:
                    found_any = True
                    self._print_course_status(course)
                else:
                    print(f"⚠️  Course not found: {course_code}")

            return found_any

        except Exception as e:
            print(f"❌ Error checking status: {e}")
            self.logger.error(f"Status check error: {e}")
            return False

    def _print_course_status(self, course) -> None:
        """Print detailed status for a course."""
        print(f"\n📘 {course.course_code}: {course.course_title or 'No Title'}")
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
    """Command for generating and deploying the website."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    def run(
        self,
        deploy: bool = False,
        semester: str | None = None,
        force: bool = False,
        minify: bool = True,
        project_name: str = "registrar-monitor",
        branch: str | None = None,
        prototype: bool = False,
        output_dir: Path | None = None,
    ) -> bool:
        """Run the deploy command."""
        if self.debug:
            print("🔍 DEBUG MODE: Website generation/deployment")

        if deploy and output_dir is not None:
            print("❌ Isolated website output is generation-only; refusing deploy.")
            return False

        service = (
            WebsiteService(output_dir=output_dir)
            if output_dir is not None
            else WebsiteService()
        )

        if prototype:
            if deploy:
                print("❌ Prototype generation is local-only; refusing deploy.")
                return False
            return service.generate_prototype(semester_key=semester)

        # Step 1: Generate
        success = service.generate(semester_key=semester, force=force, minify=minify)
        if not success:
            return False

        # Step 2: Deploy if requested
        if deploy:
            if getattr(service, "last_generation_skipped", False):
                print("❌ Website generation was skipped; refusing stale deploy.")
                return False
            return service.deploy(project_name=project_name, branch=branch)

        return True
