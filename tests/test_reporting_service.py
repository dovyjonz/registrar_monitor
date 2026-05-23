from unittest.mock import AsyncMock, patch

import pytest

from registrarmonitor.services.reporting_service import ReportingService


def test_reporting_service_defers_telegram_reporter_initialization():
    """ReportingService should not require Telegram credentials until sending."""
    with (
        patch("registrarmonitor.services.reporting_service.DatabaseManager"),
        patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
        patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        patch(
            "registrarmonitor.services.reporting_service.TelegramReporter"
        ) as telegram_cls,
    ):
        service = ReportingService(semester="Summer 2026")

    telegram_cls.assert_not_called()
    assert getattr(service, "_telegram_reporter", None) is None


@pytest.mark.asyncio
async def test_send_reports_initializes_telegram_reporter_when_needed(tmp_path):
    """The Telegram sender is constructed only for an actual send operation."""
    report_path = tmp_path / "report.txt"
    report_path.write_text("report", encoding="utf-8")

    fake_reporter = AsyncMock()

    with (
        patch("registrarmonitor.services.reporting_service.DatabaseManager"),
        patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
        patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        patch(
            "registrarmonitor.services.reporting_service.TelegramReporter",
            return_value=fake_reporter,
        ) as telegram_cls,
    ):
        service = ReportingService(semester="Summer 2026")
        await service._send_reports_via_telegram(str(report_path))

    telegram_cls.assert_called_once_with()
    fake_reporter.send_text_report.assert_awaited_once_with(str(report_path))
