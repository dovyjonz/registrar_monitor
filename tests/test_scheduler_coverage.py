"""Additional scheduler tests to increase coverage of untested paths."""

import asyncio
import datetime as dt
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import TwoPhaseScheduler

HEARTBEAT = "registrarmonitor.automation.scheduler.send_heartbeat"


class TestGetNextReportTime:
    @pytest.mark.parametrize(
        ("now", "expected"),
        [
            (
                dt.datetime(2024, 1, 15, 10, 10),
                dt.datetime(2024, 1, 15, 10, 15),
            ),
            (
                dt.datetime(2024, 1, 15, 10, 15),
                dt.datetime(2024, 1, 15, 10, 45),
            ),
            (
                dt.datetime(2024, 1, 15, 10, 46),
                dt.datetime(2024, 1, 15, 11, 15),
            ),
            (
                dt.datetime(2024, 1, 15, 23, 46),
                dt.datetime(2024, 1, 16, 0, 15),
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

    def test_uses_configured_registrar_timezone(self, make_settings):
        aware_now = dt.datetime(
            2024,
            7,
            15,
            5,
            10,
            tzinfo=dt.UTC,
        )
        with (
            patch("registrarmonitor.automation.scheduler.dt.datetime") as mock_datetime,
            patch(
                "registrarmonitor.automation.scheduler.get_settings",
                return_value=make_settings(timezone="Asia/Almaty"),
            ),
        ):
            mock_datetime.now.return_value = aware_now
            mock_datetime.fromisoformat = dt.datetime.fromisoformat
            scheduler = TwoPhaseScheduler(no_telegram=True)
            result = scheduler._get_next_report_time()

        assert result == dt.datetime(2024, 7, 15, 10, 15)


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


class TestSinglePollAndProcess:
    @staticmethod
    def _poll(
        *,
        success: bool = True,
        unchanged: bool = False,
        change_score: float = 0.0,
        rows_digest: str | None = None,
    ):
        from registrarmonitor.cli.commands import PollResult

        return PollResult(
            success=success,
            semester="Spring 2024",
            unchanged=unchanged,
            change_score=change_score,
            rows_digest=rows_digest,
        )

    @pytest.mark.asyncio
    async def test_unchanged_rows_are_reported_back_and_keep_the_digest(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        results = [
            self._poll(rows_digest="d1", change_score=4.0),
            self._poll(rows_digest="d1", unchanged=True),
        ]
        with (
            patch.object(
                scheduler.downloader,
                "download",
                AsyncMock(return_value="/tmp/data.xls"),
            ) as download,
            patch("registrarmonitor.cli.commands.PollCommand") as poll_command,
            patch(HEARTBEAT, new_callable=AsyncMock) as heartbeat,
        ):
            run = poll_command.return_value.run_with_result = AsyncMock(
                side_effect=results
            )
            first = await scheduler._single_poll_and_process()
            assert scheduler._latest_poll is results[0]
            second = await scheduler._single_poll_and_process()
        assert (first, second) == (4.0, 0.0)
        download.assert_awaited_with()
        assert run.await_args_list[0].kwargs == {
            "file_path": "/tmp/data.xls",
            "semester": None,
            "previous_digest": None,
        }
        # The second poll knows the first one's rows and semester.
        assert run.await_args_list[1].kwargs["previous_digest"] == "d1"
        assert run.await_args_list[1].kwargs["semester"] == "Spring 2024"
        assert scheduler._latest_poll is None  # nothing new to compare
        assert heartbeat.await_count == 2  # unchanged is still a healthy poll

    @pytest.mark.asyncio
    async def test_failed_processing_keeps_the_previous_digest(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler._rows_digest = "d0"
        failed = self._poll(success=False, change_score=9.0, rows_digest="d1")
        with (
            patch.object(
                scheduler.downloader,
                "download",
                AsyncMock(return_value="/tmp/data.xls"),
            ),
            patch("registrarmonitor.cli.commands.PollCommand") as poll_command,
            patch(HEARTBEAT, new_callable=AsyncMock) as heartbeat,
        ):
            poll_command.return_value.run_with_result = AsyncMock(return_value=failed)
            await scheduler._single_poll_and_process()
        assert scheduler._rows_digest == "d0"  # the same rows are retried
        heartbeat.assert_not_called()

    @pytest.mark.asyncio
    async def test_successful_poll_returns_score(self):
        scheduler = TwoPhaseScheduler(no_telegram=True)
        with (
            patch.object(
                scheduler.downloader,
                "download",
                AsyncMock(return_value="/tmp/data.xls"),
            ),
            patch("registrarmonitor.cli.commands.PollCommand") as mock_cls,
            patch(HEARTBEAT, new_callable=AsyncMock) as heartbeat,
        ):
            mock_cls.return_value.run_with_result = AsyncMock(
                return_value=self._poll(change_score=7.5)
            )
            score = await scheduler._single_poll_and_process()
        assert score == 7.5
        heartbeat.assert_awaited_once()


class TestCheckAndTriggerUpdates:
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


class TestPollHandoff:
    @pytest.mark.asyncio
    async def test_update_check_reuses_the_polls_manager_and_snapshots(
        self, current_snapshot, previous_snapshot
    ):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=True)
        db = MagicMock()
        db.get_latest_snapshot_id.return_value = 2
        db.get_last_reported_snapshot_id.return_value = 1
        scheduler._latest_poll = MagicMock(
            db_manager=db,
            snapshot_id_after=2,
            latest_snapshot=current_snapshot,
            previous_snapshot_id=1,
            previous_snapshot=previous_snapshot,
        )
        with (
            patch.object(
                cli_utils, "detect_active_semester", new_callable=AsyncMock
            ) as detect,
            patch("registrarmonitor.automation.scheduler.DatabaseManager") as db_cls,
        ):
            await scheduler._check_and_trigger_updates()

        detect.assert_not_called()
        db_cls.assert_not_called()
        db.get_snapshot_data.assert_not_called()
        assert scheduler._latest_poll is None

    @pytest.mark.asyncio
    async def test_report_cycle_uses_the_known_semester(self):
        import registrarmonitor.cli.utils as cli_utils

        scheduler = TwoPhaseScheduler(no_telegram=True)
        scheduler._semester = "Spring 2024"
        scheduler._reporting_service_class = MagicMock()
        scheduler._reporting_service_class.return_value.run_stateful_report_cycle = (
            AsyncMock(return_value=False)
        )
        with patch.object(
            cli_utils, "detect_active_semester", new_callable=AsyncMock
        ) as detect:
            await scheduler._run_report_cycle(force_poll=False)

        detect.assert_not_called()
        scheduler._reporting_service_class.assert_called_once_with(
            semester="Spring 2024"
        )

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("no_telegram", "expected"),
        [(True, "not sent"), (False, "generated and sent")],
    )
    async def test_report_message_says_whether_it_was_sent(
        self, capsys, no_telegram, expected
    ):
        scheduler = TwoPhaseScheduler(no_telegram=no_telegram)
        scheduler.reporting_service = MagicMock(
            run_stateful_report_cycle=AsyncMock(return_value=True)
        )

        await scheduler._run_report_cycle(force_poll=False)

        assert expected in capsys.readouterr().out


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
