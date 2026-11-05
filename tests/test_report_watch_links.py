"""Channel report course headings link to one-tap watches."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from pydantic import SecretStr

from registrarmonitor.models import Course, EnrollmentSnapshot
from registrarmonitor.reporting.telegram_formatting import render_report_chunks
from registrarmonitor.subscriptions.models import SubscriptionTarget
from registrarmonitor.subscriptions.payloads import watch_deep_link

pytestmark = pytest.mark.unit

REPORT = (
    "📅 2026-08-13 12:00:00\n\n"
    "+ CS 101 - COURSE ADDED\n\n"
    "MATH 200 - LAB FULL\n"
    "  🟠 10L :  23/30 (+3)\n\n"
    "\u2212 OLD 100 - COURSE REMOVED"
)


def link_for(code):
    if code in {"CS 101", "MATH 200"}:
        return f"https://t.me/registrar_bot?start=watch_fa26_{code.replace(' ', '')}"
    return None


def test_known_courses_link_their_code_inside_the_bold_heading():
    text = "\n\n".join(render_report_chunks(REPORT, course_link=link_for))

    assert (
        "*\\+ [CS 101](https://t.me/registrar_bot?start=watch_fa26_CS101)"
        " \\- COURSE ADDED*"
    ) in text
    assert (
        "*[MATH 200](https://t.me/registrar_bot?start=watch_fa26_MATH200) \\- LAB FULL*"
    ) in text
    # Removed courses and the timestamp header are never linked.
    assert "*\u2212 OLD 100 \\- COURSE REMOVED*" in text
    assert text.startswith("📅 2026")
    assert "```\n  🟠 10L" in text


def test_without_a_link_factory_headings_are_unchanged():
    assert render_report_chunks(REPORT) == render_report_chunks(
        REPORT, course_link=lambda _code: None
    )


def test_link_url_special_characters_are_escaped():
    text = render_report_chunks(
        "header\n\nCS 101", course_link=lambda _code: "https://t.me/b?x=a)b\\c"
    )[0]

    assert "(https://t.me/b?x=a\\)b\\\\c)" in text


@pytest.fixture
def reporter(tmp_path, make_settings):
    from unittest.mock import patch

    from registrarmonitor.reporting.telegram_reporter import TelegramReporter

    settings = make_settings(
        directories={"text_reports": str(tmp_path)},
        notifications={"dry_run": False},
        telegram_bot_token=SecretStr("t"),
        telegram_chat_id=SecretStr("1"),
    )
    with (
        patch(
            "registrarmonitor.reporting.telegram_reporter.get_settings",
            return_value=settings,
        ),
        patch("registrarmonitor.reporting.telegram_reporter.Bot"),
    ):
        reporter = TelegramReporter()
    reporter.bot = AsyncMock()
    reporter.bot.get_me.return_value = SimpleNamespace(username="registrar_bot")
    return reporter


@pytest.mark.asyncio
async def test_reporter_links_only_courses_in_the_reported_snapshot(reporter, tmp_path):
    path = tmp_path / "r.txt"
    path.write_text("📅 now\n\nCS 101 - 100%\n\nGONE 1", encoding="utf-8")
    snapshot = EnrollmentSnapshot(
        timestamp="t",
        semester="Fall 2026",
        overall_fill=0,
        courses={"CS 101": Course("CS 101", "CS", {}, 0)},
    )

    await reporter.send_text_report(str(path), snapshot=snapshot)
    await reporter.send_text_report(str(path), snapshot=snapshot)

    text = reporter.bot.send_message.await_args.kwargs["text"]
    expected = watch_deep_link(
        "registrar_bot", SubscriptionTarget("Fall 2026", "CS 101")
    )
    assert f"[CS 101]({expected})" in text
    assert "[GONE 1]" not in text
    reporter.bot.get_me.assert_awaited_once()  # username is cached


@pytest.mark.asyncio
async def test_reporter_without_snapshot_sends_plain_headings(reporter, tmp_path):
    path = tmp_path / "r.txt"
    path.write_text("📅 now\n\nCS 101", encoding="utf-8")

    await reporter.send_text_report(str(path))

    assert "t.me" not in reporter.bot.send_message.await_args.kwargs["text"]
    reporter.bot.get_me.assert_not_awaited()
