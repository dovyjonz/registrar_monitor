"""Tests for the Telegram reporter module."""

from unittest.mock import AsyncMock, patch

import pytest
from pydantic import SecretStr
from telegram.constants import ParseMode
from telegram.error import NetworkError

from registrarmonitor.reporting.telegram_reporter import TelegramReporter

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config(make_settings):
    """Settings with a temporary report directory and a short write delay."""
    return make_settings(
        directories={"text_reports": "/tmp/txt"},
        notifications={"file_write_delay": 0.01, "dry_run": False},
    )


@pytest.fixture
def credentials(mock_config):
    return mock_config.model_copy(
        update={
            "telegram_bot_token": SecretStr("test_token"),
            "telegram_chat_id": SecretStr("123456789"),
        }
    )


@pytest.fixture
def reporter(mock_config, credentials):
    """Create a TelegramReporter instance with mocked dependencies."""
    with (
        patch(
            "registrarmonitor.reporting.telegram_reporter.get_settings",
            return_value=credentials,
        ),
        patch("registrarmonitor.reporting.telegram_reporter.Bot"),
    ):
        reporter = TelegramReporter()
        reporter.bot = AsyncMock()
        return reporter


def test_telegram_reporter_requires_credentials(mock_config):
    """Missing Telegram credentials should fail with an actionable message."""
    missing = mock_config.model_copy(
        update={"telegram_bot_token": None, "telegram_chat_id": None}
    )

    with (
        patch(
            "registrarmonitor.reporting.telegram_reporter.get_settings",
            return_value=missing,
        ),
        patch("registrarmonitor.reporting.telegram_reporter.Bot"),
        pytest.raises(ValueError, match="TELEGRAM_BOT_TOKEN"),
    ):
        TelegramReporter()


@pytest.mark.asyncio
async def test_send_text_report_success(reporter, tmp_path):
    """Test successful text report sending."""
    txt_path = tmp_path / "test_report.txt"
    content = "This is a test report."
    txt_path.write_text(content, encoding="utf-8")

    await reporter.send_text_report(str(txt_path))

    reporter.bot.send_message.assert_called_once()
    call_args = reporter.bot.send_message.call_args
    assert call_args.kwargs["chat_id"] == "123456789"
    assert call_args.kwargs["text"] == "This is a test report\\."
    assert call_args.kwargs["parse_mode"] == ParseMode.MARKDOWN_V2


@pytest.mark.asyncio
async def test_send_text_report_propagates_telegram_failure(reporter, tmp_path):
    """The report cycle must not log success when Telegram rejects the send."""
    txt_path = tmp_path / "test_report.txt"
    txt_path.write_text("report", encoding="utf-8")
    reporter.bot.send_message.side_effect = NetworkError("unavailable")

    with pytest.raises(NetworkError, match="unavailable"):
        await reporter.send_text_report(str(txt_path))


@pytest.mark.asyncio
async def test_complete_report_uses_normal_headings_and_monospace_numeric_rows(
    reporter, tmp_path
):
    txt_path = tmp_path / "test_report.txt"
    txt_path.write_text(
        "📅 2026-08-13 12:00:00 · P2 · Y3\n\n"
        "+ CS 101 - COURSE ADDED\n\n"
        "MATH 200 - LAB FULL\n"
        "  + 2R     3/25 - SECTION ADDED\n"
        "  🟠 10L :  23/30 (+3)\n"
        "  − 1B                  - SECTION REMOVED\n",
        encoding="utf-8",
    )

    await reporter.send_text_report(str(txt_path))

    sent_text = reporter.bot.send_message.call_args.kwargs["text"]
    assert sent_text == (
        "📅 2026\\-08\\-13 12:00:00 · P2 · Y3\n\n"
        "*\\+ CS 101 \\- COURSE ADDED*\n\n"
        "*MATH 200 \\- LAB FULL*\n"
        "```\n"
        "  + 2R     3/25 - SECTION ADDED\n"
        "  🟠 10L :  23/30 (+3)\n"
        "  − 1B                  - SECTION REMOVED\n"
        "```"
    )
    assert not sent_text.startswith("```")


@pytest.mark.asyncio
async def test_send_long_report_splits_across_messages(reporter):
    """Long content with multiple large course blocks should be split into
    multiple Telegram messages so no individual message exceeds max_length."""
    max_length = 4000

    header = "Previous Snapshot: 2024-01-01\nCurrent Snapshot: 2024-01-02\n"

    def make_course_block(name: str) -> str:
        first_line = f"{name}\n"
        filler_line = "  " + "x" * 78 + "\n"
        filler_count = (2000 - len(first_line)) // len(filler_line)
        return first_line + filler_line * filler_count

    course1 = make_course_block("CS 101")
    course2 = make_course_block("CS 102")
    course3 = make_course_block("CS 103")

    content = f"{header}\n{course1}\n{course2}\n{course3}"

    await reporter._send_long_report(content)

    call_count = reporter.bot.send_message.call_count
    assert call_count > 1, f"Expected multiple messages but got {call_count}"

    for call in reporter.bot.send_message.call_args_list:
        sent_text = call.kwargs["text"]
        assert len(sent_text) <= max_length, f"Message too long: {len(sent_text)} chars"
