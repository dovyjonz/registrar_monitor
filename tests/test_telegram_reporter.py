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
