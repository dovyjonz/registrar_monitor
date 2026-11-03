"""Tests for TwoPhaseScheduler loop and orchestration methods."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

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

    def test_init(self, scheduler):
        """Verify scheduler starts quiet, with a queue and downloader."""
        assert scheduler.mode == "quiet"
        assert isinstance(scheduler.pending_polls, asyncio.Queue)
        assert scheduler.downloader is not None

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
    async def test_pending_polls_queue_concurrent_producer_consumer(self):
        """Verify that pending_polls is an asyncio.Queue and handles
        concurrent producer-consumer enqueue/dequeue correctly."""
        import asyncio

        scheduler = TwoPhaseScheduler(no_telegram=True)
        assert isinstance(scheduler.pending_polls, asyncio.Queue)

        # Producer: enqueue two mock download tasks with timestamps
        t1 = datetime(2024, 1, 15, 10, 0, 0)
        t2 = datetime(2024, 1, 15, 10, 1, 0)
        task1 = asyncio.create_task(asyncio.sleep(0, result="/tmp/file1.xls"))
        task2 = asyncio.create_task(asyncio.sleep(0, result="/tmp/file2.xls"))

        await scheduler.pending_polls.put((task1, t1))
        await scheduler.pending_polls.put((task2, t2))

        # Consumer: dequeue in FIFO order
        dt1, pt1 = await scheduler.pending_polls.get()
        dt2, pt2 = await scheduler.pending_polls.get()

        assert pt1 == t1
        assert pt2 == t2
        assert await dt1 == "/tmp/file1.xls"
        assert await dt2 == "/tmp/file2.xls"
        assert scheduler.pending_polls.empty()
