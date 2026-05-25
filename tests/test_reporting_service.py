from unittest.mock import AsyncMock, MagicMock, patch

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


@pytest.mark.asyncio
async def test_stateful_report_cycle_honors_no_telegram(
    current_snapshot, previous_snapshot
):
    """Stateful reporting should still generate a report but skip Telegram."""
    fake_db = MagicMock()
    fake_db.get_latest_snapshot_id.return_value = 2
    fake_db.get_last_reported_snapshot_id.return_value = 1
    fake_db.get_snapshot_data.side_effect = lambda snapshot_id: (
        current_snapshot if snapshot_id == 2 else previous_snapshot
    )
    fake_db.add_reporting_log.return_value = None

    with (
        patch(
            "registrarmonitor.services.reporting_service.DatabaseManager",
            return_value=fake_db,
        ),
        patch(
            "registrarmonitor.services.reporting_service.TelegramReporter"
        ) as telegram_cls,
    ):
        service = ReportingService(semester="Spring 2024")
        with patch.object(
            service,
            "generate_and_send_reports",
            new_callable=AsyncMock,
            return_value=(True, ["report.txt"]),
        ) as generate_and_send_reports:
            assert await service.run_stateful_report_cycle(send_telegram=False) is True

    generate_and_send_reports.assert_awaited_once()
    assert generate_and_send_reports.await_args.kwargs["send_telegram"] is False
    telegram_cls.assert_not_called()


@pytest.mark.asyncio
async def test_stateful_first_run_sets_baseline_without_sending():
    fake_db = MagicMock()
    fake_db.get_latest_snapshot_id.return_value = 2
    fake_db.get_last_reported_snapshot_id.return_value = None
    fake_db.add_reporting_log.return_value = None

    with (
        patch(
            "registrarmonitor.services.reporting_service.DatabaseManager",
            return_value=fake_db,
        ),
        patch(
            "registrarmonitor.services.reporting_service.TelegramReporter"
        ) as telegram_cls,
    ):
        service = ReportingService(semester="Spring 2024")
        with patch.object(
            service, "generate_and_send_reports", new_callable=AsyncMock
        ) as generate_and_send_reports:
            assert await service.run_stateful_report_cycle(send_telegram=True) is False

    generate_and_send_reports.assert_not_awaited()
    telegram_cls.assert_not_called()
