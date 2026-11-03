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
    create_web_app,
)

pytestmark = pytest.mark.unit


SECRET = "s3cret_Token-123"
PUBLIC_URL = "https://bot.example.test/telegram"


def webhook_settings(**overrides):
    secret = overrides.pop("telegram_webhook_secret", SECRET)
    values = {
        "telegram_webhook_url": PUBLIC_URL,
        "telegram_webhook_secret": None if secret is None else SecretStr(secret),
        **overrides,
    }
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


def free_port() -> int:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def fake_application(calls: list):
    def recorder(name):
        async def record(*args, **kwargs):
            calls.append((name, kwargs))

        return record

    return SimpleNamespace(
        initialize=recorder("initialize"),
        start=recorder("start"),
        stop=recorder("stop"),
        shutdown=recorder("shutdown"),
        update_queue=asyncio.Queue(),
        bot=SimpleNamespace(
            set_my_commands=recorder("set_my_commands"),
            set_chat_menu_button=recorder("set_chat_menu_button"),
            set_webhook=recorder("set_webhook"),
        ),
    )


@pytest.mark.asyncio
async def test_runtime_registers_webhook_and_enforces_the_secret_header(monkeypatch):
    """Run the real ASGI server: only Telegram's secret reaches the update queue."""
    calls: list = []
    port = free_port()
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.webhook = WebhookConfig(PUBLIC_URL, SECRET, port=port)
    runtime.test_user_id = None
    runtime.miniapp_enabled = False
    runtime.settings = get_settings()
    application = fake_application(calls)
    runtime.application = cast(Any, application)
    runtime.web_app = create_web_app(cast(Any, application), runtime.webhook)
    monkeypatch.setattr(runtime, "_delivery_loop", lambda: asyncio.sleep(3600))

    task = asyncio.create_task(runtime.run())
    url = f"http://127.0.0.1:{port}/telegram"
    try:
        async with httpx.AsyncClient() as client:
            for _ in range(100):
                if any(name == "set_webhook" for name, _ in calls):
                    break
                await asyncio.sleep(0.05)
            missing = await client.post(url, json={"update_id": 7})
            wrong = await client.post(
                url,
                json={"update_id": 7},
                headers={"X-Telegram-Bot-Api-Secret-Token": "wrong"},
            )
            assert application.update_queue.empty()
            accepted = await client.post(
                url,
                json={"update_id": 7},
                headers={"X-Telegram-Bot-Api-Secret-Token": SECRET},
            )
    finally:
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    assert (missing.status_code, wrong.status_code, accepted.status_code) == (
        403,
        403,
        200,
    )
    assert (await application.update_queue.get()).update_id == 7
    names = [name for name, _ in calls]
    assert names == [
        "initialize",
        "set_my_commands",
        "set_chat_menu_button",
        "start",
        "set_webhook",
        "stop",
        "shutdown",
    ]
    webhook_kwargs = calls[names.index("set_webhook")][1]
    assert webhook_kwargs["secret_token"] == SECRET
    assert webhook_kwargs["allowed_updates"] == BOT_ALLOWED_UPDATES


@pytest.mark.asyncio
async def test_menu_button_opens_the_site_only_when_the_mini_app_is_enabled():
    calls: list = []
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.settings = get_settings()
    runtime.application = cast(Any, fake_application(calls))

    runtime.miniapp_enabled = True
    await runtime._configure_menu_button()
    runtime.miniapp_enabled = False
    await runtime._configure_menu_button()

    enabled, disabled = (kwargs["menu_button"] for _, kwargs in calls)
    assert enabled.web_app.url == runtime.settings.website.base_url
    assert disabled.type == "default"


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
