"""Command implementations for the registrarmonitor CLI."""

from pathlib import Path
from typing import Optional, List


from ..core import get_logger
from ..core.exceptions import FileProcessingError, ReportGenerationError
from ..data.database_manager import DatabaseManager
from ..data.migrate_json_to_db import JSONMigrator
from ..services import MonitoringService, ReportingService, WebsiteService
from ..data.instructor_populator import populate_instructors
from .utils import detect_active_semester
from ..utils import get_section_sort_key


class PollCommand:
    """Command for polling/downloading enrollment data."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    async def run(self, file_path: Optional[str] = None) -> bool:
        """
        Run the polling command.

        Args:
            file_path: Optional specific file to process

        Returns:
            bool: True if successful, False otherwise
        """
        if self.debug:
            print("🔍 DEBUG MODE: Polling for enrollment data")

        self.logger.info("Starting polling command")

        try:
            # Try to detect active semester first
            detected_semester = await detect_active_semester(self.debug)
            monitoring_service = MonitoringService(semester=detected_semester)

            if file_path:
                # Process specific file
                print(f"📁 Processing specific file: {Path(file_path).name}")
                success, snapshot = monitoring_service.process_specific_file(file_path)
                excel_source = file_path
            else:
                # Download and process latest
                print("📥 Downloading and processing latest enrollment data...")
                (
                    success,
                    snapshot,
                    downloaded_path,
                ) = await monitoring_service.download_and_process_latest()
                excel_source = downloaded_path

            if success and snapshot:
                try:
                    # Always use the semester from the snapshot to find the correct database
                    # This handles the case where we just started monitoring a new semester
                    target_db_manager = DatabaseManager.create_for_semester(
                        snapshot.semester
                    )
                    current_db_path = str(target_db_manager.db_path)

                    if current_db_path:
                        if excel_source:
                            self.logger.info(
                                f"Populating instructor data for {snapshot.semester} in {current_db_path}"
                            )
                            populate_instructors(current_db_path, excel_source)
                        else:
                            self.logger.warning(
                                "Could not find Excel file source to populate instructors."
                            )

                except Exception as e:
                    self.logger.error(f"Failed to populate instructors: {e}")

                print(
                    f"✅ Successfully processed {len(snapshot.courses)} courses for {snapshot.semester}"
                )
                if self.debug:
                    print(f"   📈 Overall fill: {snapshot.overall_fill:.1%}")
                    print(f"   🔍 DEBUG: Timestamp: {snapshot.timestamp}")
                    print(f"   🔍 DEBUG: Semester: {snapshot.semester}")

                return True
            else:
                print("❌ Failed to download or process data")
                return False

        except FileProcessingError as e:
            print(f"❌ File processing error: {e}")
            self.logger.error(f"File processing error in polling: {e}")
            return False
        except Exception as e:
            print(f"❌ Unexpected error: {e}")
            self.logger.error(f"Unexpected error in polling: {e}")
            return False


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
                        debug_mode=self.debug
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

                if send_telegram:
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
    """Command for running the complete process (poll + report)."""

    def __init__(self, debug: bool = False, no_telegram: bool = False):
        self.debug = debug
        self.no_telegram = no_telegram
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
                print("🚀 Starting complete process: Poll → Report")
                print("=" * 50)

            # Step 1: Poll for data
            if self.debug:
                print("📥 Step 1/2: Polling for enrollment data...")
            poll_command = PollCommand(debug=self.debug)
            poll_success = await poll_command.run()

            if not poll_success:
                print("❌ Polling failed. Aborting complete process.")
                return False

            if self.debug:
                print("✅ Polling completed successfully")
                print("-" * 30)

            # Step 2: Generate and send reports
            if self.debug:
                print("📊 Step 2/2: Generating and sending reports...")
            report_command = ReportCommand(
                debug=self.debug, no_telegram=self.no_telegram
            )
            report_success = await report_command.run()

            if not report_success:
                print("❌ Reporting failed")
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
        scheduler_type: str = "hybrid",
        no_telegram: bool = False,
    ):
        self.debug = debug
        self.scheduler_type = scheduler_type
        self.no_telegram = no_telegram
        self.logger = get_logger(__name__)

    async def run(self) -> None:
        """Run the scheduler command."""
        if self.debug:
            print(f"🔍 DEBUG MODE: Starting scheduler (type: {self.scheduler_type})")

        self.logger.info(f"Starting scheduler (type: {self.scheduler_type})")

        try:
            from ..automation.scheduler import HybridScheduler, TwoPhaseScheduler

            if self.scheduler_type == "two-phase":
                print("⏰ Starting two-phase scheduler...")
                print("   📅 Schedule file: schedule.txt")
                print("   🔄 Two-phase mode: Quiet/Burst separation")
                if self.no_telegram:
                    print("   📵 Telegram reports: DISABLED")
                print("   🛑 Press Ctrl+C to stop")
                scheduler = TwoPhaseScheduler(no_telegram=self.no_telegram)
            else:  # hybrid (default)
                print("⏰ Starting hybrid scheduler...")
                print("   📅 Schedule file: schedule.txt")
                print("   🔄 Activity-based adaptation enabled")
                if self.no_telegram:
                    print("   📵 Telegram reports: DISABLED")
                print("   🛑 Press Ctrl+C to stop")
                scheduler = HybridScheduler(no_telegram=self.no_telegram)

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
                    detected_semester = await detect_active_semester(self.debug)
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

    def migrate(self) -> bool:
        """Migrate JSON files to database."""
        try:
            if self.debug:
                print("🔍 DEBUG: Starting JSON to database migration")

            migrator = JSONMigrator()

            print("🔄 Starting JSON to database migration...")
            results = migrator.migrate_all()

            if results:
                total_migrated = sum(results.values())
                print(f"✅ Migration completed! {total_migrated} files migrated")
                for semester, count in results.items():
                    print(f"   {semester}: {count} files")

                if self.debug:
                    print("🔍 DEBUG: Migration details available in logs")

                return True
            else:
                print("ℹ️  No files to migrate")
                return True

        except Exception as e:
            print(f"❌ Migration error: {e}")
            self.logger.error(f"Database migration error: {e}")
            return False


class StatusCommand:
    """Command for checking status of specific courses."""

    def __init__(self, debug: bool = False):
        self.debug = debug
        self.logger = get_logger(__name__)

    async def run(self, courses: List[str], semester: Optional[str] = None) -> bool:
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
        semester: Optional[str] = None,
        force: bool = False,
        minify: bool = False,
        project_name: str = "registrar-monitor",
        branch: Optional[str] = None,
    ) -> bool:
        """Run the deploy command."""
        if self.debug:
            print("🔍 DEBUG MODE: Website generation/deployment")

        service = WebsiteService()

        # Step 1: Generate
        success = service.generate(semester_key=semester, force=force, minify=minify)
        if not success:
            return False

        # Step 2: Deploy if requested
        if deploy:
            return service.deploy(project_name=project_name, branch=branch)

        return True
