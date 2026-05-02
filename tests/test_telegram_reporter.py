from unittest.mock import AsyncMock, patch

import pytest
from telegram.constants import ParseMode

from registrarmonitor.reporting.telegram_reporter import TelegramReporter


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
        # Mock the bot instance
        reporter.bot = AsyncMock()
        return reporter


@pytest.mark.asyncio
async def test_send_text_report_success(reporter, tmp_path):
    """Test successful text report sending."""
    # Create a dummy text file
    txt_path = tmp_path / "test_report.txt"
    content = "This is a test report."
    txt_path.write_text(content, encoding="utf-8")

    # Run the method
    await reporter.send_text_report(str(txt_path))

    # Verify send_message was called
    reporter.bot.send_message.assert_called_once()

    # Check arguments
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

    # Should be sent exactly once — no duplication of header lines
    reporter.bot.send_message.assert_called_once()
    sent_text = reporter.bot.send_message.call_args.kwargs["text"]
    assert "Previous Snapshot: 2024-01-01" in sent_text
    assert "No significant changes detected." in sent_text
    # Confirm the header is NOT duplicated
    assert sent_text.count("Previous Snapshot: 2024-01-01") == 1


@pytest.mark.asyncio
async def test_send_long_report_splits_across_messages(reporter):
    """Long content with multiple large course blocks should be split into
    multiple Telegram messages so no individual message exceeds max_length."""
    max_length = 4000
    # ``` wrapper adds "```\n" prefix and "\n```" suffix ≈ 8 chars; leave a small buffer
    MARKDOWN_WRAPPER_OVERHEAD = 10

    header = "Previous Snapshot: 2024-01-01\nCurrent Snapshot: 2024-01-02\n"

    # Build three course blocks each ~2000 chars so together they exceed max_length
    def make_course_block(name: str) -> str:
        first_line = f"{name}\n"
        # Indented lines fill the rest of the ~2000-char block
        filler_line = "  " + "x" * 78 + "\n"
        filler_count = (2000 - len(first_line)) // len(filler_line)
        return first_line + filler_line * filler_count

    course1 = make_course_block("CS 101")
    course2 = make_course_block("CS 102")
    course3 = make_course_block("CS 103")

    content = header + course1 + course2 + course3

    await reporter._send_long_report(content)

    call_count = reporter.bot.send_message.call_count
    # Content is large enough to require more than one message
    assert call_count > 1, f"Expected multiple messages but got {call_count}"

    # Every individual message must fit within the Telegram limit
    for call in reporter.bot.send_message.call_args_list:
        sent_text = call.kwargs["text"]
        assert len(sent_text) <= max_length + MARKDOWN_WRAPPER_OVERHEAD, (
            f"Message too long: {len(sent_text)} chars"
        )
