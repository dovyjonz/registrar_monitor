"""Telegram polling startup safety."""

from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from telegram.error import Conflict

from registrarmonitor.subscriptions.runtime import (
    BOT_COMMANDS,
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


def test_developer_command_is_visible_only_in_test_mode():
    assert "test" not in [command.command for command in bot_commands(None)]
    assert "test" in [command.command for command in bot_commands(41)]
