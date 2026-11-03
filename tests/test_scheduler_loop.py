"""Tests for TwoPhaseScheduler loop and orchestration methods."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import TwoPhaseScheduler


class TestTwoPhaseSchedulerLoop:
    """Tests for TwoPhaseScheduler loop and orchestration."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with mocked config and zone."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"semesters": {}},
            ),
        ):
            yield TwoPhaseScheduler(no_telegram=True)

    def test_run_website_update(self):
        """Generates in a child process before deploying from the scheduler."""
        mock_service = MagicMock()
        mock_service.is_any_semester_active.return_value = True

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={
                    "website": {
                        "pages_project_name": "test-project",
                        "generation_timeout_seconds": 7200,
                    },
                    "semesters": {},
                },
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=0),
            ) as run,
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        command = run.call_args.args[0]
        assert command[-2:] == ["registrarmonitor.main", "deploy"]
        assert run.call_args.kwargs["timeout"] == 7200
        mock_service.deploy.assert_called_once_with(project_name="test-project")

    def test_run_website_update_generate_failure_skips_deploy(self):
        """A failed isolated generation never publishes stale output."""
        mock_service = MagicMock()
        mock_service.is_any_semester_active.return_value = True

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={
                    "website": {"generation_timeout_seconds": 3600},
                    "semesters": {},
                },
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=1),
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        mock_service.deploy.assert_not_called()

    def test_run_website_update_failure(self):
        """Handles exception from WebsiteService gracefully."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"semesters": {}},
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                side_effect=RuntimeError("deploy failed"),
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        ("downloaded", "heartbeats"), [(None, 1), ("/tmp/new.xls", 0)]
    )
    async def test_unchanged_poll_in_the_queue_sends_heartbeat(
        self, scheduler, downloaded, heartbeats
    ):
        """A scheduled poll that finds the file unchanged is still healthy."""
        download = asyncio.create_task(asyncio.sleep(0, result=downloaded))
        await scheduler.pending_polls.put((download, datetime(2024, 1, 15, 10, 0)))
        consumer = asyncio.create_task(scheduler._process_pending_polls_loop())
        with (
            patch(
                "registrarmonitor.automation.scheduler.send_heartbeat",
                new_callable=AsyncMock,
            ) as heartbeat,
            patch.object(scheduler, "_process_file", AsyncMock(return_value=1.0)),
            patch.object(scheduler, "_check_and_trigger_updates", AsyncMock()),
        ):
            await asyncio.wait_for(scheduler.pending_polls.join(), 2)
        consumer.cancel()
        assert heartbeat.await_count == heartbeats
