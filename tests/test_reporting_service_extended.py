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
    async def test_skips_telegram_in_debug_mode(
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
                current_snapshot, previous_snapshot, send_telegram=True, debug_mode=True
            )

        mock_send.assert_not_called()

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


class TestGeneratePdfReportOnly:
    """Tests for generate_pdf_report_only."""

    @pytest.mark.asyncio
    async def test_generates_pdf_successfully(self, current_snapshot):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service,
            "_generate_pdf_report",
            new_callable=AsyncMock,
            return_value="/tmp/report.pdf",
        ):
            result = await service.generate_pdf_report_only(current_snapshot)

        assert result == "/tmp/report.pdf"

    @pytest.mark.asyncio
    async def test_returns_none_on_failure(self, current_snapshot):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service,
            "_generate_pdf_report",
            new_callable=AsyncMock,
            side_effect=Exception("fail"),
        ):
            result = await service.generate_pdf_report_only(current_snapshot)

        assert result is None


class TestGenerateComparisonReport:
    """Tests for generate_comparison_report."""

    @pytest.mark.asyncio
    async def test_generates_comparison(self, current_snapshot, previous_snapshot):
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
            return_value="/tmp/comparison.txt",
        ):
            success, path = await service.generate_comparison_report(
                current_snapshot, previous_snapshot
            )

        assert success is True
        assert path == "/tmp/comparison.txt"

    @pytest.mark.asyncio
    async def test_returns_false_on_failure(self, current_snapshot, previous_snapshot):
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
            success, path = await service.generate_comparison_report(
                current_snapshot, previous_snapshot
            )

        assert success is False
        assert path is None


class TestSendExistingReports:
    """Tests for send_existing_reports."""

    @pytest.mark.asyncio
    async def test_sends_existing_report(self):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service, "_send_reports_via_telegram", new_callable=AsyncMock
        ) as mock_send:
            result = await service.send_existing_reports("/tmp/report.txt")

        assert result is True
        mock_send.assert_awaited_once_with("/tmp/report.txt")

    @pytest.mark.asyncio
    async def test_returns_false_on_failure(self):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch.object(
            service,
            "_send_reports_via_telegram",
            new_callable=AsyncMock,
            side_effect=Exception("fail"),
        ):
            result = await service.send_existing_reports("/tmp/report.txt")

        assert result is False


class TestGetAvailableReports:
    """Tests for get_available_reports."""

    def test_returns_empty_when_no_dir(self):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch(
            "registrarmonitor.config.get_config",
            return_value={"directories": {"text_reports": "/nonexistent_dir"}},
        ):
            reports = service.get_available_reports()

        assert reports == []

    def test_returns_report_list(self, tmp_path):
        (tmp_path / "report1.txt").write_text("data")
        (tmp_path / "report2.txt").write_text("data")

        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch(
            "registrarmonitor.config.get_config",
            return_value={"directories": {"text_reports": str(tmp_path)}},
        ):
            reports = service.get_available_reports()

        assert len(reports) == 2
        assert all(r["type"] == "Text" for r in reports)


class TestCleanupOldReports:
    """Tests for cleanup_old_reports."""

    def test_keeps_specified_count(self, tmp_path):
        for i in range(5):
            (tmp_path / f"report{i}.txt").write_text("data")

        for f in sorted(tmp_path.glob("*.txt")):
            f.touch()

        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch(
            "registrarmonitor.config.get_config",
            return_value={"directories": {"text_reports": str(tmp_path)}},
        ):
            deleted = service.cleanup_old_reports(keep_count=3)

        remaining = list(tmp_path.glob("*.txt"))
        assert len(remaining) == 3
        assert deleted == 2

    def test_returns_zero_when_no_reports(self, tmp_path):
        with (
            patch("registrarmonitor.services.reporting_service.DatabaseManager"),
            patch("registrarmonitor.services.reporting_service.SnapshotComparator"),
            patch("registrarmonitor.services.reporting_service.ReportFormatter"),
        ):
            service = ReportingService()

        with patch(
            "registrarmonitor.config.get_config",
            return_value={"directories": {"text_reports": str(tmp_path)}},
        ):
            deleted = service.cleanup_old_reports(keep_count=10)

        assert deleted == 0
