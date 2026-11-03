"""Telegram webhook runtime and update processing."""

import asyncio
import logging
import socket
import sqlite3
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import httpx
import pytest
from pydantic import SecretStr

from registrarmonitor.config import get_settings
from registrarmonitor.subscriptions.runtime import (
    BOT_ALLOWED_UPDATES,
    BOT_COMMANDS,
    LatencyTrackingUpdateProcessor,
    SubscriptionBotRuntime,
    WebhookConfig,
    bot_commands,
)

pytestmark = pytest.mark.unit


SECRET = "s3cret_Token-123"
PUBLIC_URL = "https://bot.example.test/telegram"


def webhook_settings(**overrides):
    values = {"telegram_webhook_url": PUBLIC_URL, "telegram_webhook_secret": SECRET}
    values.update(overrides)
    for key in ("telegram_webhook_url", "telegram_webhook_secret"):
        if key == "telegram_webhook_secret" and values[key] is not None:
            values[key] = SecretStr(values[key])
    return get_settings().model_copy(update=values)


def test_webhook_config_reads_settings():
    settings = webhook_settings()
    settings.telegram_bot = settings.telegram_bot.model_copy(
        update={"webhook_port": 9000}
    )
    config = WebhookConfig.from_settings(settings)

    assert config.public_url == PUBLIC_URL
    assert config.secret_token == SECRET
    assert (config.listen, config.port) == ("127.0.0.1", 9000)
    assert config.url_path == "telegram"


@pytest.mark.parametrize("secret", [None, "", "   "])
def test_webhook_config_fails_fast_without_secret(secret):
    with pytest.raises(ValueError, match="TELEGRAM_WEBHOOK_SECRET is required"):
        WebhookConfig.from_settings(webhook_settings(telegram_webhook_secret=secret))


@pytest.mark.parametrize("secret", ["has space", "semi;colon", "x" * 257])
def test_webhook_config_rejects_secret_outside_telegram_alphabet(secret):
    with pytest.raises(ValueError, match="1-256 characters") as error:
        WebhookConfig.from_settings(webhook_settings(telegram_webhook_secret=secret))

    assert secret not in str(error.value)


@pytest.mark.parametrize(
    "url", [None, "", "http://bot.example.test/hook", "https://", "not a url"]
)
def test_webhook_config_requires_public_https_url(url):
    with pytest.raises(ValueError, match="public https URL"):
        WebhookConfig.from_settings(webhook_settings(telegram_webhook_url=url))


@pytest.mark.asyncio
async def test_runtime_serves_webhook_with_secret_and_shuts_down_in_order():
    calls = []

    def recorder(name):
        async def record(*args, **kwargs):
            calls.append((name, kwargs))

        return record

    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.webhook = WebhookConfig(PUBLIC_URL, SECRET, port=9000)
    runtime.test_user_id = None
    runtime._delivery_loop = AsyncMock(side_effect=asyncio.CancelledError)
    runtime.application = cast(
        Any,
        SimpleNamespace(
            initialize=recorder("initialize"),
            start=recorder("start"),
            stop=recorder("stop"),
            shutdown=recorder("shutdown"),
            bot=SimpleNamespace(set_my_commands=AsyncMock()),
            updater=SimpleNamespace(
                start_webhook=recorder("start_webhook"), stop=recorder("updater.stop")
            ),
        ),
    )

    with pytest.raises(asyncio.CancelledError):
        await runtime.run()

    assert [name for name, _ in calls] == [
        "initialize",
        "start",
        "start_webhook",
        "updater.stop",
        "stop",
        "shutdown",
    ]
    webhook_kwargs = calls[2][1]
    assert webhook_kwargs["secret_token"] == SECRET
    assert webhook_kwargs["webhook_url"] == PUBLIC_URL
    assert webhook_kwargs["listen"] == "127.0.0.1"
    assert webhook_kwargs["port"] == 9000
    assert webhook_kwargs["url_path"] == "telegram"
    assert webhook_kwargs["allowed_updates"] == BOT_ALLOWED_UPDATES


@pytest.mark.asyncio
async def test_webhook_server_rejects_requests_without_the_secret_header():
    """Exercise PTB's real webhook server: only Telegram's secret is accepted."""
    from telegram import Bot
    from telegram.ext import Updater

    class OfflineBot(Bot):
        async def initialize(self):
            pass

        async def shutdown(self):
            pass

        async def set_webhook(self, *args, **kwargs):
            return True

    port = socket.socket()
    port.bind(("127.0.0.1", 0))
    free_port = port.getsockname()[1]
    port.close()
    queue: asyncio.Queue = asyncio.Queue()
    updater = Updater(OfflineBot("123:token"), queue)
    await updater.initialize()
    await updater.start_webhook(
        listen="127.0.0.1",
        port=free_port,
        url_path="telegram",
        webhook_url=PUBLIC_URL,
        secret_token=SECRET,
    )
    url = f"http://127.0.0.1:{free_port}/telegram"
    body = {"update_id": 7}
    try:
        async with httpx.AsyncClient() as client:
            missing = await client.post(url, json=body)
            wrong = await client.post(
                url,
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
            assert queue.empty()
            accepted = await client.post(
                url,
                json=body,
                headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
            )
    finally:
        await updater.stop()
        await updater.shutdown()

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert accepted.status_code == 200


def test_public_command_menu_never_exposes_developer_command():
    assert [command.command for command in BOT_COMMANDS] == [
        "start",
        "watch",
        "watches",
        "help",
    ]
    assert "test" not in [command.command for command in bot_commands()]


@pytest.mark.asyncio
async def test_diagnostics_command_is_scoped_to_configured_user():
    bot = SimpleNamespace(set_my_commands=AsyncMock())
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.application = cast(Any, SimpleNamespace(bot=bot))
    runtime.test_user_id = 41

    await runtime._configure_commands()

    first, second = bot.set_my_commands.await_args_list
    assert "test" not in [command.command for command in first.args[0]]
    assert [command.command for command in second.args[0]][-1] == "test"
    assert second.kwargs["scope"].chat_id == 41


def make_catalog_runtime():
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime._catalog_lock = asyncio.Lock()
    runtime._catalog_key = None
    runtime._catalog = None
    return runtime


@pytest.mark.asyncio
async def test_update_processor_preserves_order_within_one_chat():
    processor = LatencyTrackingUpdateProcessor(
        max_concurrent_updates=2,
        slow_update_seconds=3,
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_finished = asyncio.Event()
    update = SimpleNamespace(update_id=1, effective_chat=SimpleNamespace(id=91))

    async def first_callback():
        first_started.set()
        await release_first.wait()

    async def second_callback():
        second_finished.set()

    first = asyncio.create_task(processor.process_update(update, first_callback()))
    await first_started.wait()
    second = asyncio.create_task(processor.process_update(update, second_callback()))

    with pytest.raises(TimeoutError):
        await asyncio.wait_for(second_finished.wait(), timeout=0.02)
    release_first.set()
    await asyncio.gather(first, second)


@pytest.mark.asyncio
async def test_latest_catalog_reuses_unchanged_snapshot(monkeypatch, current_snapshot):
    from registrarmonitor.subscriptions import runtime as runtime_module

    def active_semester():
        return "Spring 2024"

    database = SimpleNamespace(get_latest_snapshot_id=lambda: 1)
    snapshot_reads = 0

    def read_snapshot(_snapshot_id):
        nonlocal snapshot_reads
        snapshot_reads += 1
        return current_snapshot

    database.get_snapshot_data = read_snapshot
    monkeypatch.setattr(runtime_module, "find_active_semester", active_semester)
    monkeypatch.setattr(
        runtime_module.DatabaseManager,
        "create_read_only_for_semester",
        lambda _semester: database,
    )
    runtime = make_catalog_runtime()

    first = await runtime._latest_catalog()
    second = await runtime._latest_catalog()

    assert first is second
    assert snapshot_reads == 1


def test_pending_batch_recovery_contains_transient_enrollment_lock(monkeypatch, caplog):
    from registrarmonitor.subscriptions import runtime as runtime_module

    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.store = cast(
        Any,
        SimpleNamespace(
            list_pending_batches=lambda: [SimpleNamespace(semester="Fall 2026")]
        ),
    )
    monkeypatch.setattr(
        runtime_module.DatabaseManager,
        "create_read_only_for_semester",
        lambda _semester: (_ for _ in ()).throw(
            sqlite3.OperationalError("database is locked")
        ),
    )

    with caplog.at_level(logging.WARNING):
        runtime._recover_pending_batches()

    assert "Enrollment database is busy; pending batch recovery deferred" in caplog.text
