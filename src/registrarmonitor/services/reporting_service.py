"""
Reporting service for coordinating report generation and delivery workflows.

This service encapsulates the business logic for generating and sending reports,
providing a clean interface that reduces coupling in the main module.
"""

from pathlib import Path

from ..core import get_logger
from ..core.exceptions import NotificationError, ReportGenerationError
from ..data.database_manager import DatabaseManager
from ..data.snapshot_comparator import SnapshotComparator
from ..models import EnrollmentSnapshot
from ..reporting.report_formatter import ReportFormatter
from ..reporting.telegram_reporter import TelegramReporter
from ..subscriptions.publication import ReportPublication
from ..subscriptions.store import SubscriptionStore
from ..utils import construct_output_path


class ReportingService:
    """
    Service for coordinating report generation and delivery workflows.

    This service provides high-level operations for generating text reports
    and sending them via Telegram.
    """

    def __init__(
        self,
        semester: str | None = None,
        *,
        subscription_store: SubscriptionStore | None = None,
    ):
        """
        Initialize the reporting service.

        Args:
            semester: Optional semester identifier for database selection
        """
        self.semester = semester
        self.logger = get_logger(__name__)

        # Initialize components
        self.db_manager = DatabaseManager(semester=semester)
        self.snapshot_comparator = SnapshotComparator()
        self.report_formatter = ReportFormatter()
        self._telegram_reporter: TelegramReporter | None = None
        self._subscription_store = subscription_store

        self.logger.info(
            f"Reporting service initialized for semester: {semester or 'default'}"
        )

    @property
    def telegram_reporter(self) -> TelegramReporter:
        """Lazy-load Telegram only when a report is actually sent."""
        if self._telegram_reporter is None:
            self._telegram_reporter = TelegramReporter()
        return self._telegram_reporter

    @property
    def subscription_store(self) -> SubscriptionStore:
        """Lazy-load the bot database only for a live Telegram report."""
        if self._subscription_store is None:
            from ..config import get_settings

            path = get_settings().directories.telegram_bot_state
            self._subscription_store = SubscriptionStore(path)
        return self._subscription_store

    async def generate_and_send_reports(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: EnrollmentSnapshot | None = None,
        send_telegram: bool = True,
        debug_mode: bool = False,
    ) -> tuple[bool, list[str]]:
        """
        Generate text reports and optionally send them via Telegram.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous snapshot for comparison (optional)
            send_telegram: Whether to send reports via Telegram
            debug_mode: If True, generates reports but doesn't send them

        Returns:
            Tuple of (success, list of generated file paths)
        """
        self.logger.info(
            f"Starting report generation - Telegram: {send_telegram}, Debug: {debug_mode}"
        )

        generated_files: list[str] = []

        try:
            # Generate text report if we have a previous snapshot for comparison
            if previous_snapshot:
                txt_path = await self._generate_text_report(
                    current_snapshot, previous_snapshot
                )
                if txt_path:
                    generated_files.append(txt_path)

            # Send reports if not in debug mode
            if send_telegram and not debug_mode and generated_files:
                txt_path = generated_files[0] if generated_files else None
                await self._send_reports_via_telegram(
                    txt_path, snapshot=current_snapshot
                )

            self.logger.info(f"Successfully generated {len(generated_files)} reports")
            return True, generated_files

        except Exception as e:
            self.logger.error(f"Failed to generate/send reports: {e}")
            raise ReportGenerationError(f"Report generation failed: {e}") from e

    async def generate_comparison_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: EnrollmentSnapshot,
        send_telegram: bool = False,
    ) -> tuple[bool, str | None]:
        """
        Generate a detailed comparison report between two snapshots.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous enrollment snapshot
            send_telegram: Whether to send the report via Telegram

        Returns:
            Tuple of (success, text_report_path)
        """
        self.logger.info("Generating comparison report")

        try:
            txt_path = await self._generate_text_report(
                current_snapshot, previous_snapshot
            )

            if send_telegram and txt_path:
                await self.telegram_reporter.send_text_report(
                    txt_path, snapshot=current_snapshot
                )

            return True, txt_path

        except Exception as e:
            self.logger.error(f"Failed to generate comparison report: {e}")
            return False, None

    async def run_stateful_report_cycle(
        self, *, send_telegram: bool = True, debug_mode: bool = False
    ) -> bool:
        """
        Run a stateful reporting cycle (only report if changes detected).

        This method replaces the old StatefulReporter.

        Args:
            send_telegram: Whether to send generated reports via Telegram
            debug_mode: If True, does not send to Telegram

        Returns:
            bool: True if reports were generated and sent (or would be sent), False otherwise.
        """
        self.logger.info("Starting stateful reporter cycle")

        # Get the comparison points
        latest_snapshot_id = self.db_manager.get_latest_snapshot_id()
        last_reported_id = self.db_manager.get_last_reported_snapshot_id()

        # Exit if no snapshots exist in the database at all
        if not latest_snapshot_id:
            self.logger.info("No snapshots in the database. Nothing to do.")
            return False

        # Handle the first-run scenario. A single retained snapshot is only a
        # baseline; existing history must be compared so its changes are not
        # silently discarded.
        if not last_reported_id:
            first_snapshot_id = self.db_manager.get_first_snapshot_id()
            if first_snapshot_id is None or first_snapshot_id == latest_snapshot_id:
                self.logger.info(
                    "First run detected with one snapshot. Logging it as the baseline."
                )
                self.db_manager.add_reporting_log(
                    snapshot_id=latest_snapshot_id, changes_were_found=False
                )
                self.logger.info(
                    "Baseline set. Next run will compare against this snapshot."
                )
                return False
            self.logger.info(
                "First run detected with retained history. Comparing the latest "
                f"snapshot ({latest_snapshot_id}) against the first snapshot "
                f"({first_snapshot_id})."
            )
            last_reported_id = first_snapshot_id

        # Decide if a report is needed
        if latest_snapshot_id == last_reported_id:
            self.logger.info(
                f"Latest snapshot ({latest_snapshot_id}) has already been reported. No new data."
            )
            return False

        self.logger.info(
            f"Comparing latest snapshot ({latest_snapshot_id}) against last reported ({last_reported_id})"
        )

        # Fetch the actual data for comparison
        current_snapshot = self.db_manager.get_snapshot_data(latest_snapshot_id)
        previous_snapshot = self.db_manager.get_snapshot_data(last_reported_id)

        if not current_snapshot or not previous_snapshot:
            self.logger.error("Failed to fetch snapshot data for comparison")
            return False

        # Compare snapshots
        comparison = self.snapshot_comparator.compare_snapshots(
            current_snapshot, previous_snapshot
        )

        # Instructor-only changes belong to the website event history, not to
        # Telegram enrollment reports.
        changes_found = self.report_formatter.has_reportable_changes(comparison)

        if changes_found:
            self.logger.info("Changes found! Generating and sending reports...")

            if send_telegram and not debug_mode:

                async def send_channel() -> None:
                    success, _ = await self.generate_and_send_reports(
                        current_snapshot=current_snapshot,
                        previous_snapshot=previous_snapshot,
                        send_telegram=True,
                        debug_mode=False,
                    )
                    if not success:
                        raise ReportGenerationError("Channel report was not sent")

                await ReportPublication(
                    self.subscription_store, self.db_manager
                ).publish(
                    semester=current_snapshot.semester,
                    previous_snapshot_id=last_reported_id,
                    current_snapshot_id=latest_snapshot_id,
                    send_channel=send_channel,
                )
                success = True
            else:
                success, _ = await self.generate_and_send_reports(
                    current_snapshot=current_snapshot,
                    previous_snapshot=previous_snapshot,
                    send_telegram=send_telegram,
                    debug_mode=debug_mode,
                )

            if success:
                self.logger.info("Reports processed successfully")
            else:
                self.logger.error("Failed to generate or send reports")
                return False
        else:
            self.logger.info("No significant changes found between snapshots")

        # Log that this snapshot has now been reported
        # We log it even if no changes were found, so we don't check it again
        publication_logged = changes_found and send_telegram and not debug_mode
        if not debug_mode and not publication_logged:
            self.db_manager.add_reporting_log(
                snapshot_id=latest_snapshot_id, changes_were_found=changes_found
            )
            self.logger.info(
                f"Successfully logged snapshot {latest_snapshot_id} as reported"
            )
        elif debug_mode:
            self.logger.info(
                f"DEBUG: Would log snapshot {latest_snapshot_id} as reported"
            )

        return changes_found

    async def _generate_text_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: EnrollmentSnapshot,
    ) -> str | None:
        """
        Generate a text comparison report.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous enrollment snapshot

        Returns:
            Path to generated text file
        """
        try:
            from ..config import get_settings

            settings = get_settings()

            # Compare snapshots
            comparison = self.snapshot_comparator.compare_snapshots(
                current_snapshot, previous_snapshot
            )

            if not self.report_formatter.has_reportable_changes(comparison):
                self.logger.info(
                    "Skipping text report because there are no reportable "
                    "enrollment or capacity changes"
                )
                return None

            # Generate text report
            text_report = self.report_formatter.format_changes_report(
                comparison, current_snapshot, previous_snapshot
            )

            # Construct output path
            txt_path = construct_output_path(
                settings.directories.text_reports,
                current_snapshot.semester,
                current_snapshot.timestamp,
                ".txt",
            )

            self.logger.info(f"Generating text report: {txt_path}")

            # Ensure directory exists
            Path(txt_path).parent.mkdir(parents=True, exist_ok=True)

            # Write text report
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(text_report)

            file_size = Path(txt_path).stat().st_size
            self.logger.info(
                f"Successfully generated text report: {txt_path} ({file_size} bytes)"
            )
            return txt_path

        except Exception as e:
            self.logger.error(f"Failed to generate text report: {e}")
            raise ReportGenerationError(f"Text report generation failed: {e}") from e

    async def _send_reports_via_telegram(
        self, txt_path: str | None, snapshot: EnrollmentSnapshot | None = None
    ) -> None:
        """
        Send text reports via Telegram.

        Args:
            txt_path: Path to text report
            snapshot: Reported snapshot, used to link courses to one-tap watches
        """
        try:
            self.logger.info("Sending reports via Telegram")

            if txt_path and Path(txt_path).exists():
                await self.telegram_reporter.send_text_report(
                    txt_path, snapshot=snapshot
                )
                self.logger.info("Text report sent successfully")
            else:
                self.logger.warning("No reports to send via Telegram")

        except Exception as e:
            self.logger.error(f"Failed to send reports via Telegram: {e}")
            raise NotificationError(f"Telegram sending failed: {e}") from e

    def cleanup_old_reports(self, keep_count: int = 20) -> int:
        """
        Clean up old text report files.

        Args:
            keep_count: Number of recent reports to keep

        Returns:
            Number of text files deleted
        """
        try:
            from ..config import get_settings

            settings = get_settings()
            txt_dir = Path(settings.directories.text_reports)

            txt_deleted = 0

            # Clean up text files
            if txt_dir.exists():
                txt_files = sorted(
                    txt_dir.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True
                )
                for old_file in txt_files[keep_count:]:
                    old_file.unlink()
                    txt_deleted += 1

            if txt_deleted:
                self.logger.info(f"Cleaned up {txt_deleted} text files")

            return txt_deleted

        except Exception as e:
            self.logger.error(f"Failed to cleanup old reports: {e}")
            return 0
