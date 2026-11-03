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
            "data_source": {},
            "directories": {
                "raw_downloads": str(download_dir),
                "data_storage": str(tmp_path / "data"),
            },
            "semesters": {
                "Fall 2026": {"registrar_url": "http://test.url"},
            },
        }
        yield mock


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
    mock_config.return_value["data_source"]["ca_bundle"] = str(ca_bundle)
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
    mock_config.return_value["data_source"]["ca_bundle"] = str(tmp_path / "none.pem")
    downloader = DataDownloader()

    with pytest.raises(FileProcessingError, match="fetch_registrar_ca"):
        await downloader.download()

    mock_httpx_client.client_class.assert_not_called()


def _response(content=b"xls-v1", status=200, **headers):
    response = MagicMock()
    response.status_code = status
    response.content = content
    response.headers = headers
    response.raise_for_status = MagicMock()
    return response


@pytest.mark.asyncio
async def test_unchanged_download_is_skipped_and_changed_one_is_returned(
    mock_config, mock_httpx_client
):
    downloader = DataDownloader()
    mock_httpx_client.get.side_effect = [
        _response(b"xls-v1", ETag='"a"', **{"Last-Modified": "Mon, 01 Jan 2026"}),
        _response(b"xls-v1"),  # same bytes, no validators: hash match
        _response(status=304),  # conditional request honored
        _response(b"xls-v2", ETag='"b"'),
    ]

    first = await downloader.download(skip_unchanged=True)
    assert first is not None
    assert await downloader.download(skip_unchanged=True) is None
    assert await downloader.download(skip_unchanged=True) is None
    third = await downloader.download(skip_unchanged=True)
    assert third is not None
    assert third != first

    sent = [call.kwargs["headers"] for call in mock_httpx_client.get.call_args_list]
    assert sent[0] == {}
    assert sent[1] == {"If-None-Match": '"a"', "If-Modified-Since": "Mon, 01 Jan 2026"}


@pytest.mark.asyncio
async def test_default_download_never_skips_and_forget_resets_state(
    mock_config, mock_httpx_client
):
    downloader = DataDownloader()
    mock_httpx_client.get.side_effect = lambda *a, **k: _response(b"same")

    assert await downloader.download() is not None
    assert await downloader.download() is not None  # manual runs always fetch
    assert await downloader.download(skip_unchanged=True) is None

    downloader.forget_last_download()
    assert await downloader.download(skip_unchanged=True) is not None
