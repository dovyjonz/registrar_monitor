"""
Monitoring service for coordinating data collection and processing workflows.

This service encapsulates the business logic for monitoring enrollment data,
providing a clean interface that reduces coupling in the main module.
"""

from pathlib import Path
from typing import Optional, Tuple

from ..automation.downloader import DataDownloader
from ..automation.scheduler import HybridScheduler
from ..core import get_logger
from ..core.exceptions import FileProcessingError
from ..data.database_manager import DatabaseManager
from ..data.excel_reader import ExcelReader
from ..data.snapshot_processor import SnapshotProcessor
from ..models import EnrollmentSnapshot


class MonitoringService:
    """
    Service for coordinating enrollment monitoring workflows.

    This service provides high-level operations for downloading, processing,
    and storing enrollment data while maintaining separation of concerns.
    """

    def __init__(self, semester: Optional[str] = None):
        """
        Initialize the monitoring service.

        Args:
            semester: Optional semester identifier for database selection
        """
        self.semester = semester
        self.logger = get_logger(__name__)

        # Initialize components
        self.downloader = DataDownloader()
        self.excel_reader = ExcelReader()
        self.snapshot_processor = SnapshotProcessor()
        self.db_manager = DatabaseManager(semester=semester)

        self.logger.info(
            f"Monitoring service initialized for semester: {semester or 'default'}"
        )

    async def download_and_process_latest(
        self,
    ) -> Tuple[bool, Optional[EnrollmentSnapshot], Optional[str]]:
        """
        Download and process the latest enrollment data.

        Returns:
            Tuple of (success, snapshot, file_path) where:
            - success indicates if operation completed
            - snapshot contains the processed data if successful
            - file_path contains the path to the downloaded file
        """
        self.logger.info("Starting download and process workflow")

        try:
            # Step 1: Download latest data
            downloaded_file = await self._download_data()
            if not downloaded_file:
                return False, None, None

            # Step 2: Process the downloaded file
            snapshot = self._process_file(downloaded_file)
            if not snapshot:
                return False, None, downloaded_file

            self.logger.info(
                f"Successfully processed data into snapshot with {len(snapshot.courses)} courses"
            )
            return True, snapshot, downloaded_file

        except Exception as e:
            self.logger.error(f"Failed to download and process data: {e}")
            return False, None, None

    def process_specific_file(
        self, file_path: str
    ) -> Tuple[bool, Optional[EnrollmentSnapshot]]:
        """
        Process a specific Excel file.

        Args:
            file_path: Path to the Excel file to process

        Returns:
            Tuple of (success, snapshot)
        """
        self.logger.info(f"Processing specific file: {file_path}")

        try:
            if not Path(file_path).exists():
                raise FileProcessingError(f"File not found: {file_path}")

            snapshot = self._process_file(file_path)
            if snapshot:
                self.logger.info(
                    f"Successfully processed file with {len(snapshot.courses)} courses"
                )
                return True, snapshot
            else:
                return False, None

        except Exception as e:
            self.logger.error(f"Failed to process file {file_path}: {e}")
            return False, None

    def get_latest_snapshot(self) -> Optional[EnrollmentSnapshot]:
        """
        Get the latest enrollment snapshot from the database.

        Returns:
            Latest snapshot or None if no snapshots exist
        """
        try:
            snapshot_id = self.db_manager.get_latest_snapshot_id()
            if not snapshot_id:
                self.logger.info("No snapshots found in database")
                return None

            snapshot = self.db_manager.get_snapshot_data(snapshot_id)
            if snapshot:
                self.logger.info(
                    f"Retrieved latest snapshot with {len(snapshot.courses)} courses"
                )

            return snapshot

        except Exception as e:
            self.logger.error(f"Failed to get latest snapshot: {e}")
            return None

    def get_snapshot_comparison(
        self,
    ) -> Tuple[Optional[EnrollmentSnapshot], Optional[EnrollmentSnapshot]]:
        """
        Get the current and previous snapshots for comparison.

        Returns:
            Tuple of (current_snapshot, previous_snapshot)
        """
        try:
            # Get latest snapshot
            latest_id = self.db_manager.get_latest_snapshot_id()
            if not latest_id:
                self.logger.info("No snapshots available for comparison")
                return None, None

            current_snapshot = self.db_manager.get_snapshot_data(latest_id)

            # Get previous snapshot (second most recent)
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()
                result = cursor.execute(
                    "SELECT snapshot_id FROM snapshots WHERE snapshot_id != ? ORDER BY timestamp DESC LIMIT 1",
                    (latest_id,),
                ).fetchone()

                if result:
                    previous_snapshot = self.db_manager.get_snapshot_data(result[0])
                else:
                    previous_snapshot = None

            self.logger.info(
                f"Retrieved comparison snapshots - Current: {bool(current_snapshot)}, Previous: {bool(previous_snapshot)}"
            )
            return current_snapshot, previous_snapshot

        except Exception as e:
            self.logger.error(f"Failed to get snapshots for comparison: {e}")
            return None, None

    def cleanup_old_data(self, keep_count: int = 50) -> int:
        """
        Clean up old snapshots from the database.

        Args:
            keep_count: Number of recent snapshots to keep

        Returns:
            Number of snapshots deleted
        """
        try:
            deleted_count = self.db_manager.cleanup_old_snapshots(keep_count)
            if deleted_count > 0:
                self.logger.info(f"Cleaned up {deleted_count} old snapshots")
            else:
                self.logger.info("No old snapshots to clean up")

            return deleted_count

        except Exception as e:
            self.logger.error(f"Failed to cleanup old data: {e}")
            return 0

    def start_scheduler(self) -> None:
        """
        Start the hybrid scheduler for automated monitoring.
        """
        try:
            self.logger.info("Starting hybrid scheduler")
            scheduler = HybridScheduler()
            scheduler.start()

        except Exception as e:
            self.logger.error(f"Failed to start scheduler: {e}")
            raise

    def get_database_stats(self) -> dict:
        """
        Get statistics about the database contents.

        Returns:
            Dictionary with database statistics
        """
        try:
            with self.db_manager.get_connection() as conn:
                cursor = conn.cursor()

                # Count snapshots
                snapshot_count = cursor.execute(
                    "SELECT COUNT(*) FROM snapshots"
                ).fetchone()[0]

                # Count courses
                course_count = cursor.execute(
                    "SELECT COUNT(*) FROM courses"
                ).fetchone()[0]

                # Count sections
                section_count = cursor.execute(
                    "SELECT COUNT(*) FROM sections"
                ).fetchone()[0]

                # Get date range
                date_range = cursor.execute(
                    "SELECT MIN(timestamp), MAX(timestamp) FROM snapshots"
                ).fetchone()

                stats = {
                    "snapshots": snapshot_count,
                    "courses": course_count,
                    "sections": section_count,
                    "earliest_snapshot": date_range[0],
                    "latest_snapshot": date_range[1],
                }

                self.logger.debug(f"Database stats: {stats}")
                return stats

        except Exception as e:
            self.logger.error(f"Failed to get database stats: {e}")
            return {}

    def get_course_history(self, course_code: str) -> list[dict]:
        """
        Get historical enrollment data for a specific course.

        Args:
            course_code: Course code to query

        Returns:
            List of dictionaries containing timestamp and section info
        """
        try:
            return self.db_manager.get_course_history(course_code, self.semester)
        except Exception as e:
            self.logger.error(f"Failed to get course history for {course_code}: {e}")
            return []

    async def _download_data(self) -> Optional[str]:
        """
        Download the latest enrollment data.

        Returns:
            Path to downloaded file or None if failed
        """
        try:
            self.logger.info("Downloading latest enrollment data")
            downloaded_file = await self.downloader.download()

            if downloaded_file:
                self.logger.info(f"Successfully downloaded: {downloaded_file}")
                return downloaded_file
            else:
                raise FileProcessingError("Download failed - no file returned")

        except Exception as e:
            self.logger.error(f"Download failed: {e}")
            raise FileProcessingError(f"Download failed: {e}") from e

    def _process_file(self, file_path: str) -> Optional[EnrollmentSnapshot]:
        """
        Process an Excel file into an enrollment snapshot.

        Args:
            file_path: Path to the Excel file

        Returns:
            Processed enrollment snapshot or None if failed
        """
        try:
            self.logger.info(f"Processing file: {file_path}")

            # Read Excel file
            semester, timestamp, df = self.excel_reader.read_excel_data(file_path)
            if df is None or df.empty:
                raise FileProcessingError("Excel file is empty or invalid")

            # Process into snapshot
            snapshot = self.snapshot_processor.process_data(df, semester, timestamp)

            # Save snapshot
            json_path = self.snapshot_processor.save_snapshot(snapshot)
            self.logger.info(f"Saved snapshot to: {json_path}")

            return snapshot

        except Exception as e:
            self.logger.error(f"Failed to process file {file_path}: {e}")
            raise FileProcessingError(f"Failed to process file: {e}") from e

    def _extract_semester_from_filename(self, file_path: str) -> str:
        """
        Extract semester information from filename.

        Args:
            file_path: Path to the file

        Returns:
            Extracted semester or default value
        """
        filename = Path(file_path).stem.lower()

        # Common semester patterns
        if "fall" in filename or "autumn" in filename:
            if "2024" in filename:
                return "Fall 2024"
            elif "2025" in filename:
                return "Fall 2025"
        elif "spring" in filename:
            if "2024" in filename:
                return "Spring 2024"
            elif "2025" in filename:
                return "Spring 2025"
        elif "summer" in filename:
            if "2024" in filename:
                return "Summer 2024"
            elif "2025" in filename:
                return "Summer 2025"

        # Default fallback
        import datetime

        current_year = datetime.datetime.now().year
        current_month = datetime.datetime.now().month

        if current_month <= 5:
            return f"Spring {current_year}"
        elif current_month <= 8:
            return f"Summer {current_year}"
        else:
            return f"Fall {current_year}"
