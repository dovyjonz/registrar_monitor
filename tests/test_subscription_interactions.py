"""Representative private-chat command and callback flows."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest
from telegram.constants import ChatType, ParseMode

from registrarmonitor.models import Course, Section
from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.interactions import SubscriptionInteractions
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import (
    encode_course_import,
    encode_target,
    encode_watch_payload,
)
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit

COURSE_SHARE_URL = "https://registrar-monitor.spooktaken.workers.dev/courses/spring-2024/cs-101/?v=Abcd_123"


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

    await bot.start(start_update, make_context(encode_watch_payload(target)))

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
    assert keyboard[-1][0].url == COURSE_SHARE_URL

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
@pytest.mark.parametrize("url", [COURSE_SHARE_URL, None])
async def test_course_text_and_preview_button_share_one_published_identity(
    interaction, url
):
    bot, _store = interaction
    provider = Mock(side_effect=[url])
    bot._course_share_url = provider
    target = SubscriptionTarget("Spring 2024", "CS 101")
    update = make_update(callback_data=f"sections:{encode_target(target)}")
    await bot.callback(update, make_context())
    reply = update.callback_query.edit_message_text.await_args
    links = [
        button.url
        for row in reply.kwargs["reply_markup"].inline_keyboard
        for button in row
        if button.url
    ]
    assert links == ([url] if url else [])
    assert (f'href="{url}"' in reply.args[0]) if url else ("href=" not in reply.args[0])
    provider.assert_called_once_with("Spring 2024", "CS 101")
    assert "Review changes" in [
        button.text
        for row in reply.kwargs["reply_markup"].inline_keyboard
        for button in row
    ]


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


@pytest.mark.asyncio
async def test_channel_course_link_confirms_before_watching(interaction):
    bot, store = interaction
    target = SubscriptionTarget("Spring 2024", "CS 101")
    update = make_update()

    await bot.start(update, make_context("watch_sp24_CS101"))

    reply = update.effective_message.reply_text.await_args
    assert "Watch <code>CS 101</code>?" in reply.args[0]
    assert store.list_subscriptions(41) == []  # nothing is added before Confirm
    confirm = reply.kwargs["reply_markup"].inline_keyboard[0][0]
    assert confirm.text == "Confirm"
    assert confirm.callback_data == f"sub:{encode_target(target)}"

    callback_update = make_update(callback_data=confirm.callback_data)
    await bot.callback(callback_update, make_context())

    assert store.list_subscriptions(41) == [target]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "payload",
    [
        "watch_fa26_CS101",  # another semester
        "watch_sp24_CS999",  # unknown course
        "watch_sp24_CS101_99Z",  # unknown section
        "garbage",
        "watch_sp24_cs101",  # payloads are upper-case only
        "watch_sp24_" + "A" * 60,  # over Telegram's 64-character limit
    ],
)
async def test_bad_or_stale_start_links_never_add_a_watch(interaction, payload):
    bot, store = interaction
    update = make_update()

    await bot.start(update, make_context(payload))

    assert store.list_subscriptions(41) == []
    reply = update.effective_message.reply_text.await_args
    assert "/watch" in reply.args[0]
    assert "Confirm" not in str(reply.kwargs.get("reply_markup"))


@pytest.mark.asyncio
async def test_ambiguous_start_link_is_refused(multi_interaction):
    bot, store = multi_interaction
    catalog = await bot._catalog_provider()
    # "CS 101" and "CS101" compact to the same deep-link code.
    catalog.snapshot.courses["CS101"] = catalog.snapshot.courses["CS 101"]
    update = make_update()

    await bot.start(update, make_context("watch_sp24_CS101"))

    assert store.list_subscriptions(41) == []
    assert (
        "not in the latest snapshot"
        in update.effective_message.reply_text.await_args.args[0]
    )
