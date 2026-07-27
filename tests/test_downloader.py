"""Tests for the DataDownloader."""

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from registrarmonitor.automation.downloader import DataDownloader
from registrarmonitor.core.exceptions import FileProcessingError

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config(tmp_path):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    with patch("registrarmonitor.automation.downloader.get_config") as mock:
        mock.return_value = {
            "data_source": {"url": "http://test.url"},
            "directories": {"raw_downloads": str(download_dir)},
        }
        yield mock


@pytest.fixture
def mock_httpx_client():
    with patch("httpx.AsyncClient") as mock_client_cls:
        mock_client = AsyncMock()
        mock_client_cls.return_value.__aenter__.return_value = mock_client
        mock_client_cls.return_value.__aexit__.return_value = None
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
@pytest.mark.parametrize(
    "status_code",
    [400, 403, 404, 429, 500, 502, 503],
)
async def test_download_various_http_errors(
    mock_config, mock_httpx_client, status_code
):
    """All HTTP error status codes should raise FileProcessingError."""
    import httpx

    mock_response = MagicMock()
    mock_response.status_code = status_code
    mock_error = httpx.HTTPStatusError(
        f"HTTP {status_code}", request=MagicMock(), response=mock_response
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
async def test_download_connect_error(mock_config, mock_httpx_client):
    """Connection refused should raise FileProcessingError."""
    import httpx

    mock_httpx_client.get.side_effect = httpx.ConnectError("Connection refused")

    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="Connection error"):
        await downloader.download()


@pytest.mark.asyncio
async def test_download_request_error(mock_config, mock_httpx_client):
    """Generic request errors should raise FileProcessingError."""
    import httpx

    mock_httpx_client.get.side_effect = httpx.RequestError(
        "Bad request", request=MagicMock()
    )

    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="Request error"):
        await downloader.download()
