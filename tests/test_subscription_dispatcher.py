"""Personal notification matching and durable delivery."""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from telegram.error import Forbidden, RetryAfter

from registrarmonitor.models import Course, EnrollmentSnapshot, Section
from registrarmonitor.subscriptions.dispatcher import SubscriptionDispatcher
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit


def prepare_delivery(tmp_path, target):
    store = SubscriptionStore(
        tmp_path / "subscriptions.db",
        clock=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
    )
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, target)
    batch = store.stage_batch(target.semester, 10, 11)
    store.mark_channel_succeeded(batch.batch_id)
    store.activate_batch(batch.batch_id)
    return store, batch


@pytest.mark.asyncio
async def test_dispatches_only_watched_section(
    tmp_path, current_snapshot, previous_snapshot
):
    store, batch = prepare_delivery(
        tmp_path, SubscriptionTarget("Spring 2024", "CS 101", "10L")
    )
    enrollment_db = MagicMock()
    enrollment_db.get_snapshot_data.side_effect = [current_snapshot, previous_snapshot]
    messenger = AsyncMock()
    dispatcher = SubscriptionDispatcher(store, enrollment_db, messenger)

    assert await dispatcher.dispatch_one() is True

    text = messenger.send_message.await_args.kwargs["text"]
    reply_markup = messenger.send_message.await_args.kwargs["reply_markup"]
    assert "10L" in text
    assert "11L" not in text
    assert "Spring 2024" in text
    assert reply_markup.inline_keyboard[0][0].callback_data == "subs:0"
    assert store.list_deliveries(batch.batch_id)[0].status == "sent"


@pytest.mark.asyncio
async def test_retry_after_is_persisted(tmp_path, current_snapshot, previous_snapshot):
    store, batch = prepare_delivery(
        tmp_path, SubscriptionTarget("Spring 2024", "CS 101")
    )
    enrollment_db = MagicMock()
    enrollment_db.get_snapshot_data.side_effect = [current_snapshot, previous_snapshot]
    messenger = AsyncMock()
    messenger.send_message.side_effect = RetryAfter(7)

    assert (
        await SubscriptionDispatcher(store, enrollment_db, messenger).dispatch_one()
        is True
    )

    delivery = store.list_deliveries(batch.batch_id)[0]
    assert delivery.status == "retry"
    assert delivery.attempt_count == 1
    assert delivery.error_category == "rate_limit"


@pytest.mark.asyncio
async def test_forbidden_deactivates_user(
    tmp_path, current_snapshot, previous_snapshot
):
    store, batch = prepare_delivery(
        tmp_path, SubscriptionTarget("Spring 2024", "CS 101")
    )
    enrollment_db = MagicMock()
    enrollment_db.get_snapshot_data.side_effect = [current_snapshot, previous_snapshot]
    messenger = AsyncMock()
    messenger.send_message.side_effect = Forbidden("blocked")

    assert (
        await SubscriptionDispatcher(store, enrollment_db, messenger).dispatch_one()
        is True
    )

    assert store.get_user(41).active is False
    assert store.list_deliveries(batch.batch_id)[0].status == "permanent_failed"


@pytest.mark.asyncio
async def test_unsubscribed_delivery_is_skipped(
    tmp_path, current_snapshot, previous_snapshot
):
    target = SubscriptionTarget("Spring 2024", "CS 101")
    store, batch = prepare_delivery(tmp_path, target)
    store.unsubscribe(41, target)
    enrollment_db = MagicMock()
    messenger = AsyncMock()

    assert (
        await SubscriptionDispatcher(store, enrollment_db, messenger).dispatch_one()
        is True
    )

    messenger.send_message.assert_not_awaited()
    enrollment_db.get_snapshot_data.assert_not_called()
    assert store.list_deliveries(batch.batch_id)[0].status == "skipped"


@pytest.mark.asyncio
async def test_developer_mode_skips_every_other_recipient(tmp_path):
    store, batch = prepare_delivery(
        tmp_path, SubscriptionTarget("Spring 2024", "CS 101")
    )
    enrollment_db = MagicMock()
    messenger = AsyncMock()
    dispatcher = SubscriptionDispatcher(
        store,
        enrollment_db,
        messenger,
        test_user_id=42,
    )

    assert await dispatcher.dispatch_one() is True

    messenger.send_message.assert_not_awaited()
    enrollment_db.get_snapshot_data.assert_not_called()
    delivery = store.list_deliveries(batch.batch_id)[0]
    assert delivery.status == "skipped"
    assert delivery.error_category == "developer_test_mode"


@pytest.mark.asyncio
async def test_oversized_course_digest_fails_only_its_delivery(tmp_path):
    store, batch = prepare_delivery(
        tmp_path, SubscriptionTarget("Spring 2024", "CS 101")
    )
    previous_sections = {
        f"{index:03}L": Section(f"{index:03}L", "L", 0, 30, 0) for index in range(250)
    }
    current_sections = {
        code: Section(code, "L", 1, 30, 1 / 30) for code in previous_sections
    }
    previous = EnrollmentSnapshot(
        "2024-01-15 09:00:00",
        "Spring 2024",
        0,
        {"CS 101": Course("CS 101", "CS", previous_sections, 0)},
    )
    current = EnrollmentSnapshot(
        "2024-01-15 10:30:00",
        "Spring 2024",
        1 / 30,
        {"CS 101": Course("CS 101", "CS", current_sections, 1 / 30)},
    )
    enrollment_db = MagicMock()
    enrollment_db.get_snapshot_data.side_effect = [current, previous]
    messenger = AsyncMock()

    assert (
        await SubscriptionDispatcher(store, enrollment_db, messenger).dispatch_one()
        is True
    )

    messenger.send_message.assert_not_awaited()
    delivery = store.list_deliveries(batch.batch_id)[0]
    assert delivery.status == "permanent_failed"
    assert delivery.error_category == "rendering"
