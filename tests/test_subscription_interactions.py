"""Representative private-chat command and callback flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.constants import ChatType, ParseMode

from registrarmonitor.models import Course, Section
from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.interactions import SubscriptionInteractions
from registrarmonitor.subscriptions.models import BotDiagnostics, SubscriptionTarget
from registrarmonitor.subscriptions.payloads import encode_course_import, encode_target
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit

COURSE_SHARE_URL = (
    "https://registrar-monitor.pages.dev/courses/spring-2024/cs-101/?v=Abcd_123"
)


@pytest.fixture
def interaction(tmp_path, current_snapshot):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    return (
        SubscriptionInteractions(
            store,
            provide_catalog,
            course_share_url=lambda _semester, _course: COURSE_SHARE_URL,
        ),
        store,
    )


@pytest.fixture
def multi_interaction(tmp_path, current_snapshot):
    current_snapshot.courses["MATH 202"] = Course(
        course_code="MATH 202",
        department="MATH",
        sections={
            "1L": Section(
                section_id="1L",
                section_type="L",
                enrollment=10,
                capacity=30,
                fill=0.33,
            )
        },
        average_fill=0.33,
    )
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    return (
        SubscriptionInteractions(
            store,
            provide_catalog,
            course_share_url=lambda _semester, _course: COURSE_SHARE_URL,
        ),
        store,
    )


def make_update(*, text="", callback_data=None, private=True, user_id=41, chat_id=91):
    message = SimpleNamespace(text=text, reply_text=AsyncMock())
    user = SimpleNamespace(id=user_id)
    chat = SimpleNamespace(
        id=chat_id,
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

    assert application.add_handler.call_count == 8


@pytest.mark.asyncio
async def test_private_command_journey(interaction):
    bot, store = interaction
    update = make_update()

    await bot.start(update, make_context())
    await bot.watch(update, make_context("CS", "101"))
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))
    await bot.watches(update, make_context())
    await bot.help(update, make_context())

    sent = [
        call.args[0] for call in update.effective_message.reply_text.await_args_list
    ]
    assert any("Spring 2024" in text for text in sent)
    assert not any(text == "Select a course:" for text in sent)
    assert any("My watches" in text for text in sent)
    assert any("/watch" in text for text in sent)


@pytest.mark.asyncio
async def test_start_presents_only_two_primary_paths(interaction):
    bot, _store = interaction
    update = make_update()

    await bot.start(update, make_context())

    reply = update.effective_message.reply_text.await_args
    assert "private message when enrollment changes" in reply.args[0]
    assert "CSCI 115" in reply.args[0]
    assert "CSCI 115, PHYS 161 / 1L" in reply.args[0]
    assert "Spring 2024" in reply.args[0]
    buttons = [
        button.text
        for row in reply.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert buttons == ["Add a watch", "My watches"]


@pytest.mark.asyncio
async def test_start_text_adds_watch_and_invalid_text_reprompts(interaction):
    bot, store = interaction
    context = make_context()

    await bot.start(make_update(), context)

    invalid_update = make_update(text="not a course")
    await bot.watch_text(invalid_update, context)

    invalid_reply = invalid_update.effective_message.reply_text.await_args.args[0]
    assert "No matching course found" in invalid_reply
    assert "Send a course code" in invalid_reply
    assert "<code>CSCI 115</code>" in invalid_reply
    assert (
        invalid_update.effective_message.reply_text.await_args.kwargs["parse_mode"]
        == ParseMode.HTML
    )

    valid_update = make_update(text="CS 101")
    await bot.watch_text(valid_update, context)

    assert store.list_subscriptions(41) == [SubscriptionTarget("Spring 2024", "CS 101")]
    assert (
        "Watching <code>CS 101</code>"
        in valid_update.effective_message.reply_text.await_args.args[0]
    )


@pytest.mark.asyncio
async def test_deep_link_confirms_then_callback_subscribes(interaction):
    bot, store = interaction
    target = SubscriptionTarget("Spring 2024", "CS 101", "10L")
    payload = encode_target(target)
    start_update = make_update()

    await bot.start(start_update, make_context(payload))

    reply = start_update.effective_message.reply_text.await_args
    assert "Watch <code>CS 101 / 10L</code>?" in reply.args[0]
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
    await bot.watch_text(update, context)
    assert (
        "Watching <code>CS 101</code>"
        in update.effective_message.reply_text.await_args.args[0]
    )

    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))
    clear_update = make_update(callback_data="clear:yes")
    await bot.callback(clear_update, make_context())
    assert store.list_subscriptions(41) == []

    delete_update = make_update(callback_data="delete:yes")
    await bot.callback(delete_update, make_context())
    assert store.get_user(41) is None


@pytest.mark.asyncio
async def test_single_watch_removal_requires_confirmation(interaction):
    bot, store = interaction
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    target = SubscriptionTarget("Spring 2024", "CS 101")
    store.subscribe(41, target)
    payload = encode_target(target)

    prompt_update = make_update(callback_data=f"rm:{payload}")
    await bot.callback(prompt_update, make_context())

    assert store.list_subscriptions(41) == [target]
    prompt = prompt_update.callback_query.edit_message_text.await_args
    assert "Stop watching <code>CS 101</code>?" in prompt.args[0]
    assert (
        prompt.kwargs["reply_markup"].inline_keyboard[0][0].callback_data
        == f"unsub:{payload}"
    )

    confirm_update = make_update(callback_data=f"unsub:{payload}")
    await bot.callback(confirm_update, make_context())

    assert store.list_subscriptions(41) == []


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
    assert [button.text for button in keyboard[1]] == ["10L selected", "11L"]
    assert keyboard[-2][0].text == "Review changes"
    assert keyboard[-1][0].url.endswith("/courses/spring-2024/cs-101/")

    apply_update = make_update(callback_data=f"apply:{encode_target(course)}")
    await bot.callback(apply_update, context)

    assert store.list_subscriptions(41) == []
    assert (
        "Review changes"
        in (apply_update.callback_query.edit_message_text.await_args.args[0])
    )

    confirm_update = make_update(callback_data="sections:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [section]
    confirmation = confirm_update.callback_query.edit_message_text.await_args.args[0]
    assert "Watch updated" in confirmation


@pytest.mark.asyncio
async def test_watches_group_semester_and_open_details_safely(interaction):
    bot, store = interaction
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "10L"))
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "11L"))
    update = make_update()

    await bot.watches(update, make_context())

    reply = update.effective_message.reply_text.await_args
    assert reply.args[0].count("Spring 2024") == 1
    keyboard = reply.kwargs["reply_markup"].inline_keyboard
    assert len(keyboard[0]) == 2
    assert keyboard[0][0].text == "CS 101 / 10L"
    assert keyboard[0][0].callback_data.startswith("detail:")
    assert keyboard[-2][0].text == "Add a watch"
    assert keyboard[-1][0].text == "Data and settings"

    detail_update = make_update(callback_data=keyboard[0][0].callback_data)
    await bot.callback(detail_update, make_context())
    detail = detail_update.callback_query.edit_message_text.await_args
    assert "Current scope:" in detail.args[0]
    assert "Updated 2024-01-15 10:30:00" in detail.args[0]
    detail_buttons = [
        button.text
        for row in detail.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert detail_buttons == ["Change sections", "Stop watching", "Back to my watches"]


@pytest.mark.asyncio
async def test_empty_watches_offers_add_watch_and_settings(interaction):
    bot, _store = interaction
    update = make_update()

    await bot.watches(update, make_context())

    reply = update.effective_message.reply_text.await_args
    assert "You have no watches" in reply.args[0]
    buttons = [
        button.text
        for row in reply.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]
    assert buttons == ["Add a watch", "Data and settings"]


@pytest.mark.asyncio
async def test_quick_reset_requires_confirmation_and_clears_watches(interaction):
    bot, store = interaction
    target = SubscriptionTarget("Spring 2024", "CS 101")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, target)

    reset_update = make_update(callback_data="clear")
    await bot.callback(reset_update, make_context())

    assert store.list_subscriptions(41) == [target]
    prompt = reset_update.callback_query.edit_message_text.await_args
    assert prompt.args[0] == "Clear all watches?"
    assert prompt.kwargs["reply_markup"].inline_keyboard[0][0].text == ("Confirm clear")
    assert prompt.kwargs["reply_markup"].inline_keyboard[1][0].text == "Back"

    confirm_update = make_update(callback_data="clear:yes")
    await bot.callback(confirm_update, make_context())

    assert store.list_subscriptions(41) == []
    assert (
        "Cleared 1 watch"
        in (confirm_update.callback_query.edit_message_text.await_args.args[0])
    )
    assert (
        confirm_update.callback_query.edit_message_text.await_args.kwargs[
            "reply_markup"
        ]
        .inline_keyboard[0][0]
        .text
        == "My watches"
    )


@pytest.mark.asyncio
async def test_data_settings_requires_confirmations(interaction):
    bot, store = interaction
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101"))

    settings_update = make_update(callback_data="settings")
    await bot.callback(settings_update, make_context())
    buttons = [
        button.text
        for row in settings_update.callback_query.edit_message_text.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard
        for button in row
    ]
    assert buttons == ["Clear all watches", "Delete my bot data", "Back to my watches"]

    clear_update = make_update(callback_data="clear")
    await bot.callback(clear_update, make_context())
    assert store.list_subscriptions(41)

    delete_update = make_update(callback_data="delete")
    await bot.callback(delete_update, make_context())
    delete_buttons = [
        button.text
        for row in delete_update.callback_query.edit_message_text.await_args.kwargs[
            "reply_markup"
        ].inline_keyboard
        for button in row
    ]
    assert delete_buttons == ["Confirm delete", "Back"]


@pytest.mark.asyncio
async def test_exact_watch_command_adds_course_or_section_immediately(interaction):
    bot, store = interaction
    course_update = make_update()

    await bot.watch(course_update, make_context("CS", "101"))

    assert store.list_subscriptions(41) == [SubscriptionTarget("Spring 2024", "CS 101")]
    assert (
        "Watching <code>CS 101</code>"
        in course_update.effective_message.reply_text.await_args.args[0]
    )
    assert (
        f'href="{COURSE_SHARE_URL}"'
        in course_update.effective_message.reply_text.await_args.args[0]
    )
    assert (
        course_update.effective_message.reply_text.await_args.kwargs[
            "link_preview_options"
        ].prefer_large_media
        is True
    )
    assert (
        course_update.effective_message.reply_text.await_args.kwargs[
            "link_preview_options"
        ].show_above_text
        is True
    )

    section_update = make_update()
    await bot.watch(section_update, make_context("CS", "101", "/", "10L"))

    assert store.list_subscriptions(41) == [SubscriptionTarget("Spring 2024", "CS 101")]
    assert (
        "whole-course watch already includes <code>CS 101 / 10L</code>"
        in section_update.effective_message.reply_text.await_args.args[0]
    )
    assert (
        "link_preview_options"
        not in section_update.effective_message.reply_text.await_args.kwargs
    )

    store.clear_subscriptions(41)
    section_update = make_update()
    await bot.watch(section_update, make_context("CS", "101", "/", "10L"))

    assert store.list_subscriptions(41) == [
        SubscriptionTarget("Spring 2024", "CS 101", "10L")
    ]
    assert (
        f'href="{COURSE_SHARE_URL}"'
        in section_update.effective_message.reply_text.await_args.args[0]
    )
    assert (
        section_update.effective_message.reply_text.await_args.kwargs[
            "link_preview_options"
        ].prefer_large_media
        is True
    )


@pytest.mark.asyncio
async def test_exact_watch_waits_for_a_published_preview(interaction):
    bot, store = interaction
    bot._course_share_url = lambda _semester, _course: None
    update = make_update()

    await bot.watch(update, make_context("CS", "101"))

    assert store.list_subscriptions(41) == []
    assert (
        "preview is still publishing"
        in (update.effective_message.reply_text.await_args.args[0])
    )
    assert "link_preview_options" not in (
        update.effective_message.reply_text.await_args.kwargs
    )


@pytest.mark.asyncio
async def test_multiple_exact_watch_text_adds_courses_and_sections(multi_interaction):
    bot, store = multi_interaction
    context = make_context()

    await bot.watch(make_update(), context)
    update = make_update(text="CS 101 / 10L, MATH 202")
    await bot.watch_text(update, context)

    assert store.list_subscriptions(41) == [
        SubscriptionTarget("Spring 2024", "CS 101", "10L"),
        SubscriptionTarget("Spring 2024", "MATH 202"),
    ]
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "CS 101 / 10L" in reply
    assert "MATH 202" in reply


@pytest.mark.asyncio
async def test_invalid_multiple_watch_text_reports_codes_and_reprompts(
    multi_interaction,
):
    bot, store = multi_interaction
    context = make_context()

    await bot.watch(make_update(), context)
    update = make_update(text="CS 101, UNKNOWN 999")
    await bot.watch_text(update, context)

    assert store.list_subscriptions(41) == []
    reply = update.effective_message.reply_text.await_args.args[0]
    assert "UNKNOWN 999" in reply
    assert "Send a course code" in reply


@pytest.mark.asyncio
async def test_whole_course_watch_can_be_amended_to_sections(interaction):
    bot, store = interaction
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    course = SubscriptionTarget("Spring 2024", "CS 101")
    store.subscribe(41, SubscriptionTarget("Spring 2024", "CS 101", "11L"))
    store.add_watch(41, course)
    context = make_context()

    edit_update = make_update(callback_data=f"sections:{encode_target(course)}")
    await bot.callback(edit_update, context)

    keyboard = edit_update.callback_query.edit_message_text.await_args.kwargs[
        "reply_markup"
    ].inline_keyboard
    assert any(button.text == "10L" for row in keyboard for button in row)

    section = SubscriptionTarget("Spring 2024", "CS 101", "10L")
    pick_update = make_update(callback_data=f"pick:{encode_target(section)}")
    await bot.callback(pick_update, context)
    apply_update = make_update(callback_data=f"apply:{encode_target(course)}")
    await bot.callback(apply_update, context)
    receipt = apply_update.callback_query.edit_message_text.await_args.args[0]
    assert "10L" in receipt
    assert "11L" not in receipt
    confirm_update = make_update(callback_data="sections:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [section]


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
async def test_diagnostics_are_private_without_restricting_ordinary_users(
    tmp_path, current_snapshot
):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    catalog = SubscriptionCatalog(current_snapshot)

    async def provide_catalog():
        return catalog

    async def diagnostics():
        return BotDiagnostics(
            uptime="2h 3m",
            users=4,
            active_users=3,
            pending_deliveries=2,
            recent_logs=("INFO: Polling started", "INFO: Update processed in 18 ms"),
        )

    bot = SubscriptionInteractions(
        store, provide_catalog, test_user_id=41, diagnostics=diagnostics
    )
    outsider = make_update(user_id=42, chat_id=92)
    outsider_callback = make_update(user_id=42, chat_id=92, callback_data="subs:0")
    outsider_test = make_update(user_id=42, chat_id=92)

    await bot.start(outsider, make_context())
    await bot.callback(outsider_callback, make_context())
    await bot.developer_diagnostics(outsider_test, make_context())

    outsider.effective_message.reply_text.assert_awaited_once()
    outsider_callback.callback_query.answer.assert_awaited_once()
    outsider_test.effective_message.reply_text.assert_not_awaited()
    assert store.get_user(42) is not None

    developer = make_update()
    await bot.developer_diagnostics(developer, make_context())

    diagnostic = developer.effective_message.reply_text.await_args.args[0]
    assert "Developer diagnostics" in diagnostic
    assert "Spring 2024" in diagnostic
    assert "Latest snapshot:" in diagnostic
    assert "Bot: running (uptime 2h 3m)" in diagnostic
    assert "Users: 4 total, 3 active" in diagnostic
    assert "Pending deliveries: 2" in diagnostic
    assert "Polling started" in diagnostic
    assert "Update processed in 18 ms" in diagnostic
    assert (
        developer.effective_message.reply_text.await_args.kwargs["parse_mode"]
        == ParseMode.HTML
    )


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
    context = make_context()
    course = SubscriptionTarget("Spring 2024", "CS 101")
    view_update = make_update(callback_data=f"sections:{encode_target(course)}")

    await bot.callback(view_update, context)

    reply = view_update.callback_query.edit_message_text.await_args
    assert "Sections 1-12 of 20" in reply.args[0]
    keyboard = reply.kwargs["reply_markup"].inline_keyboard
    assert any(row[0].text == "Type section IDs" for row in keyboard)
    assert keyboard[-1][-1].text == "Cancel"

    target = SubscriptionTarget("Spring 2024", "CS 101")
    type_update = make_update(callback_data=f"type:{encode_target(target)}")
    await bot.callback(type_update, context)
    typed_update = make_update(text="1L, 15L")
    await bot.watch_text(typed_update, context)

    receipt = typed_update.effective_message.reply_text.await_args.args[0]
    assert "Review changes" in receipt
    assert "1L" in receipt
    assert "15L" in receipt

    confirm_update = make_update(callback_data="sections:confirm")
    await bot.callback(confirm_update, context)

    assert store.list_subscriptions(41) == [
        SubscriptionTarget("Spring 2024", "CS 101", "15L"),
        SubscriptionTarget("Spring 2024", "CS 101", "1L"),
    ]
