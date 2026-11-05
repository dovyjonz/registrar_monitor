"""Tests for the DataDownloader."""

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from registrarmonitor.automation.downloader import DataDownloader
from registrarmonitor.core.exceptions import FileProcessingError

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config(tmp_path, make_settings):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    settings = make_settings(
        data_source={"ca_bundle": None},
        directories={
            "raw_downloads": str(download_dir),
            "data_storage": str(tmp_path / "data"),
        },
        semesters={"Fall 2026": {"registrar_url": "http://test.url"}},
    )
    with patch(
        "registrarmonitor.automation.downloader.get_settings", return_value=settings
    ):
        yield settings


@pytest.fixture
def mock_httpx_client():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = None
        mock_client.client_class = mock_client_cls
        yield mock_client


@pytest.mark.asyncio
async def test_download_success(mock_config, mock_httpx_client):
    content = b"test content"
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    downloader = DataDownloader()

    filename = await downloader.download()

    assert filename is not None
    assert Path(filename).exists()
    with open(filename, "rb") as f:
        assert f.read() == content


@pytest.mark.asyncio
async def test_download_network_error(mock_config, mock_httpx_client):
    import httpx

    mock_httpx_client.get.side_effect = httpx.NetworkError("Network failure")

    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="Connection error"):
        await downloader.download()


@pytest.mark.asyncio
async def test_download_http_error(mock_config, mock_httpx_client):
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_error = httpx.HTTPStatusError(
        "Not found", request=MagicMock(), response=mock_response
    )

    mock_response.raise_for_status.side_effect = mock_error
    mock_httpx_client.get.return_value = mock_response

    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="HTTP error"):
        await downloader.download()


@pytest.mark.asyncio
async def test_download_timeout(mock_config, mock_httpx_client):
    """Timeout should raise FileProcessingError."""
    import httpx

    mock_httpx_client.get.side_effect = httpx.TimeoutException("Connection timed out")

    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="timeout"):
        await downloader.download()


@pytest.mark.asyncio
async def test_verified_tls_rejects_invalid_chain_without_writing_file(
    mock_config, mock_httpx_client
):
    """TLS verification is the default and failed verification writes no file."""
    import httpx

    mock_httpx_client.get.side_effect = httpx.ConnectError("certificate verify failed")
    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="Connection error"):
        await downloader.download()

    mock_httpx_client.client_class.assert_called_once_with(verify=True)
    assert list(Path(downloader.raw_xls_directory).iterdir()) == []


@pytest.mark.asyncio
async def test_configured_ca_bundle_is_passed_to_httpx(
    mock_config, mock_httpx_client, tmp_path
):
    ca_bundle = tmp_path / "registrar-ca.pem"
    ca_bundle.write_text("pem")
    mock_config.data_source.ca_bundle = str(ca_bundle)
    mock_httpx_client.get.side_effect = __import__("httpx").ConnectError(
        "certificate verify failed"
    )
    downloader = DataDownloader()

    with pytest.raises(FileProcessingError):
        await downloader.download()

    mock_httpx_client.client_class.assert_called_once_with(verify=str(ca_bundle))


@pytest.mark.asyncio
async def test_unset_ca_bundle_uses_default_verification(
    mock_config, mock_httpx_client
):
    mock_httpx_client.get.side_effect = __import__("httpx").ConnectError("down")
    downloader = DataDownloader()

    with pytest.raises(FileProcessingError):
        await downloader.download()

    mock_httpx_client.client_class.assert_called_once_with(verify=True)


@pytest.mark.asyncio
async def test_missing_ca_bundle_fails_closed(mock_config, mock_httpx_client, tmp_path):
    mock_config.data_source.ca_bundle = str(tmp_path / "none.pem")
    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="fetch_registrar_ca"):
        await downloader.download()

    mock_httpx_client.client_class.assert_not_called()


@pytest.mark.asyncio
async def test_old_downloads_are_pruned_but_recent_ones_and_other_files_stay(
    mock_config, mock_httpx_client
):
    from registrarmonitor.automation.downloader import KEEP_DOWNLOADS

    directory = Path(mock_config.directories.raw_downloads)
    old = []
    for number in range(KEEP_DOWNLOADS + 3):
        path = directory / f"school_schedule_by_term_2026010{number}_old.xls"
        path.write_bytes(b"old")
        os.utime(path, (1_000_000 + number, 1_000_000 + number))
        old.append(path)
    other = directory / "notes.txt"
    other.write_text("keep")
    response = MagicMock()
    response.content = b"new"
    mock_httpx_client.get.return_value = response

    new = Path(await DataDownloader().download())

    remaining = sorted(directory.glob("school_schedule_by_term_*.xls"))
    assert new in remaining
    assert len(remaining) == KEEP_DOWNLOADS
    # The newest old files survive alongside the new download.
    assert set(old[-(KEEP_DOWNLOADS - 1) :]) < set(remaining)
    assert other.exists()
