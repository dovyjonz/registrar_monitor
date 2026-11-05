import asyncio
import os
import uuid
from datetime import datetime

import httpx

from ..config import get_settings
from ..core import get_logger
from ..core.exceptions import FileProcessingError
from ..validation import validate_directory_exists
from ..website.config import semester_sort_key

DOWNLOAD_PREFIX = "school_schedule_by_term_"
# Newest downloads to keep. More than one so a download still queued for
# parsing is not pruned by the next burst-rate download.
KEEP_DOWNLOADS = 5


class DataDownloader:
    """Downloads enrollment data from the university registrar."""

    def __init__(self, semester: str | None = None):
        self.settings = get_settings()
        self.logger = get_logger(__name__)
        semester_configs = self.settings.semesters
        if semester is None:
            candidates = [
                label
                for label, value in semester_configs.items()
                if value.registrar_url
            ]
            if not candidates:
                raise ValueError("no semester registrar source is configured")
            semester = max(candidates, key=semester_sort_key)
        semester_config = semester_configs.get(semester)
        registrar_url = semester_config.registrar_url if semester_config else None
        if not registrar_url or not registrar_url.strip():
            raise ValueError(f"no registrar source configured for {semester!r}")
        self.semester = semester
        self.url = registrar_url.strip()
        self.raw_xls_directory = self.settings.directories.raw_downloads

    @staticmethod
    def _write_file(filename: str, content: bytes) -> None:
        """Write a download, then drop all but the newest few.

        The newest files stay for debugging a bad parse; the rest would fill the
        disk at the 10-second burst rate (about 280 MB an hour).
        """
        with open(filename, "wb") as f:
            f.write(content)
        directory = os.path.dirname(filename)
        files = sorted(
            (
                entry
                for entry in os.scandir(directory)
                if entry.name.startswith(DOWNLOAD_PREFIX)
                and entry.name.endswith(".xls")
            ),
            key=lambda entry: entry.stat().st_mtime,
            reverse=True,
        )
        for old in files[KEEP_DOWNLOADS:]:
            try:
                os.unlink(old.path)
            except OSError:
                pass

    def _tls_verification(self) -> bool | str:
        """Return httpx's ``verify=`` value; verification is never disabled."""
        ca_bundle = self.settings.data_source.ca_bundle
        if not ca_bundle:
            return True
        if not os.path.isfile(ca_bundle):
            raise FileProcessingError(
                f"data_source.ca_bundle {ca_bundle} does not exist; "
                "run scripts/fetch_registrar_ca.sh on the host"
            )
        return ca_bundle

    async def download(self) -> str:
        """
        Download the enrollment data file and return its path.

        The second row of the sheet is the registrar's generation time
        (``ExcelReader`` reads it as the snapshot timestamp), so the bytes can
        differ per response while the data does not. Unchanged data is detected
        after parsing (see ``MonitoringService``), not here. Failures raise
        ``FileProcessingError``.
        """
        validate_directory_exists(self.raw_xls_directory, create_if_missing=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Add UUID to ensure uniqueness and prevent race conditions
        unique_id = uuid.uuid4().hex[:8]
        filename = os.path.join(
            self.raw_xls_directory,
            f"{DOWNLOAD_PREFIX}{timestamp}_{unique_id}.xls",
        )

        try:
            verify = self._tls_verification()
            async with httpx.AsyncClient(verify=verify) as client:
                print(f"Downloading file from {self.url}...")
                response = await client.get(self.url, timeout=30.0)
                response.raise_for_status()

                # Offload blocking I/O to a separate thread
                await asyncio.to_thread(self._write_file, filename, response.content)

                print(f"File downloaded successfully as {filename}")
                self.logger.info(f"Successfully downloaded file: {filename}")
                return filename

        except httpx.TimeoutException as e:
            self.logger.error(f"Download timeout: {e}")
            raise FileProcessingError(f"Download timeout: {e}") from e
        except httpx.NetworkError as e:
            self.logger.error(f"Connection error: {e}")
            raise FileProcessingError(f"Connection error: {e}") from e
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            self.logger.error(f"HTTP error {status_code}: {e}")
            raise FileProcessingError(f"HTTP error: {e}") from e
        except httpx.RequestError as e:
            self.logger.error(f"Request error: {e}")
            raise FileProcessingError(f"Request error: {e}") from e
        except Exception as e:
            self.logger.error(f"Unexpected error during download: {e}")
            raise FileProcessingError(f"Unexpected download error: {e}") from e
