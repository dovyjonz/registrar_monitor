"""Representative private-chat command and callback flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.constants import ChatType

from registrarmonitor.models import Section
from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.interactions import SubscriptionInteractions
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import encode_course_import, encode_target
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


def test_registers_all_private_bot_handlers(interaction):
    bot, _store = interaction
    bot.test_user_id = 41
    application = SimpleNamespace(add_handler=Mock())

    bot.register(application)

    assert application.add_handler.call_count == 11


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
    assert any("Spring 2024" in text for text in sent)
    assert not any(text == "Select a course:" for text in sent)
    assert any("Updated " in text for text in sent)
    assert any("Choose a watched course or section:" in text for text in sent)
    assert any("Your watches" in text for text in sent)
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
    assert "10L  25/30  open" in reply.args[0]
    assert "Updated 2024-01-15 10:30:00" in reply.args[0]
    assert "View on the website" in reply.args[0]
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
    assert "<b>CS 101</b>" in update.effective_message.reply_text.await_args.args[0]

    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))
    clear_update = make_update(callback_data="clear:yes")
    await bot.callback(clear_update, make_context())
    assert store.list_subscriptions(41) == []

    delete_update = make_update(callback_data="delete:yes")
    await bot.callback(delete_update, make_context())
    assert store.get_user(41) is None


@pytest.mark.asyncio
async def test_section_watches_are_compact_and_apply_together(interaction):
    bot, store = interaction
    context = make_context()
    section = SubscriptionTarget("Spring 2024", "CS 101", "10L")
    course = SubscriptionTarget("Spring 2024", "CS 101")

    pick_update = make_update(callback_data=f"pick:{encode_target(section)}")
    await bot.callback(pick_update, context)

    assert store.list_subscriptions(41) == []
    keyboard = pick_update.callback_query.edit_message_text.await_args.kwargs[
        "reply_markup"
    ].inline_keyboard
    assert [button.text for button in keyboard[1]] == ["☑ 10L", "☐ 11L"]
    assert keyboard[-2][0].text == "Apply section watches · 1 selected"
    assert keyboard[-1][0].url.endswith("/courses/spring-2024/cs-101/")

    apply_update = make_update(callback_data=f"apply:{encode_target(course)}")
    await bot.callback(apply_update, context)

    assert store.list_subscriptions(41) == []
    assert (
        "Review section watches"
        in (apply_update.callback_query.edit_message_text.await_args.args[0])
    )

    confirm_update = make_update(callback_data="sections:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [section]
    confirmation = confirm_update.callback_query.edit_message_text.await_args.args[0]
    assert "1 section selected" in confirmation


@pytest.mark.asyncio
async def test_subscriptions_group_semester_once_and_offer_add_watch(interaction):
    bot, store = interaction
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "10L"))
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "11L"))
    update = make_update()

    await bot.subscriptions(update, make_context())

    reply = update.effective_message.reply_text.await_args
    assert reply.args[0].count("Spring 2024") == 1
    keyboard = reply.kwargs["reply_markup"].inline_keyboard
    assert len(keyboard[0]) == 2
    assert keyboard[-1][0].text == "➕ Add watch"


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
    assert "Developer test mode" in diagnostic
    assert "Spring 2024" in diagnostic
    assert "Latest snapshot:" in diagnostic


@pytest.mark.asyncio
async def test_website_import_requires_receipt_confirmation(interaction):
    bot, store = interaction
    context = make_context()
    import_update = make_update(text=encode_course_import("Spring 2024", ["CS 101"]))

    await bot.import_watches(import_update, context)

    assert store.list_subscriptions(41) == []
    receipt = import_update.effective_message.reply_text.await_args.args[0]
    assert "Review website import" in receipt
    assert "Nothing is added until you confirm" in receipt

    confirm_update = make_update(callback_data="import:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [SubscriptionTarget("Spring 2024", "CS 101")]


@pytest.mark.asyncio
async def test_developer_can_preview_simulated_change(tmp_path, current_snapshot):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    bot = SubscriptionInteractions(store, provide_catalog, test_user_id=41)
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "10L"))
    update = make_update(callback_data="test:simulate")

    await bot.callback(update, make_context())

    reply = update.callback_query.edit_message_text.await_args
    assert "Simulation only" in reply.args[0]
    assert "10L" in reply.args[0]
    assert store.list_subscriptions(41) == [
        SubscriptionTarget("Spring 2024", "CS 101", "10L")
    ]


@pytest.mark.asyncio
async def test_large_course_is_paged_and_accepts_typed_sections(
    tmp_path, current_snapshot
):
    for index in range(1, 21):
        section_id = f"{index}L"
        current_snapshot.courses["CS 101"].sections[section_id] = Section(
            section_id=section_id,
            section_type="L",
            enrollment=index,
            capacity=30,
            fill=index / 30,
        )
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    bot = SubscriptionInteractions(store, provide_catalog)
    context = make_context("CS 101")
    status_update = make_update()

    await bot.status(status_update, context)

    reply = status_update.effective_message.reply_text.await_args
    assert "Sections 1–12 of 20" in reply.args[0]
    keyboard = reply.kwargs["reply_markup"].inline_keyboard
    assert any(row[0].text == "⌨ Type section IDs" for row in keyboard)

    target = SubscriptionTarget("Spring 2024", "CS 101")
    type_update = make_update(callback_data=f"type:{encode_target(target)}")
    await bot.callback(type_update, context)
    typed_update = make_update(text="1L, 15L")
    await bot.watch_text(typed_update, context)

    receipt = typed_update.effective_message.reply_text.await_args.args[0]
    assert "Review section watches" in receipt
    assert "1L" in receipt
    assert "15L" in receipt

    confirm_update = make_update(callback_data="sections:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [
        SubscriptionTarget("Spring 2024", "CS 101", "15L"),
        SubscriptionTarget("Spring 2024", "CS 101", "1L"),
    ]
