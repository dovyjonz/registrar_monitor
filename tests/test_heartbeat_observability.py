import json
import logging

import httpx
import pytest

from registrarmonitor import heartbeat, observability
from registrarmonitor.core.logging_config import JsonFormatter


def _mock_client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    real = httpx.AsyncClient
    monkeypatch.setattr(
        heartbeat.httpx,
        "AsyncClient",
        lambda **kw: real(transport=transport, **kw),
    )


@pytest.mark.asyncio
async def test_heartbeat_noop_without_url(monkeypatch):
    monkeypatch.delenv("HEARTBEAT_URL", raising=False)
    assert await heartbeat.send_heartbeat() is False


@pytest.mark.asyncio
async def test_heartbeat_pings_url(monkeypatch):
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(200)

    _mock_client(monkeypatch, handler)
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc.example/ping/abc")
    assert await heartbeat.send_heartbeat() is True
    assert seen == ["https://hc.example/ping/abc"]


@pytest.mark.asyncio
async def test_heartbeat_failure_is_logged_not_raised(monkeypatch, caplog):
    def handler(request):
        raise httpx.ConnectError("boom")

    _mock_client(monkeypatch, handler)
    monkeypatch.setenv("HEARTBEAT_URL", "https://hc.example/ping/secret")
    with caplog.at_level(logging.WARNING):
        assert await heartbeat.send_heartbeat() is False
    assert "secret" not in caplog.text


def test_sentry_disabled_without_dsn(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    monkeypatch.setattr(observability, "_initialized", False)
    assert observability.init_sentry() is False


def test_json_formatter_emits_json_line():
    record = logging.LogRecord("x", logging.INFO, "", 0, "hi %s", ("there",), None)
    data = json.loads(JsonFormatter().format(record))
    assert data["message"] == "hi there"
    assert data["level"] == "INFO"
