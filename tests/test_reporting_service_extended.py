"""Extended tests for the reporting service (untested pathways)."""

from unittest.mock import AsyncMock, patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.services.reporting_service import ReportingService


class TestGenerateAndSendReports:
    """Tests for generate_and_send_reports."""

    @pytest.mark.asyncio
    async def test_generates_text_report_with_previous_snapshot(
        self, current_snapshot, previous_snapshot
    ):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with (
            patch.object(
                service,
                "_generate_text_report",
                new_callable=AsyncMock,
                return_value="/tmp/report.txt",
            ),
            patch.object(service, "_send_reports_via_telegram", new_callable=AsyncMock),
        ):
            success, files = await service.generate_and_send_reports(
                current_snapshot, previous_snapshot, send_telegram=False
            )

        assert success is True
        assert files == ["/tmp/report.txt"]

    @pytest.mark.asyncio
    async def test_skips_generation_when_no_previous_snapshot(self, current_snapshot):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service, "_generate_text_report", new_callable=AsyncMock
        ) as mock_gen:
            success, files = await service.generate_and_send_reports(
                current_snapshot, None, send_telegram=False
            )

        assert success is True
        assert files == []
        mock_gen.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_via_telegram_when_requested(
        self, current_snapshot, previous_snapshot
    ):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with (
            patch.object(
                service,
                "_generate_text_report",
                new_callable=AsyncMock,
                return_value="/tmp/report.txt",
            ),
            patch.object(
                service, "_send_reports_via_telegram", new_callable=AsyncMock
            ) as mock_send,
        ):
            await service.generate_and_send_reports(
                current_snapshot, previous_snapshot, send_telegram=True
            )

        mock_send.assert_awaited_once_with("/tmp/report.txt")

    @pytest.mark.asyncio
    async def test_raises_report_generation_error_on_failure(
        self, current_snapshot, previous_snapshot
    ):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service,
            "_generate_text_report",
            new_callable=AsyncMock,
            side_effect=Exception("fail"),
        ):
            from registrarmonitor.core.exceptions import ReportGenerationError

            with pytest.raises(ReportGenerationError, match="Report generation failed"):
                await service.generate_and_send_reports(
                    current_snapshot, previous_snapshot
                )
