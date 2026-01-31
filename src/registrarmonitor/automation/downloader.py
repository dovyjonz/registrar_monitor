import asyncio
import os
import uuid
from datetime import datetime
from typing import Optional

import httpx

from ..config import get_config
from ..core import get_logger
from ..core.exceptions import FileProcessingError
from ..validation import validate_directory_exists


class DataDownloader:
    """Downloads enrollment data from the university registrar."""

    def __init__(self):
        self.config = get_config()
        self.logger = get_logger(__name__)
        self.url = self.config["data_source"]["url"]
        self.raw_xls_directory = self.config["directories"]["raw_downloads"]

    @staticmethod
    def _write_file(filename: str, content: bytes) -> None:
        """Writes content to a file synchronously."""
        with open(filename, "wb") as f:
            f.write(content)

    async def download(self) -> Optional[str]:
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
            async with httpx.AsyncClient(verify=False) as client:
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


if __name__ == "__main__":
    import asyncio

    downloader = DataDownloader()
    asyncio.run(downloader.download())
