"""
Monitoring service for coordinating data collection and processing workflows.

This service encapsulates the business logic for monitoring enrollment data,
providing a clean interface that reduces coupling in the main module.
"""

from pathlib import Path

from ..automation.downloader import DataDownloader
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

    def __init__(self, semester: str | None = None):
        """
        Initialize the monitoring service.

        Args:
            semester: Optional semester identifier for database selection
        """
        self.semester = semester
        self.logger = get_logger(__name__)

        # Initialize components
        self.downloader = DataDownloader(semester=semester)
        self.excel_reader = ExcelReader()
        self.snapshot_processor = SnapshotProcessor()
        self.db_manager = DatabaseManager(semester=semester)

        self.logger.info(
            f"Monitoring service initialized for semester: {semester or 'default'}"
        )

    async def download_and_process_latest(
        self,
    ) -> tuple[bool, EnrollmentSnapshot | None, str | None]:
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
    ) -> tuple[bool, EnrollmentSnapshot | None]:
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

    def get_latest_snapshot(self) -> EnrollmentSnapshot | None:
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
    ) -> tuple[EnrollmentSnapshot | None, EnrollmentSnapshot | None]:
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

            previous_id = self.db_manager.get_previous_snapshot_id(latest_id)
            previous_snapshot = (
                self.db_manager.get_snapshot_data(previous_id)
                if previous_id is not None
                else None
            )

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

    def get_database_stats(self) -> dict:
        """
        Get statistics about the database contents.

        Returns:
            Dictionary with database statistics
        """
        try:
            stats = self.db_manager.get_database_stats()
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

    async def _download_data(self) -> str | None:
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

    def _process_file(self, file_path: str) -> EnrollmentSnapshot | None:
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
            semester, timestamp, data = self.excel_reader.read_excel_data(file_path)
            if self.semester is not None and semester.strip() != self.semester:
                raise FileProcessingError(
                    f"registrar response semester {semester!r} does not match "
                    f"configured source semester {self.semester!r}"
                )
            if not data:
                raise FileProcessingError("Excel file is empty or invalid")

            # Process into snapshot
            snapshot = self.snapshot_processor.process_data(data, semester, timestamp)

            # Save snapshot to database
            self.snapshot_processor.save_snapshot(snapshot)
            self.logger.info("Saved snapshot to database")

            return snapshot

        except Exception as e:
            self.logger.error(f"Failed to process file {file_path}: {e}")
            raise FileProcessingError(f"Failed to process file: {e}") from e
