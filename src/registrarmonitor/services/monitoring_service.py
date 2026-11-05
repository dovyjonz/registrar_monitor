"""
Monitoring service for coordinating data collection and processing workflows.

This service encapsulates the business logic for monitoring enrollment data,
providing a clean interface that reduces coupling in the main module.
"""

import asyncio
import hashlib
import json
from pathlib import Path
from typing import Any, NamedTuple

from ..automation.downloader import DataDownloader
from ..core import get_logger
from ..core.exceptions import FileProcessingError
from ..data.checkpointed_state import WriteResult
from ..data.database_manager import DatabaseManager
from ..data.excel_reader import ExcelReader
from ..data.snapshot_processor import SnapshotProcessor
from ..heartbeat import send_heartbeat
from ..models import EnrollmentSnapshot


class ProcessedFile(NamedTuple):
    """Outcome of processing one registrar file."""

    success: bool
    snapshot: EnrollmentSnapshot | None = None
    digest: str | None = None
    # The parsed rows match ``previous_digest``: nothing was stored.
    unchanged: bool = False


def rows_digest(semester: str, rows: list[dict[str, Any]]) -> str:
    """Hash parsed rows independent of row order and of the sheet timestamp."""
    digest = hashlib.sha256(semester.strip().encode())
    for line in sorted(json.dumps(row, sort_keys=True, default=str) for row in rows):
        digest.update(b"\n")
        digest.update(line.encode())
    return digest.hexdigest()


class MonitoringService:
    """
    Service for coordinating enrollment monitoring workflows.

    This service provides high-level operations for downloading, processing,
    and storing enrollment data while maintaining separation of concerns.
    """

    def __init__(
        self,
        semester: str | None = None,
        *,
        db_manager: DatabaseManager | None = None,
    ):
        """
        Initialize the monitoring service.

        Args:
            semester: Optional semester identifier for database selection
            db_manager: Optional manager for ``semester`` to reuse
        """
        self.semester = semester
        self.logger = get_logger(__name__)

        # Initialize components
        self.downloader = DataDownloader(semester=semester)
        self.excel_reader = ExcelReader()
        self.snapshot_processor = SnapshotProcessor()
        self.db_manager = db_manager or DatabaseManager(semester=semester)
        self._other_dbs: dict[str, DatabaseManager] = {}

        self.logger.info(
            f"Monitoring service initialized for semester: {semester or 'default'}"
        )

    def db_for(self, semester: str) -> DatabaseManager:
        """Return the manager for ``semester``, opening other semesters once."""
        if semester == self.semester:
            return self.db_manager
        if semester not in self._other_dbs:
            self._other_dbs[semester] = DatabaseManager.create_for_semester(semester)
        return self._other_dbs[semester]

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
            processed = await asyncio.to_thread(self._process_file, downloaded_file)
            snapshot = processed.snapshot
            if not snapshot:
                return False, None, downloaded_file

            self.logger.info(
                f"Successfully processed data into snapshot with {len(snapshot.courses)} courses"
            )
            await send_heartbeat()
            return True, snapshot, downloaded_file

        except Exception as e:
            self.logger.error(f"Failed to download and process data: {e}")
            return False, None, None

    async def process_specific_file(
        self, file_path: str, previous_digest: str | None = None
    ) -> ProcessedFile:
        """
        Process a specific Excel file off the event loop.

        Args:
            file_path: Path to the Excel file to process
            previous_digest: Digest of the last processed rows; when the parsed
                rows hash to it, nothing is stored and ``unchanged`` is set

        Returns:
            The outcome, with the digest of the parsed rows on success
        """
        self.logger.info(f"Processing specific file: {file_path}")

        try:
            if not Path(file_path).exists():
                raise FileProcessingError(f"File not found: {file_path}")

            processed = await asyncio.to_thread(
                self._process_file, file_path, previous_digest
            )
            if processed.unchanged:
                self.logger.info("Parsed rows unchanged; nothing stored")
            elif processed.snapshot:
                self.logger.info(
                    f"Successfully processed file with {len(processed.snapshot.courses)} courses"
                )
            return processed

        except Exception as e:
            self.logger.error(f"Failed to process file {file_path}: {e}")
            return ProcessedFile(success=False)

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

    def _process_file(
        self, file_path: str, previous_digest: str | None = None
    ) -> ProcessedFile:
        """
        Parse an Excel file and store it as an enrollment snapshot.

        Runs in a worker thread: it parses the workbook and writes SQLite.

        Args:
            file_path: Path to the Excel file
            previous_digest: Digest of the last processed rows, if known

        Returns:
            The processed file, or an unchanged marker when the rows match
            ``previous_digest``
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

            digest = rows_digest(semester, data)
            if digest == previous_digest:
                return ProcessedFile(success=True, digest=digest, unchanged=True)

            # Process into snapshot
            snapshot = self.snapshot_processor.process_data(data, semester, timestamp)

            # Save snapshot to database
            result: WriteResult = self.snapshot_processor.save_snapshot(
                snapshot, self.db_for(snapshot.semester)
            )
            self.logger.info(
                "Saved snapshot to database"
                if result.created
                else "Snapshot matches the stored state; advanced last-seen"
            )

            return ProcessedFile(success=True, snapshot=snapshot, digest=digest)

        except Exception as e:
            self.logger.error(f"Failed to process file {file_path}: {e}")
            raise FileProcessingError(f"Failed to process file: {e}") from e
