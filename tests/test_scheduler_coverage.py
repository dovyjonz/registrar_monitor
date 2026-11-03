"""Additional scheduler tests to increase coverage of untested paths."""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import (
    TwoPhaseScheduler,
)


class TestScheduledReportLoop:
    @pytest.mark.asyncio
    async def test_runs_without_forcing_poll_and_honors_no_telegram(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler._get_next_report_time = MagicMock(
            return_value=dt.datetime(2024, 1, 15, 10, 15)
        )

        with (
            patch(
                "registrarmonitor.automation.scheduler._registrar_now",
                return_value=dt.datetime(2024, 1, 15, 10, 10),
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
            return_value=dt.datetime(2024, 1, 15, 10, 15)
        )

        with (
            patch(
                "registrarmonitor.automation.scheduler._registrar_now",
                return_value=dt.datetime(2024, 1, 15, 10, 10),
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


class TestCheckAndTriggerUpdates:
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
                scheduler, "_run_report_cycle_async", new_callable=AsyncMock
            ) as run_report,
        ):
            db_cls.return_value.get_latest_snapshot_id.return_value = 2
            db_cls.return_value.get_last_reported_snapshot_id.return_value = None
            db_cls.return_value.get_first_snapshot_id.return_value = 1
            db_cls.return_value.get_snapshot_data.side_effect = lambda snapshot_id: (
                current_snapshot if snapshot_id == 2 else previous_snapshot
            )

            await scheduler._check_and_trigger_updates()

            assert scheduler._telegram_report_task is not None
            await scheduler._telegram_report_task

        run_report.assert_awaited_once_with(force_poll=False)
        db_cls.return_value.add_reporting_log.assert_not_called()
