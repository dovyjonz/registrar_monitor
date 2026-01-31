import pytest
import os
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from registrarmonitor.automation.downloader import DataDownloader
from registrarmonitor.core.exceptions import FileProcessingError

@pytest.fixture
def mock_config():
    with patch("registrarmonitor.automation.downloader.get_config") as mock:
        mock.return_value = {
            "data_source": {"url": "http://test.url"},
            "directories": {"raw_downloads": "tests/temp_downloads"}
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
    # Setup
    content = b"test content"
    mock_response = MagicMock()
    mock_response.content = content
    mock_response.raise_for_status = MagicMock()
    mock_httpx_client.get.return_value = mock_response

    downloader = DataDownloader()

    # Execute
    filename = await downloader.download()

    # Verify
    assert filename is not None
    assert os.path.exists(filename)
    with open(filename, "rb") as f:
        assert f.read() == content

    # Cleanup
    if os.path.exists(filename):
        os.remove(filename)
    if os.path.exists("tests/temp_downloads"):
        os.rmdir("tests/temp_downloads")

@pytest.mark.asyncio
async def test_download_network_error(mock_config, mock_httpx_client):
    # Setup
    import httpx
    mock_httpx_client.get.side_effect = httpx.NetworkError("Network failure")

    downloader = DataDownloader()

    # Execute & Verify
    with pytest.raises(FileProcessingError, match="Connection error"):
        await downloader.download()

@pytest.mark.asyncio
async def test_download_http_error(mock_config, mock_httpx_client):
    # Setup
    import httpx
    mock_response = MagicMock()
    mock_response.status_code = 404
    mock_error = httpx.HTTPStatusError("Not found", request=MagicMock(), response=mock_response)

    mock_response.raise_for_status.side_effect = mock_error
    mock_httpx_client.get.return_value = mock_response

    downloader = DataDownloader()

    # Execute & Verify
    with pytest.raises(FileProcessingError, match="HTTP error"):
        await downloader.download()
