"""Representative private-chat command and callback flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.constants import ChatType

from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.interactions import SubscriptionInteractions
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import encode_target
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit


@pytest.fixture
def interaction(tmp_path, current_snapshot):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    return SubscriptionInteractions(store, provide_catalog), store


def make_update(*, text="", callback_data=None, private=True, user_id=41):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    user = SimpleNamespace(id=user_id)
    chat = SimpleNamespace(
        id=91,
        type=ChatType.PRIVATE if private else ChatType.GROUP,
    )
    callback = None
    if callback_data is not None:
        callback = SimpleNamespace(
            data=callback_data,
            message=message,
            answer=AsyncMock(),
            edit_message_text=AsyncMock(),
        )
    return SimpleNamespace(
        effective_message=message,
        effective_user=user,
        effective_chat=chat,
        callback_query=callback,
    )


def make_context(*args):
    return SimpleNamespace(args=list(args), user_data={})


@pytest.mark.asyncio
async def test_private_command_journey(interaction):
    bot, store = interaction
    update = make_update()

    await bot.start(update, make_context())
    await bot.watch(update, make_context("CS", "101"))
    await bot.status(update, make_context("CS 101"))
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))
    await bot.status(update, make_context())
    await bot.subscriptions(update, make_context())
    await bot.settings(update, make_context())
    await bot.help(update, make_context())

    sent = [
        call.args[0] for call in update.effective_message.reply_text.await_args_list
    ]
    assert any("Current semester: Spring 2024" in text for text in sent)
    assert "Select a course:" in sent
    assert any("Updated:" in text for text in sent)
    assert any("Choose a watched course or section:" in text for text in sent)
    assert any("Your subscriptions:" in text for text in sent)
    assert any("channel reports" in text for text in sent)
    assert any("/watch" in text for text in sent)


@pytest.mark.asyncio
async def test_deep_link_confirms_then_callback_subscribes(interaction):
    bot, store = interaction
    target = SubscriptionTarget("Spring 2024", "CS 101", "10L")
    payload = encode_target(target)
    start_update = make_update()

    await bot.start(start_update, make_context(payload))

    reply = start_update.effective_message.reply_text.await_args
    assert "Watch CS 101 / 10L?" in reply.args[0]
    assert "10L: 25/30" in reply.args[0]
    assert "Updated: 2024-01-15 10:30:00" in reply.args[0]
    assert (
        reply.kwargs["reply_markup"]
        .inline_keyboard[0][0]
        .callback_data.startswith("sub:")
    )

    callback_update = make_update(callback_data=f"sub:{payload}")
    await bot.callback(callback_update, make_context())

    callback_update.callback_query.answer.assert_awaited_once_with()
    assert store.list_subscriptions(41) == [target]
    callback_update.callback_query.edit_message_text.assert_awaited_once()


@pytest.mark.asyncio
async def test_text_search_and_destructive_confirmations(interaction):
    bot, store = interaction
    update = make_update(text="CS 101")
    context = make_context()

    await bot.watch(update, context)
    assert context.user_data["awaiting_watch"] is True
    await bot.watch_text(update, context)
    assert update.effective_message.reply_text.await_args.args[0] == "Select a course:"

    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))
    clear_update = make_update(callback_data="clear:yes")
    await bot.callback(clear_update, make_context())
    assert store.list_subscriptions(41) == []

    delete_update = make_update(callback_data="delete:yes")
    await bot.callback(delete_update, make_context())
    assert store.get_user(41) is None


@pytest.mark.asyncio
async def test_group_chat_is_rejected(interaction):
    bot, store = interaction
    update = make_update(private=False)

    await bot.start(update, make_context())

    update.effective_message.reply_text.assert_awaited_once_with(
        "Use this bot in a private chat."
    )
    assert store.get_user(41) is None


@pytest.mark.asyncio
async def test_developer_mode_admits_only_configured_user(tmp_path, current_snapshot):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    bot = SubscriptionInteractions(store, provide_catalog, test_user_id=41)
    outsider = make_update(user_id=42)
    outsider_callback = make_update(user_id=42, callback_data="subs:0")

    await bot.start(outsider, make_context())
    await bot.callback(outsider_callback, make_context())

    outsider.effective_message.reply_text.assert_not_awaited()
    outsider_callback.callback_query.answer.assert_not_awaited()
    assert store.get_user(42) is None

    developer = make_update()
    await bot.test_mode_status(developer, make_context())

    diagnostic = developer.effective_message.reply_text.await_args.args[0]
    assert "Developer test mode is active" in diagnostic
    assert "Spring 2024" in diagnostic
    assert "Latest snapshot:" in diagnostic
