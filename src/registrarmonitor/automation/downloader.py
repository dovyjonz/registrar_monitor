import asyncio
import hashlib
import json
import logging
import os
import uuid
from datetime import datetime

import httpx

from ..config import get_settings
from ..core.exceptions import FileProcessingError
from ..validation import validate_directory_exists
from ..website.config import semester_sort_key

logger = logging.getLogger(__name__)


class DataDownloader:
    """Downloads enrollment data from the university registrar."""

    def __init__(self, semester: str | None = None):
        self.settings = get_settings()
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
        self.state_path = os.path.join(
            self.settings.directories.data_storage, "download_state.json"
        )

    def _load_state(self) -> dict:
        try:
            with open(self.state_path, encoding="utf-8") as f:
                state = json.load(f)
            return state if isinstance(state, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save_state(self, entry: dict | None) -> None:
        """Persist (or with None, forget) this semester's last download."""
        state = self._load_state()
        if entry is None:
            state.pop(self.semester, None)
        else:
            state[self.semester] = entry
        os.makedirs(os.path.dirname(self.state_path), exist_ok=True)
        tmp_path = f"{self.state_path}.tmp"
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2)
        os.replace(tmp_path, self.state_path)

    def forget_last_download(self) -> None:
        """Make the next conditional download fetch and return the file again.

        Call when a downloaded file could not be processed.
        """
        self._save_state(None)

    @staticmethod
    def _write_file(filename: str, content: bytes) -> None:
        """Writes content to a file synchronously."""
        with open(filename, "wb") as f:
            f.write(content)

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

    async def download(self, skip_unchanged: bool = False) -> str | None:
        """
        Download the enrollment data file.

        With ``skip_unchanged``, send ``If-None-Match`` / ``If-Modified-Since``
        from the previous download and compare the SHA-256 of the body; return
        None when the registrar file is unchanged so callers skip parsing,
        comparison, and publishing. Failures raise ``FileProcessingError``.

        Returns:
            The path to the downloaded file, or None if it is unchanged.
        """
        validate_directory_exists(self.raw_xls_directory, create_if_missing=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # Add UUID to ensure uniqueness and prevent race conditions
        unique_id = uuid.uuid4().hex[:8]
        filename = os.path.join(
            self.raw_xls_directory,
            f"school_schedule_by_term_{timestamp}_{unique_id}.xls",
        )
        previous = self._load_state().get(self.semester, {})
        headers = {}
        if skip_unchanged:
            if previous.get("etag"):
                headers["If-None-Match"] = previous["etag"]
            if previous.get("last_modified"):
                headers["If-Modified-Since"] = previous["last_modified"]

        try:
            verify = self._tls_verification()
            async with httpx.AsyncClient(verify=verify) as client:
                logger.info("Downloading registrar file semester=%s", self.semester)
                response = await client.get(self.url, timeout=30.0, headers=headers)
                if response.status_code == 304:
                    logger.info(
                        "Registrar file not modified (HTTP 304) semester=%s",
                        self.semester,
                    )
                    return None
                response.raise_for_status()

                digest = hashlib.sha256(response.content).hexdigest()
                if skip_unchanged and digest == previous.get("sha256"):
                    logger.info(
                        "Registrar file unchanged (same SHA-256) semester=%s",
                        self.semester,
                    )
                    return None
                validators = {
                    key: value
                    for key, header in (
                        ("etag", "ETag"),
                        ("last_modified", "Last-Modified"),
                    )
                    if isinstance(value := response.headers.get(header), str)
                }

                # Offload blocking I/O to a separate thread
                await asyncio.to_thread(self._write_file, filename, response.content)
                await asyncio.to_thread(
                    self._save_state, {"sha256": digest, **validators}
                )

                logger.info(
                    "Downloaded registrar file semester=%s bytes=%d path=%s",
                    self.semester,
                    len(response.content),
                    filename,
                )
                return filename

        except httpx.TimeoutException as e:
            logger.error(f"Download timeout: {e}")
            raise FileProcessingError(f"Download timeout: {e}") from e
        except httpx.NetworkError as e:
            logger.error(f"Connection error: {e}")
            raise FileProcessingError(f"Connection error: {e}") from e
        except httpx.HTTPStatusError as e:
            status_code = e.response.status_code
            logger.error(f"HTTP error {status_code}: {e}")
            raise FileProcessingError(f"HTTP error: {e}") from e
        except httpx.RequestError as e:
            logger.error(f"Request error: {e}")
            raise FileProcessingError(f"Request error: {e}") from e
        except Exception as e:
            logger.error(f"Unexpected error during download: {e}")
            raise FileProcessingError(f"Unexpected download error: {e}") from e
