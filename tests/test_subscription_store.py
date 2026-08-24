"""Durable state for Telegram course subscriptions."""

from datetime import UTC, datetime, timedelta

import pytest

from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.store import SubscriptionStore

pytestmark = pytest.mark.unit


@pytest.fixture
def store(tmp_path):
    return SubscriptionStore(
        tmp_path / "subscriptions.db",
        clock=lambda: datetime(2026, 8, 24, 12, tzinfo=UTC),
    )


def test_user_subscription_lifecycle_is_idempotent(store):
    target = SubscriptionTarget("Fall 2026", "CSCI 151", "001")

    store.touch_user(telegram_user_id=41, private_chat_id=91)
    assert store.subscribe(41, target) is True
    assert store.subscribe(41, target) is False
    assert store.list_subscriptions(41) == [target]

    assert store.unsubscribe(41, target) is True
    assert store.unsubscribe(41, target) is False
    assert store.list_subscriptions(41) == []


def test_reactivation_and_account_deletion(store):
    target = SubscriptionTarget("Fall 2026", "MATH 161")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, target)

    store.deactivate_user(41)
    assert store.get_user(41).active is False
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    assert store.get_user(41).active is True

    store.delete_user(41)
    assert store.get_user(41) is None
    assert store.list_subscriptions(41) == []


def test_batch_activation_snapshots_only_eligible_recipients(store):
    course = SubscriptionTarget("Fall 2026", "CSCI 151")
    section = SubscriptionTarget("Fall 2026", "CSCI 151", "001")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, course)

    batch = store.stage_batch(
        "Fall 2026", previous_snapshot_id=10, current_snapshot_id=11
    )
    store.touch_user(telegram_user_id=42, private_chat_id=92)
    store.subscribe(42, section)
    store.mark_channel_succeeded(batch.batch_id)
    store.activate_batch(batch.batch_id)

    deliveries = store.list_deliveries(batch.batch_id)
    assert [
        (delivery.telegram_user_id, delivery.private_chat_id) for delivery in deliveries
    ] == [(41, 91)]
    assert store.get_batch(batch.batch_id).status == "deliverable"


def test_batch_targets_are_immutable_and_do_not_backfill(store):
    staged = SubscriptionTarget("Fall 2026", "CSCI 151")
    added_later = SubscriptionTarget("Fall 2026", "MATH 161")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, staged)

    batch = store.stage_batch("Fall 2026", 10, 11)
    store.subscribe(41, added_later)
    store.stage_batch("Fall 2026", 10, 11)

    assert store.effective_batch_subscriptions(batch.batch_id, 41) == [staged]

    store.unsubscribe(41, staged)
    store.subscribe(41, staged)
    assert store.effective_batch_subscriptions(batch.batch_id, 41) == []


def test_quick_add_preserves_frozen_subscription_identity(store):
    original = SubscriptionTarget("Fall 2026", "CSCI 151", "001")
    added = SubscriptionTarget("Fall 2026", "CSCI 151", "002")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.add_watch(41, original)
    batch = store.stage_batch("Fall 2026", 10, 11)

    store.add_watch(41, added)

    assert store.effective_batch_subscriptions(batch.batch_id, 41) == [original]


def test_resubscribing_cannot_reuse_a_staged_subscription_identity(store):
    target = SubscriptionTarget("Fall 2026", "CSCI 151")
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, target)
    batch = store.stage_batch("Fall 2026", 10, 11)

    store.unsubscribe(41, target)
    store.subscribe(41, target)

    assert store.effective_batch_subscriptions(batch.batch_id, 41) == []


def test_account_deletion_removes_pending_delivery_identity(store):
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151"))
    batch = store.stage_batch("Fall 2026", 10, 11)
    store.mark_channel_succeeded(batch.batch_id)
    store.activate_batch(batch.batch_id)

    store.delete_user(41)

    assert store.list_deliveries(batch.batch_id) == []
    assert store.get_batch(batch.batch_id).status == "complete"


def test_due_delivery_progress_and_retry_are_durable(store):
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151"))
    batch = store.stage_batch("Fall 2026", 10, 11)
    store.mark_channel_succeeded(batch.batch_id)
    store.activate_batch(batch.batch_id)

    delivery = store.claim_due_delivery(lease_seconds=30)
    assert delivery is not None
    assert delivery.status == "sending"

    store.record_chunk_sent(delivery.delivery_id, next_chunk_index=1)
    store.retry_delivery(delivery.delivery_id, delay_seconds=60, category="network")
    assert store.claim_due_delivery(lease_seconds=30) is None

    persisted = store.list_deliveries(batch.batch_id)[0]
    assert persisted.next_chunk_index == 1
    assert persisted.attempt_count == 1
    assert persisted.error_category == "network"


def test_course_subscription_supersedes_section_overlap(store):
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151"))
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151", "001"))

    assert store.effective_subscriptions(41, "Fall 2026") == [
        SubscriptionTarget("Fall 2026", "CSCI 151")
    ]


def test_cleanup_keeps_inactive_users_who_still_have_subscriptions(tmp_path):
    now = [datetime(2026, 1, 1, tzinfo=UTC)]
    store = SubscriptionStore(tmp_path / "subscriptions.db", clock=lambda: now[0])
    store.touch_user(telegram_user_id=41, private_chat_id=91)
    store.subscribe(41, SubscriptionTarget("Fall 2026", "CSCI 151"))
    store.touch_user(telegram_user_id=42, private_chat_id=92)
    store.deactivate_user(41)
    store.deactivate_user(42)
    now[0] += timedelta(days=181)

    _, deleted_users = store.cleanup(
        completed_batch_days=90,
        inactive_user_days=180,
    )

    assert deleted_users == 1
    assert store.get_user(41) is not None
    assert store.get_user(42) is None
