"""Subscription target payloads and latest-snapshot search."""

import base64
import json

import pytest

from registrarmonitor.models import Course
from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import (
    decode_course_import,
    decode_target,
    decode_watch_payload,
    encode_course_import,
    encode_target,
    encode_watch_payload,
    watch_deep_link,
)

pytestmark = pytest.mark.unit


def test_target_payload_round_trip_is_telegram_safe():
    target = SubscriptionTarget("Fall 2026", "CSCI 151", "001")

    payload = encode_target(target)

    assert len(payload) <= 64
    assert set(payload) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    assert decode_target(payload) == target


def test_unknown_payload_version_is_rejected():
    raw = json.dumps([2, "Fall 2026", "CSCI 151", "001"]).encode()
    payload = base64.urlsafe_b64encode(raw).decode().rstrip("=")

    with pytest.raises(ValueError, match="Invalid subscription link"):
        decode_target(payload)


def test_search_prefers_exact_code_then_bounded_partial(current_snapshot):
    catalog = SubscriptionCatalog(current_snapshot)

    assert [course.course_code for course in catalog.search(" cs 101 ")] == ["CS 101"]
    assert len(catalog.search("cs", limit=1)) == 1


def test_search_deduplicates_course_codes(current_snapshot):
    current_snapshot.courses["CS101-duplicate"] = Course(
        course_code="CS 101",
        department="CS",
        sections={},
        average_fill=0.67,
    )

    catalog = SubscriptionCatalog(current_snapshot)

    assert [course.course_code for course in catalog.search("cs")] == ["CS 101"]


def test_target_validation_uses_latest_snapshot(current_snapshot):
    catalog = SubscriptionCatalog(current_snapshot)

    assert catalog.resolve(SubscriptionTarget("Spring 2024", "CS 101", "10L"))
    assert not catalog.resolve(SubscriptionTarget("Spring 2024", "CS 101", "missing"))
    assert not catalog.resolve(SubscriptionTarget("Fall 2026", "CS 101"))


def test_website_course_import_text_round_trip():
    payload = encode_course_import("Fall 2026", ["MATH 101", "CSCI 115"])

    assert payload == "/import\nFall 2026\nCSCI 115\nMATH 101"
    assert decode_course_import(payload) == (
        "Fall 2026",
        ["CSCI 115", "MATH 101"],
    )


def test_website_course_import_supports_large_pasted_selection():
    courses = [f"COURSE {index}" for index in range(20)]

    payload = encode_course_import("Fall 2026", courses)

    assert decode_course_import(payload) == ("Fall 2026", sorted(courses))


def test_watch_payload_round_trip_resolves_to_the_real_course(current_snapshot):
    target = SubscriptionTarget("Spring 2024", "CS 101")

    payload = encode_watch_payload(target)

    assert payload == "watch_sp24_CS101"
    assert len(payload) <= 64
    assert set(payload) <= set(
        "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
    )
    catalog = SubscriptionCatalog(current_snapshot)
    assert catalog.resolve_link(decode_watch_payload(payload)) == target


def test_watch_payload_round_trip_with_section(current_snapshot):
    target = SubscriptionTarget("Spring 2024", "CS 101", "10L")

    payload = encode_watch_payload(target)

    assert payload == "watch_sp24_CS101_10L"
    catalog = SubscriptionCatalog(current_snapshot)
    assert catalog.resolve_link(decode_watch_payload(payload)) == target


@pytest.mark.parametrize(
    ("semester", "key"),
    [("Fall 2026", "fa26"), ("Spring 2027", "sp27"), ("Summer 2026", "su26")],
)
def test_watch_payload_semester_keys(semester, key):
    assert encode_watch_payload(SubscriptionTarget(semester, "CSCI 115")) == (
        f"watch_{key}_CSCI115"
    )


@pytest.mark.parametrize(
    "target",
    [
        SubscriptionTarget("Winter 2026", "CSCI 115"),
        SubscriptionTarget("Fall 2026", "!!!"),
        SubscriptionTarget("Fall 2026", "A" * 60),
        SubscriptionTarget("Fall 2026", "CSCI 115", "S" * 60),
    ],
)
def test_unrepresentable_or_overlong_targets_are_not_encoded(target):
    with pytest.raises(ValueError, match="Telegram|link"):
        encode_watch_payload(target)


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "start",
        "watch_",
        "watch_xx26_CS101",
        "watch_fa2026_CS101",
        "watch_fa26_cs 101",
    ],
)
def test_malformed_watch_payloads_are_rejected(payload):
    with pytest.raises(ValueError, match="Invalid subscription link"):
        decode_watch_payload(payload)


def test_ambiguous_compacted_codes_do_not_resolve(current_snapshot):
    other = Course(course_code="CS101", department="CS", sections={}, average_fill=0)
    current_snapshot.courses["CS101"] = other
    catalog = SubscriptionCatalog(current_snapshot)

    assert catalog.resolve_link(decode_watch_payload("watch_sp24_CS101")) is None


def test_deep_link_uses_bot_username():
    target = SubscriptionTarget("Fall 2026", "CSCI 115")

    assert watch_deep_link("registrar_bot", target) == (
        "https://t.me/registrar_bot?start=watch_fa26_CSCI115"
    )
    with pytest.raises(ValueError, match="bot username"):
        watch_deep_link("bad name", target)
