"""Telegram polling startup safety."""

import asyncio
import logging
import time
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from telegram.error import Conflict

from registrarmonitor.subscriptions.runtime import (
    BOT_COMMANDS,
    LatencyTrackingUpdateProcessor,
    SubscriptionBotRuntime,
    bot_commands,
)

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_runtime_refuses_active_webhook_and_shuts_down():
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    start_polling = AsyncMock()
    shutdown = AsyncMock()
    get_updates = AsyncMock()
    runtime.application = cast(
        Any,
        SimpleNamespace(
            initialize=AsyncMock(),
            shutdown=shutdown,
            bot=SimpleNamespace(
                get_webhook_info=AsyncMock(
                    return_value=SimpleNamespace(url="https://example.test/hook")
                ),
                get_updates=get_updates,
                set_my_commands=AsyncMock(),
            ),
            updater=SimpleNamespace(start_polling=start_polling, stop=AsyncMock()),
            start=AsyncMock(),
            stop=AsyncMock(),
        ),
    )

    with pytest.raises(RuntimeError, match="active webhook"):
        await runtime.run()

    start_polling.assert_not_awaited()
    get_updates.assert_not_awaited()
    shutdown.assert_awaited_once_with()
    assert [command.command for command in BOT_COMMANDS] == [
        "start",
        "watch",
        "catalog",
        "import",
        "subscriptions",
        "status",
        "settings",
        "help",
    ]


@pytest.mark.asyncio
async def test_direct_polling_reports_another_consumer():
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime.application = cast(
        Any,
        SimpleNamespace(
            bot=SimpleNamespace(
                get_updates=AsyncMock(side_effect=Conflict("already polling"))
            )
        ),
    )

    with pytest.raises(RuntimeError, match="another process"):
        await runtime._poll_updates()


def test_public_command_menu_never_exposes_developer_command():
    assert "test" not in [command.command for command in bot_commands()]


def make_catalog_runtime():
    runtime = SubscriptionBotRuntime.__new__(SubscriptionBotRuntime)
    runtime._catalog_lock = asyncio.Lock()
    runtime._catalog_key = None
    runtime._catalog = None
    return runtime


@pytest.mark.asyncio
async def test_update_processor_does_not_serialize_slow_updates():
    processor = LatencyTrackingUpdateProcessor(
        max_concurrent_updates=2,
        slow_update_seconds=3,
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    second_finished = asyncio.Event()

    async def slow_update():
        first_started.set()
        await release_first.wait()

    async def later_update():
        second_finished.set()

    first = asyncio.create_task(
        processor.process_update(SimpleNamespace(update_id=1), slow_update())
    )
    await first_started.wait()
    second = asyncio.create_task(
        processor.process_update(SimpleNamespace(update_id=2), later_update())
    )

    await asyncio.wait_for(second_finished.wait(), timeout=0.1)
    release_first.set()
    await asyncio.gather(first, second)


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
async def test_one_chat_cannot_consume_every_active_update_slot():
    processor = LatencyTrackingUpdateProcessor(
        max_concurrent_updates=2,
        slow_update_seconds=3,
    )
    first_started = asyncio.Event()
    release_first = asyncio.Event()
    other_chat_finished = asyncio.Event()
    first_chat = SimpleNamespace(effective_chat=SimpleNamespace(id=91))
    other_chat = SimpleNamespace(effective_chat=SimpleNamespace(id=92))

    async def blocked():
        first_started.set()
        await release_first.wait()

    async def queued_behind_first():
        pass

    async def other_callback():
        other_chat_finished.set()

    first = asyncio.create_task(processor.process_update(first_chat, blocked()))
    await first_started.wait()
    second = asyncio.create_task(
        processor.process_update(first_chat, queued_behind_first())
    )
    other = asyncio.create_task(processor.process_update(other_chat, other_callback()))

    await asyncio.wait_for(other_chat_finished.wait(), timeout=0.1)
    release_first.set()
    await asyncio.gather(first, second, other)


@pytest.mark.asyncio
async def test_update_latency_is_logged_even_when_handler_fails(caplog):
    processor = LatencyTrackingUpdateProcessor(
        max_concurrent_updates=1,
        slow_update_seconds=0,
    )

    async def fail():
        raise RuntimeError("handler failed")

    with caplog.at_level(logging.WARNING), pytest.raises(RuntimeError):
        await processor.process_update(SimpleNamespace(message=object()), fail())

    assert "type=message" in caplog.text
    assert "duration_ms=" in caplog.text


@pytest.mark.asyncio
async def test_latest_catalog_database_read_does_not_block_event_loop(
    monkeypatch, current_snapshot
):
    from registrarmonitor.subscriptions import runtime as runtime_module

    def active_semester():
        return "Spring 2024"

    class SlowDatabase:
        def get_latest_snapshot_id(self):
            return 1

        def get_snapshot_data(self, _snapshot_id):
            time.sleep(0.1)
            return current_snapshot

    monkeypatch.setattr(runtime_module, "find_active_semester", active_semester)
    monkeypatch.setattr(
        runtime_module.DatabaseManager,
        "create_for_semester",
        lambda _semester: SlowDatabase(),
    )
    runtime = make_catalog_runtime()
    started = time.monotonic()
    heartbeat_at = None

    async def heartbeat():
        nonlocal heartbeat_at
        await asyncio.sleep(0.01)
        heartbeat_at = time.monotonic()

    catalog, _ = await asyncio.gather(runtime._latest_catalog(), heartbeat())

    assert catalog is not None
    assert heartbeat_at is not None
    assert heartbeat_at - started < 0.05


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
        "create_for_semester",
        lambda _semester: database,
    )
    runtime = make_catalog_runtime()

    first = await runtime._latest_catalog()
    second = await runtime._latest_catalog()

    assert first is second
    assert snapshot_reads == 1
