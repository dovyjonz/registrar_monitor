"""
Reporting service for coordinating report generation and delivery workflows.

This service encapsulates the business logic for generating and sending reports,
providing a clean interface that reduces coupling in the main module.
"""

import asyncio
from functools import partial
from pathlib import Path
from typing import List, Optional, Tuple

from ..core import get_logger
from ..core.exceptions import NotificationError, ReportGenerationError
from ..data.database_manager import DatabaseManager
from ..data.snapshot_comparator import SnapshotComparator
from ..models import EnrollmentSnapshot
from ..reporting.pdf_generator import PDFGenerator
from ..reporting.report_formatter import ReportFormatter
from ..reporting.telegram_reporter import TelegramReporter
from ..utils import construct_output_path


class ReportingService:
    """
    Service for coordinating report generation and delivery workflows.

    This service provides high-level operations for generating PDF and text reports
    and sending them via various channels while maintaining separation of concerns.
    """

    def __init__(self, semester: Optional[str] = None):
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
        self.pdf_generator = PDFGenerator()
        self.report_formatter = ReportFormatter()
        self.telegram_reporter = TelegramReporter()

        self.logger.info(
            f"Reporting service initialized for semester: {semester or 'default'}"
        )

    async def generate_and_send_reports(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: Optional[EnrollmentSnapshot] = None,
        send_telegram: bool = True,
        debug_mode: bool = False,
    ) -> Tuple[bool, List[str]]:
        """
        Generate reports and optionally send them via Telegram.

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

        generated_files = []

        try:
            # Generate PDF report
            pdf_path = await self._generate_pdf_report(
                current_snapshot, previous_snapshot
            )
            if pdf_path:
                generated_files.append(pdf_path)

            # Generate text report if we have a previous snapshot for comparison
            txt_path = None
            if previous_snapshot:
                txt_path = await self._generate_text_report(
                    current_snapshot, previous_snapshot
                )
                if txt_path:
                    generated_files.append(txt_path)

            # Send reports if not in debug mode
            if send_telegram and not debug_mode:
                await self._send_reports_via_telegram(pdf_path, txt_path)

            self.logger.info(f"Successfully generated {len(generated_files)} reports")
            return True, generated_files

        except Exception as e:
            self.logger.error(f"Failed to generate/send reports: {e}")
            raise ReportGenerationError(f"Report generation failed: {e}") from e

    async def generate_pdf_report_only(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: Optional[EnrollmentSnapshot] = None,
        custom_filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate only a PDF report without sending it.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous snapshot for comparison
            custom_filename: Custom filename for the PDF

        Returns:
            Path to generated PDF file or None if failed
        """
        try:
            return await self._generate_pdf_report(
                current_snapshot, previous_snapshot, custom_filename
            )
        except Exception as e:
            self.logger.error(f"Failed to generate PDF report: {e}")
            return None

    async def generate_comparison_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: EnrollmentSnapshot,
        send_telegram: bool = False,
    ) -> Tuple[bool, Optional[str]]:
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
                await self.telegram_reporter.send_text_report(txt_path)

            return True, txt_path

        except Exception as e:
            self.logger.error(f"Failed to generate comparison report: {e}")
            return False, None

    async def run_stateful_report_cycle(self, debug_mode: bool = False) -> bool:
        """
        Run a stateful reporting cycle (only report if changes detected).

        This method replaces the old StatefulReporter.

        Args:
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

        # Handle the first-run scenario
        if not last_reported_id:
            self.logger.info(
                "First run detected. Logging the latest snapshot as the baseline."
            )
            self.db_manager.add_reporting_log(
                snapshot_id=latest_snapshot_id, changes_were_found=False
            )
            self.logger.info(
                "Baseline set. Next run will compare against this snapshot."
            )
            return False

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

        # Determine if there are significant changes
        changes_found = bool(
            comparison.new_courses
            or comparison.removed_courses
            or comparison.changed_courses
        )

        if changes_found:
            self.logger.info("Changes found! Generating and sending reports...")

            # Use the main generation method
            success, _ = await self.generate_and_send_reports(
                current_snapshot=current_snapshot,
                previous_snapshot=previous_snapshot,
                send_telegram=True,
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
        if not debug_mode:
            self.db_manager.add_reporting_log(
                snapshot_id=latest_snapshot_id, changes_were_found=changes_found
            )
            self.logger.info(
                f"Successfully logged snapshot {latest_snapshot_id} as reported"
            )
        else:
            self.logger.info(
                f"DEBUG: Would log snapshot {latest_snapshot_id} as reported"
            )

        return changes_found

    async def send_existing_reports(
        self, pdf_path: Optional[str] = None, txt_path: Optional[str] = None
    ) -> bool:
        """
        Send existing report files via Telegram.

        Args:
            pdf_path: Path to PDF report file
            txt_path: Path to text report file

        Returns:
            True if successful, False otherwise
        """
        try:
            await self._send_reports_via_telegram(pdf_path, txt_path)
            return True
        except Exception as e:
            self.logger.error(f"Failed to send existing reports: {e}")
            return False

    def get_available_reports(self, limit: int = 10) -> List[dict]:
        """
        Get a list of recently generated reports.

        Args:
            limit: Maximum number of reports to return

        Returns:
            List of report information dictionaries
        """
        try:
            from ..config import get_config

            config = get_config()
            pdf_dir = Path(config["directories"]["pdf_output"])
            txt_dir = Path(config["directories"]["text_reports"])

            reports = []

            # Get PDF reports
            if pdf_dir.exists():
                for pdf_file in sorted(
                    pdf_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True
                )[:limit]:
                    reports.append(
                        {
                            "type": "PDF",
                            "path": str(pdf_file),
                            "filename": pdf_file.name,
                            "size": pdf_file.stat().st_size,
                            "modified": pdf_file.stat().st_mtime,
                        }
                    )

            # Get text reports
            if txt_dir.exists():
                for txt_file in sorted(
                    txt_dir.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True
                )[:limit]:
                    reports.append(
                        {
                            "type": "Text",
                            "path": str(txt_file),
                            "filename": txt_file.name,
                            "size": txt_file.stat().st_size,
                            "modified": txt_file.stat().st_mtime,
                        }
                    )

            # Sort by modification time
            reports.sort(key=lambda x: x["modified"], reverse=True)  # type: ignore
            return reports[:limit]

        except Exception as e:
            self.logger.error(f"Failed to get available reports: {e}")
            return []

    async def _generate_pdf_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: Optional[EnrollmentSnapshot] = None,
        custom_filename: Optional[str] = None,
    ) -> Optional[str]:
        """
        Generate a PDF report.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous snapshot for comparison
            custom_filename: Custom filename for the PDF

        Returns:
            Path to generated PDF file
        """
        try:
            from ..config import get_config

            config = get_config()

            if custom_filename:
                pdf_path = custom_filename
            else:
                pdf_path = construct_output_path(
                    config["directories"]["pdf_output"],
                    current_snapshot.semester,
                    current_snapshot.timestamp,
                    ".pdf",
                )

            self.logger.info(f"Generating PDF report: {pdf_path}")

            # Ensure directory exists
            Path(pdf_path).parent.mkdir(parents=True, exist_ok=True)

            # Generate the PDF in a thread pool to avoid blocking the event loop
            loop = asyncio.get_running_loop()
            result_path = await loop.run_in_executor(
                None,
                partial(
                    self.pdf_generator.generate_enrollment_report,
                    current_snapshot,
                    pdf_path,
                    previous_snapshot,
                ),
            )

            if result_path and Path(result_path).exists():
                file_size = Path(result_path).stat().st_size
                self.logger.info(
                    f"Successfully generated PDF report: {result_path} ({file_size} bytes)"
                )
                return result_path
            else:
                raise ReportGenerationError("PDF generation returned no file")

        except Exception as e:
            self.logger.error(f"Failed to generate PDF report: {e}")
            raise ReportGenerationError(f"PDF generation failed: {e}") from e

    async def _generate_text_report(
        self,
        current_snapshot: EnrollmentSnapshot,
        previous_snapshot: EnrollmentSnapshot,
    ) -> Optional[str]:
        """
        Generate a text comparison report.

        Args:
            current_snapshot: Current enrollment snapshot
            previous_snapshot: Previous enrollment snapshot

        Returns:
            Path to generated text file
        """
        try:
            from ..config import get_config

            config = get_config()

            # Compare snapshots
            comparison = self.snapshot_comparator.compare_snapshots(
                current_snapshot, previous_snapshot
            )

            # Generate text report
            text_report = self.report_formatter.format_changes_report(
                comparison, current_snapshot, previous_snapshot
            )

            # Construct output path
            txt_path = construct_output_path(
                config["directories"]["text_reports"],
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
        self, pdf_path: Optional[str], txt_path: Optional[str]
    ) -> None:
        """
        Send reports via Telegram.

        Args:
            pdf_path: Path to PDF report
            txt_path: Path to text report
        """
        try:
            self.logger.info("Sending reports via Telegram")

            if pdf_path and Path(pdf_path).exists():
                await self.telegram_reporter.send_pdf_report(pdf_path)
                self.logger.info("PDF report sent successfully")

            if txt_path and Path(txt_path).exists():
                await self.telegram_reporter.send_text_report(txt_path)
                self.logger.info("Text report sent successfully")

            if not pdf_path and not txt_path:
                self.logger.warning("No reports to send via Telegram")

        except Exception as e:
            self.logger.error(f"Failed to send reports via Telegram: {e}")
            raise NotificationError(f"Telegram sending failed: {e}") from e

    def cleanup_old_reports(self, keep_count: int = 20) -> Tuple[int, int]:
        """
        Clean up old report files.

        Args:
            keep_count: Number of recent reports to keep for each type

        Returns:
            Tuple of (pdf_deleted_count, txt_deleted_count)
        """
        try:
            from ..config import get_config

            config = get_config()
            pdf_dir = Path(config["directories"]["pdf_output"])
            txt_dir = Path(config["directories"]["text_reports"])

            pdf_deleted = 0
            txt_deleted = 0

            # Clean up PDF files
            if pdf_dir.exists():
                pdf_files = sorted(
                    pdf_dir.glob("*.pdf"), key=lambda x: x.stat().st_mtime, reverse=True
                )
                for old_file in pdf_files[keep_count:]:
                    old_file.unlink()
                    pdf_deleted += 1

            # Clean up text files
            if txt_dir.exists():
                txt_files = sorted(
                    txt_dir.glob("*.txt"), key=lambda x: x.stat().st_mtime, reverse=True
                )
                for old_file in txt_files[keep_count:]:
                    old_file.unlink()
                    txt_deleted += 1

            if pdf_deleted or txt_deleted:
                self.logger.info(
                    f"Cleaned up {pdf_deleted} PDF files and {txt_deleted} text files"
                )

            return pdf_deleted, txt_deleted

        except Exception as e:
            self.logger.error(f"Failed to cleanup old reports: {e}")
            return 0, 0
