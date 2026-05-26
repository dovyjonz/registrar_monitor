"""Additional scheduler tests to increase coverage of untested paths."""

import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import (
    SchedulingLevel,
    TwoPhaseScheduler,
    get_current_time_str,
    get_next_zone_change,
    is_extreme_zone,
    is_hot_zone,
)


class TestTopLevelHelpers:
    def test_get_current_time_str(self):
        result = get_current_time_str()
        assert len(result) == 19
        assert "-" in result
        assert ":" in result

    def test_is_extreme_zone(self):
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.HOT,
        ):
            assert is_extreme_zone() is True
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.SLEEP,
        ):
            assert is_extreme_zone() is False

    def test_is_hot_zone(self):
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.HOT,
        ):
            assert is_hot_zone() is True
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.SLEEP,
        ):
            assert is_hot_zone() is False

    def test_get_next_zone_change_no_future_events(self):
        now = datetime.datetime(2024, 1, 15, 10, 0)
        zones = {SchedulingLevel.HOT: []}
        with (
            patch(
                "registrarmonitor.automation.scheduler.parse_schedule_file",
                return_value=zones,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
        ):
            with patch("registrarmonitor.automation.scheduler.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = now
                next_time, next_zone = get_next_zone_change()
            assert next_time is None
            assert next_zone == SchedulingLevel.SLEEP

    def test_get_next_zone_change_returns_next_event(self):
        now = datetime.datetime(2024, 1, 15, 9, 0)
        start = datetime.datetime(2024, 1, 15, 10, 0)
        end = datetime.datetime(2024, 1, 15, 11, 0)
        zones = {SchedulingLevel.HOT: [(start, end)]}
        with (
            patch(
                "registrarmonitor.automation.scheduler.parse_schedule_file",
                return_value=zones,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
        ):
            with patch("registrarmonitor.automation.scheduler.datetime") as mock_dt:
                mock_dt.datetime.now.return_value = now
                next_time, next_zone = get_next_zone_change()
            assert next_time == start
            assert next_zone == SchedulingLevel.HOT


class TestGetNextReportTime:
    def test_returns_next_quarter_hour(self):
        with patch("registrarmonitor.automation.scheduler.datetime") as mock_dt:
            mock_now = datetime.datetime(2024, 1, 15, 10, 10, 0)
            mock_dt.datetime.now.return_value = mock_now
            mock_dt.timedelta = datetime.timedelta
            scheduler = TwoPhaseScheduler(no_telegram=True)
            result = scheduler._get_next_report_time()
        assert result.minute in (15, 45)
        assert result > mock_now


class TestShowScheduleStatus:
    def test_runs_without_error(self):
        zones = {SchedulingLevel.HOT: []}
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
            patch(
                "registrarmonitor.automation.scheduler.parse_schedule_file",
                return_value=zones,
            ),
        ):
            scheduler = TwoPhaseScheduler(no_telegram=True)
            scheduler._show_schedule_status()


class TestPrintStatus:
    def test_runs_without_error_no_decisions(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        with patch.object(scheduler.logger, "get_recent_decisions", return_value=[]):
            scheduler.print_status()

    def test_runs_with_decisions(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        with patch.object(
            scheduler.logger,
            "get_recent_decisions",
            return_value=[
                {
                    "timestamp": "2024-01-15T10:00:00",
                    "change_score": 5.0,
                    "mode": "quiet",
                    "final_interval_minutes": 5.0,
                },
            ],
        ):
            scheduler.print_status()


class TestSinglePollAndProcess:
    @pytest.mark.asyncio
    async def test_download_failure_returns_zero(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler.downloader.download = AsyncMock(return_value=None)
        score = await scheduler._single_poll_and_process()
        assert score == 0.0

    @pytest.mark.asyncio
    async def test_successful_poll_returns_score(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.change_score = 7.5
        scheduler.downloader.download = AsyncMock(return_value="/tmp/data.xls")
        with patch("registrarmonitor.cli.commands.PollCommand") as mock_cls:
            mock_cls.return_value.run_with_result = AsyncMock(return_value=mock_result)
            score = await scheduler._single_poll_and_process()
        assert score == 7.5


class TestCheckAndTriggerUpdates:
    @pytest.mark.asyncio
    async def test_no_latest_snapshot_returns(self):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                cli_utils,
                "detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.automation.scheduler.DatabaseManager") as db_cls,
        ):
            db_cls.return_value.get_latest_snapshot_id.return_value = None
            await scheduler._check_and_trigger_updates()

    @pytest.mark.asyncio
    async def test_first_run_sets_baseline(self):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                cli_utils,
                "detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.automation.scheduler.DatabaseManager") as db_cls,
        ):
            db_cls.return_value.get_latest_snapshot_id.return_value = 1
            db_cls.return_value.get_last_reported_snapshot_id.return_value = None
            await scheduler._check_and_trigger_updates()
            db_cls.return_value.add_reporting_log.assert_called_once_with(
                snapshot_id=1, changes_were_found=False
            )

    @pytest.mark.asyncio
    async def test_already_reported_skips(self):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                cli_utils,
                "detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.automation.scheduler.DatabaseManager") as db_cls,
        ):
            db_cls.return_value.get_latest_snapshot_id.return_value = 1
            db_cls.return_value.get_last_reported_snapshot_id.return_value = 1
            await scheduler._check_and_trigger_updates()
            db_cls.return_value.add_reporting_log.assert_not_called()


class TestStartInitialization:
    @pytest.mark.asyncio
    async def test_start_initializes_and_cleans_up(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                scheduler,
                "_single_poll_and_process",
                new_callable=AsyncMock,
                return_value=5.0,
            ),
            patch.object(
                scheduler, "_check_and_trigger_updates", new_callable=AsyncMock
            ),
            patch.object(
                scheduler, "_process_pending_polls_loop", new_callable=AsyncMock
            ),
            patch.object(
                scheduler, "get_next_poll_interval", return_value=(60, MagicMock())
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.sleep",
                side_effect=KeyboardInterrupt,
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.create_subprocess_exec",
                new_callable=AsyncMock,
            ),
        ):
            await scheduler.start()

        assert scheduler._last_change_score == 5.0
        assert scheduler._last_poll_time is not None
