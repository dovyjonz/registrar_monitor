import asyncio
import os
import uuid
from datetime import datetime

import httpx

from ..config import get_config
from ..core import get_logger
from ..core.exceptions import FileProcessingError
from ..validation import validate_directory_exists
from ..website.config import semester_sort_key


class DataDownloader:
    """Downloads enrollment data from the university registrar."""

    def __init__(self, semester: str | None = None):
        self.config = get_config()
        self.logger = get_logger(__name__)
        semester_configs = self.config.get("semesters", {})
        if semester is None:
            candidates = [
                label
                for label, value in semester_configs.items()
                if isinstance(value, dict) and value.get("registrar_url")
            ]
            if not candidates:
                raise ValueError("no semester registrar source is configured")
            semester = max(candidates, key=semester_sort_key)
        semester_config = semester_configs.get(semester, {})
        registrar_url = (
            semester_config.get("registrar_url")
            if isinstance(semester_config, dict)
            else None
        )
        if not isinstance(registrar_url, str) or not registrar_url.strip():
            raise ValueError(f"no registrar source configured for {semester!r}")
        self.semester = semester
        self.url = registrar_url.strip()
        self.raw_xls_directory = self.config["directories"]["raw_downloads"]

    @staticmethod
    def _write_file(filename: str, content: bytes) -> None:
        """Writes content to a file synchronously."""
        with open(filename, "wb") as f:
            f.write(content)

    def _tls_verification(self) -> bool | str:
        """Return httpx's ``verify=`` value; verification is never disabled."""
        ca_bundle = self.config["data_source"].get("ca_bundle")
        if not ca_bundle:
            return True
        if not os.path.isfile(ca_bundle):
            raise FileProcessingError(
                f"data_source.ca_bundle {ca_bundle} does not exist; "
                "run scripts/fetch_registrar_ca.sh on the host"
            )
        return ca_bundle

    async def download(self) -> str | None:
        """
        Download the enrollment data file.

        Returns:
            Optional[str]: The path to the downloaded file, or None if download fails.
        """
        validate_directory_exists(self.raw_xls_directory, create_if_missing=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Add UUID to ensure uniqueness and prevent race conditions
        unique_id = uuid.uuid4().hex[:8]
        filename = os.path.join(
            self.raw_xls_directory,
            f"school_schedule_by_term_{timestamp}_{unique_id}.xls",
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
