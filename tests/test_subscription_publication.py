"""The report-to-personal-notification publication seam."""

from unittest.mock import MagicMock

import pytest

from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.publication import ReportPublication
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit


@pytest.fixture
def publication(tmp_path):
    store = SubscriptionStore(tmp_path / "subscriptions.db")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151"))
    enrollment_db = MagicMock()
    enrollment_db.was_snapshot_reported.return_value = False
    return ReportPublication(store, enrollment_db), store, enrollment_db


@pytest.mark.asyncio
async def test_publish_orders_channel_log_and_activation(publication):
    publisher, store, enrollment_db = publication
    events = []

    async def send_channel():
        events.append("channel")

    enrollment_db.add_reporting_log.side_effect = lambda **_: events.append("log")
    original_activate = store.activate_batch

    def activate(batch_id):
        events.append("activate")
        original_activate(batch_id)

    store.activate_batch = activate

    await publisher.publish(
        semester="Fall 2026",
        previous_snapshot_id=10,
        current_snapshot_id=11,
        send_channel=send_channel,
    )

    assert events == ["channel", "log", "activate"]
    assert store.list_pending_batches() == []
    assert len(store.list_deliveries(1)) == 1


@pytest.mark.asyncio
async def test_channel_failure_leaves_batch_pending_and_does_not_log(publication):
    publisher, store, enrollment_db = publication

    async def fail_channel():
        raise OSError("temporary Telegram failure")

    with pytest.raises(OSError, match="temporary Telegram failure"):
        await publisher.publish(
            semester="Fall 2026",
            previous_snapshot_id=10,
            current_snapshot_id=11,
            send_channel=fail_channel,
        )

    assert store.list_pending_batches()[0].channel_succeeded is False
    enrollment_db.add_reporting_log.assert_not_called()
    assert store.list_deliveries(1) == []


@pytest.mark.asyncio
async def test_retry_after_logged_channel_delivery_does_not_resend(publication):
    publisher, store, enrollment_db = publication
    batch = store.stage_batch("Fall 2026", 10, 11)
    store.mark_channel_succeeded(batch.batch_id)
    enrollment_db.was_snapshot_reported.return_value = True
    send_channel = MagicMock()

    await publisher.publish(
        semester="Fall 2026",
        previous_snapshot_id=10,
        current_snapshot_id=11,
        send_channel=send_channel,
    )

    send_channel.assert_not_called()
    enrollment_db.add_reporting_log.assert_not_called()
    assert store.get_batch(batch.batch_id).status == "deliverable"


def test_recover_activates_only_channel_backed_reported_batches(publication):
    publisher, store, enrollment_db = publication
    safe = store.stage_batch("Fall 2026", 10, 11)
    store.mark_channel_succeeded(safe.batch_id)
    unsafe = store.stage_batch("Fall 2026", 11, 12)
    enrollment_db.was_snapshot_reported.side_effect = lambda snapshot_id, **_: (
        snapshot_id == 11
    )

    assert publisher.recover() == 1
    assert store.get_batch(safe.batch_id).status == "deliverable"
    assert store.get_batch(unsafe.batch_id).status == "pending"
