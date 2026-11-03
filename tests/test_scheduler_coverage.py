"""Additional scheduler tests to increase coverage of untested paths."""

import asyncio
import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import TwoPhaseScheduler


class TestGetNextReportTime:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (
                datetime.datetime(2024, 1, 15, 10, 10),
                datetime.datetime(2024, 1, 15, 10, 15),
            ),
            (
                datetime.datetime(2024, 1, 15, 10, 15),
                datetime.datetime(2024, 1, 15, 10, 45),
            ),
            (
                datetime.datetime(2024, 1, 15, 10, 46),
                datetime.datetime(2024, 1, 15, 11, 15),
            ),
            (
                datetime.datetime(2024, 1, 15, 23, 46),
                datetime.datetime(2024, 1, 16, 0, 15),
            ),
        ],
    )
    def test_returns_next_twice_hourly_deadline(self, now, expected):
        with patch(
            "registrarmonitor.automation.scheduler._registrar_now",
            return_value=now,
        ):
            scheduler = TwoPhaseScheduler(no_telegram=True)
            result = scheduler._get_next_report_time()
        assert result == expected

    def test_uses_configured_registrar_timezone(self):
        aware_now = datetime.datetime(
            2024,
            7,
            15,
            5,
            10,
            tzinfo=datetime.UTC,
        )
        with (
            patch(
                "registrarmonitor.automation.scheduler.datetime.datetime"
            ) as mock_datetime,
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"timezone": "Asia/Almaty"},
            ),
        ):
            mock_datetime.now.return_value = aware_now
            mock_datetime.fromisoformat = datetime.datetime.fromisoformat
            scheduler = TwoPhaseScheduler(no_telegram=True)
            result = scheduler._get_next_report_time()

        assert result == datetime.datetime(2024, 7, 15, 10, 15)


class TestScheduledReportLoop:
    @pytest.mark.asyncio
    async def test_runs_without_forcing_poll_and_honors_no_telegram(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler._get_next_report_time = MagicMock(
            return_value=datetime.datetime(2024, 1, 15, 10, 15)
        )

        with (
            patch(
                "registrarmonitor.automation.scheduler._registrar_now",
                return_value=datetime.datetime(2024, 1, 15, 10, 10),
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.sleep",
                AsyncMock(side_effect=[None, asyncio.CancelledError]),
            ) as sleep,
            patch.object(
                scheduler,
                "_run_report_cycle",
                AsyncMock(),
            ) as report,
        ):
            with pytest.raises(asyncio.CancelledError):
                await scheduler._scheduled_report_loop()

        sleep.assert_any_await(300.0)
        report.assert_awaited_once_with(force_poll=False)

    @pytest.mark.asyncio
    async def test_report_failure_does_not_stop_loop(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler._get_next_report_time = MagicMock(
            return_value=datetime.datetime(2024, 1, 15, 10, 15)
        )

        with (
            patch(
                "registrarmonitor.automation.scheduler._registrar_now",
                return_value=datetime.datetime(2024, 1, 15, 10, 10),
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.sleep",
                AsyncMock(side_effect=[None, None, asyncio.CancelledError]),
            ),
            patch.object(
                scheduler,
                "_run_report_cycle",
                AsyncMock(side_effect=[RuntimeError("boom"), None]),
            ) as report,
        ):
            with pytest.raises(asyncio.CancelledError):
                await scheduler._scheduled_report_loop()

        assert report.await_count == 2

    @pytest.mark.asyncio
    async def test_report_cycles_are_serialized(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        active = 0
        maximum_active = 0

        async def run_unlocked(*, force_poll):
            nonlocal active, maximum_active
            active += 1
            maximum_active = max(maximum_active, active)
            await asyncio.sleep(0)
            active -= 1
            return 0.0

        with patch.object(
            scheduler,
            "_run_report_cycle_unlocked",
            side_effect=run_unlocked,
        ):
            await asyncio.gather(
                scheduler._run_report_cycle(force_poll=False),
                scheduler._run_report_cycle(force_poll=False),
            )

        assert maximum_active == 1


class TestSinglePollAndProcess:
    @pytest.mark.asyncio
    async def test_unchanged_download_skips_parse_and_compare(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                scheduler.downloader, "download", AsyncMock(return_value=None)
            ) as download,
            patch("registrarmonitor.cli.commands.PollCommand") as poll_command,
        ):
            score = await scheduler._single_poll_and_process()
        assert score == 0.0
        download.assert_awaited_once_with(skip_unchanged=True)
        poll_command.assert_not_called()

    @pytest.mark.asyncio
    async def test_failed_processing_makes_the_file_downloadable_again(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        failed = MagicMock(success=False, change_score=9.0)
        with (
            patch.object(
                scheduler.downloader,
                "download",
                AsyncMock(return_value="/tmp/data.xls"),
            ),
            patch.object(scheduler.downloader, "forget_last_download") as forget,
            patch("registrarmonitor.cli.commands.PollCommand") as poll_command,
        ):
            poll_command.return_value.run_with_result = AsyncMock(return_value=failed)
            score = await scheduler._single_poll_and_process()
        assert score == 0.0
        forget.assert_called_once_with()

    @pytest.mark.asyncio
    async def test_successful_poll_returns_score(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.change_score = 7.5
        with (
            patch.object(
                scheduler.downloader,
                "download",
                AsyncMock(return_value="/tmp/data.xls"),
            ),
            patch("registrarmonitor.cli.commands.PollCommand") as mock_cls,
        ):
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
            db_cls.return_value.get_first_snapshot_id.return_value = 1
            await scheduler._check_and_trigger_updates()
            db_cls.return_value.add_reporting_log.assert_called_once_with(
                snapshot_id=1, changes_were_found=False
            )

    @pytest.mark.asyncio
    async def test_first_run_triggers_report_for_retained_history(
        self, current_snapshot, previous_snapshot
    ):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=False)
        with (
            patch.object(
                cli_utils,
                "detect_active_semester",
                new_callable=AsyncMock,
                return_value="Spring 2024",
            ),
            patch("registrarmonitor.automation.scheduler.DatabaseManager") as db_cls,
            patch.object(
                scheduler, "_run_report_cycle", new_callable=AsyncMock
            ) as run_report,
        ):
            db_cls.return_value.get_latest_snapshot_id.return_value = 2
            db_cls.return_value.get_last_reported_snapshot_id.return_value = None
            db_cls.return_value.get_first_snapshot_id.return_value = 1
            db_cls.return_value.get_snapshot_data.side_effect = lambda snapshot_id: (
                current_snapshot if snapshot_id == 2 else previous_snapshot
            )

            await scheduler._check_and_trigger_updates()

            assert scheduler._report_job.task is not None
            await scheduler._report_job.task

        run_report.assert_awaited_once_with(force_poll=False)
        db_cls.return_value.add_reporting_log.assert_not_called()

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
            patch.object(scheduler, "_scheduled_report_loop", new_callable=AsyncMock),
            patch.object(
                scheduler, "get_next_poll_interval", return_value=(60, MagicMock())
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.sleep",
                side_effect=KeyboardInterrupt,
            ),
        ):
            await scheduler.start()

        assert scheduler._last_change_score == 5.0
        assert scheduler._last_poll_time is not None
