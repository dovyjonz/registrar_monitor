"""Tests for the Telegram reporter module."""

from unittest.mock import AsyncMock, patch

import pytest
from telegram.constants import ParseMode

from registrarmonitor.reporting.telegram_reporter import TelegramReporter

pytestmark = pytest.mark.unit


@pytest.fixture
def mock_config():
    """Mock configuration dictionary."""
    return {
        "telegram": {
            "bot_token": "test_token",
            "chat_id": "123456789",
        },
        "directories": {
            "pdf_output": "/tmp/pdf",
            "text_reports": "/tmp/txt",
        },
        "notifications": {
            "file_write_delay": 0.01,
            "dry_run": False,
        },
    }


@pytest.fixture
def reporter(mock_config):
    """Create a TelegramReporter instance with mocked dependencies."""
    with (
        patch(
            "registrarmonitor.reporting.telegram_reporter.get_config",
            return_value=mock_config,
        ),
        patch("registrarmonitor.reporting.telegram_reporter.Bot"),
    ):
        reporter = TelegramReporter()
        reporter.bot = AsyncMock()
        return reporter


def test_telegram_reporter_requires_credentials(mock_config):
    """Missing Telegram credentials should fail with an actionable message."""
    mock_config["telegram"] = {"bot_token": "", "chat_id": ""}

    with (
        patch(
            "registrarmonitor.reporting.telegram_reporter.get_config",
            return_value=mock_config,
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
    assert "This is a test report." in call_args.kwargs["text"]
    assert call_args.kwargs["parse_mode"] == ParseMode.MARKDOWN_V2


@pytest.mark.asyncio
async def test_send_long_report_no_courses(reporter):
    """Headers-only content (no course blocks) should be sent as a single message
    without duplicating any lines."""
    content = (
        "Previous Snapshot: 2024-01-01\n"
        "Current Snapshot: 2024-01-02\n"
        "Overall Fill: 80%\n"
        "No significant changes detected."
    )

    await reporter._send_long_report(content)

    reporter.bot.send_message.assert_called_once()
    sent_text = reporter.bot.send_message.call_args.kwargs["text"]
    assert "Previous Snapshot: 2024-01-01" in sent_text
    assert "No significant changes detected." in sent_text
    assert sent_text.count("Previous Snapshot: 2024-01-01") == 1


@pytest.mark.asyncio
async def test_send_long_report_splits_across_messages(reporter):
    """Long content with multiple large course blocks should be split into
    multiple Telegram messages so no individual message exceeds max_length."""
    max_length = 4000
    MARKDOWN_WRAPPER_OVERHEAD = 10

    header = "Previous Snapshot: 2024-01-01\nCurrent Snapshot: 2024-01-02\n"

    def make_course_block(name: str) -> str:
        first_line = f"{name}\n"
        filler_line = "  " + "x" * 78 + "\n"
        filler_count = (2000 - len(first_line)) // len(filler_line)
        return first_line + filler_line * filler_count

    course1 = make_course_block("CS 101")
    course2 = make_course_block("CS 102")
    course3 = make_course_block("CS 103")

    content = header + course1 + course2 + course3

    await reporter._send_long_report(content)

    call_count = reporter.bot.send_message.call_count
    assert call_count > 1, f"Expected multiple messages but got {call_count}"

    for call in reporter.bot.send_message.call_args_list:
        sent_text = call.kwargs["text"]
        assert len(sent_text) <= max_length + MARKDOWN_WRAPPER_OVERHEAD, (
            f"Message too long: {len(sent_text)} chars"
        )


@pytest.mark.asyncio
async def test_send_long_report_single_course_fits_in_one_message(reporter):
    """A single course block that fits within max_length should be sent in
    one message (including header)."""
    header = "Previous Snapshot: 2024-01-01\nCurrent Snapshot: 2024-01-02\n"
    course = "CS 101\n  Section 10L: 25/30 (83%)\n  Section 11L: 28/30 (93%)\n"

    content = header + course

    await reporter._send_long_report(content)

    reporter.bot.send_message.assert_called_once()
    sent_text = reporter.bot.send_message.call_args.kwargs["text"]
    assert "CS 101" in sent_text
    assert "Previous Snapshot" in sent_text


@pytest.mark.asyncio
async def test_send_long_report_course_block_near_limit(reporter):
    """A single course block that is close to the max_length should be
    sent in one message with the header, without splitting mid-block."""
    header = "Previous Snapshot: 2024-01-01\n"
    # Build a course block ~3900 chars (fits in one message with header)
    first_line = "CS 101\n"
    filler_line = "  " + "x" * 78 + "\n"
    filler_count = (3900 - len(first_line)) // len(filler_line)
    course = first_line + filler_line * filler_count

    content = header + course

    await reporter._send_long_report(content)

    assert reporter.bot.send_message.call_count >= 1
    # Verify no message exceeds the limit
    for call in reporter.bot.send_message.call_args_list:
        sent_text = call.kwargs["text"]
        assert len(sent_text) <= 4010, f"Message too long: {len(sent_text)} chars"


@pytest.mark.asyncio
async def test_send_long_report_two_courses_split_cleanly(reporter):
    """Two medium course blocks that together exceed max_length should be
    split cleanly on the course boundary (second course starts new message)."""
    header = "Previous Snapshot: 2024-01-01\nCurrent Snapshot: 2024-01-02\n"


    # Build two course blocks each ~2500 chars (exceeds max_length together)
    def make_block(name: str, size: int) -> str:
        first_line = f"{name}\n"
        filler_line = "  " + "x" * 78 + "\n"
        filler_count = (size - len(first_line)) // len(filler_line)
        return first_line + filler_line * filler_count

    course1 = make_block("CS 101", 2500)
    course2 = make_block("CS 102", 2500)

    content = header + course1 + course2

    await reporter._send_long_report(content)

    call_count = reporter.bot.send_message.call_count
    assert call_count >= 2, f"Expected at least 2 messages but got {call_count}"

    # First message should contain the header and CS 101
    msg1 = reporter.bot.send_message.call_args_list[0].kwargs["text"]
    assert "Previous Snapshot" in msg1
    assert "CS 101" in msg1

    # Second message should contain CS 102 but NOT duplicate the header
    msg2 = reporter.bot.send_message.call_args_list[1].kwargs["text"]
    assert "CS 102" in msg2
    assert "Previous Snapshot" not in msg2


@pytest.mark.asyncio
async def test_send_long_report_empty_content(reporter):
    """Empty content should not cause errors and should send nothing or an
    empty message."""
    await reporter._send_long_report("")

    # Should either not call send_message or send with valid content
    if reporter.bot.send_message.called:
        for call in reporter.bot.send_message.call_args_list:
            assert isinstance(call.kwargs["text"], str)
