"""Subscription target payloads and latest-snapshot search."""

import base64
import json

import pytest

from registrarmonitor.subscriptions.catalog import SubscriptionCatalog
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import (
    decode_course_import,
    decode_target,
    encode_course_import,
    encode_target,
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
