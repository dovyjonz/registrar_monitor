
import asyncio
import os
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

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
    with patch("registrarmonitor.reporting.telegram_reporter.get_config", return_value=mock_config), \
         patch("registrarmonitor.reporting.telegram_reporter.Bot") as MockBot:

        reporter = TelegramReporter()
        # Mock the bot instance
        reporter.bot = AsyncMock()
        return reporter

@pytest.mark.asyncio
async def test_send_pdf_report_success(reporter, tmp_path):
    """Test successful PDF report sending."""
    # Create a dummy PDF file
    pdf_path = tmp_path / "test_report.pdf"
    content = b"%PDF-1.4 dummy content"
    pdf_path.write_bytes(content)

    # Run the method
    await reporter.send_pdf_report(str(pdf_path))

    # Verify send_document was called
    reporter.bot.send_document.assert_called_once()

    # Check arguments
    call_args = reporter.bot.send_document.call_args
    assert call_args.kwargs["chat_id"] == "123456789"
    assert call_args.kwargs["filename"] == "test_report.pdf"
    assert call_args.kwargs["parse_mode"] == ParseMode.MARKDOWN_V2

    # Verify document argument
    # In the current implementation, it passes an open file object
    document_arg = call_args.kwargs["document"]
    # It should be a file object (has read method) or bytes
    if hasattr(document_arg, "read"):
        # Check if it's closed (it should be closed after the 'with open' block exits)
        assert document_arg.closed
        # We can't easily check content of a closed file object, but we know it was passed.
    else:
        # If implementation changes to bytes, this will handle it
        assert document_arg == content

@pytest.mark.asyncio
async def test_send_pdf_report_file_not_found(reporter):
    """Test handling of missing PDF file."""
    # Run with non-existent file
    await reporter.send_pdf_report("/path/to/nonexistent.pdf")

    # Verify send_document was NOT called
    reporter.bot.send_document.assert_not_called()

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
