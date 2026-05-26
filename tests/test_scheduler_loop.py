"""Tests for TwoPhaseScheduler loop and orchestration methods."""

import asyncio
from datetime import datetime
from unittest.mock import MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import (
    SchedulingLevel,
    TwoPhaseScheduler,
)


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
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
        ):
            yield TwoPhaseScheduler(no_telegram=True)

    def test_init(self, scheduler):
        """Verify scheduler starts in quiet mode, has queue and downloader."""
        assert scheduler.mode == "quiet"
        assert scheduler.consecutive_low == 0
        assert scheduler.decay_counter == 0
        assert isinstance(scheduler.pending_polls, asyncio.Queue)
        assert hasattr(scheduler, "downloader")
        assert scheduler.downloader is not None

    def test_get_baseline_level(self, scheduler):
        """Delegates to get_current_zone_type."""
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.HOT,
        ):
            assert scheduler._get_baseline_level() == SchedulingLevel.HOT

        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.SLEEP,
        ):
            assert scheduler._get_baseline_level() == SchedulingLevel.SLEEP

    def test_quiet_interval(self, scheduler):
        """Interval tiers in quiet mode based on score."""
        assert scheduler._quiet_interval(5) == 300
        assert scheduler._quiet_interval(10) == 300
        assert scheduler._quiet_interval(2) == 900
        assert scheduler._quiet_interval(3) == 900
        assert scheduler._quiet_interval(0) == 1800
        assert scheduler._quiet_interval(1) == 1800

    def test_burst_interval(self, scheduler):
        """Interval tiers in burst mode based on score."""
        assert scheduler._burst_interval(25) == 10
        assert scheduler._burst_interval(50) == 10
        assert scheduler._burst_interval(12) == 60
        assert scheduler._burst_interval(18) == 60
        assert scheduler._burst_interval(5) == 120
        assert scheduler._burst_interval(8) == 120
        assert scheduler._burst_interval(0) == 180
        assert scheduler._burst_interval(3) == 180

    def test_get_all_milestones(self):
        """Reads milestones from config, returns sorted unique datetimes."""
        config = {
            "semesters": {
                "Spring 2026": {
                    "priorities": {
                        "freshmen": [
                            ["2026-01-15T10:00:00", "Priority 1"],
                            ["2026-01-16T14:30:00", "Priority 2"],
                        ],
                    },
                    "deadlines": [
                        ["2026-01-10T08:00:00", "Registration opens"],
                    ],
                },
            },
        }
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value=config,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            milestones = sched.get_all_milestones()

        assert len(milestones) == 3
        assert milestones[0] == datetime(2026, 1, 10, 8, 0, 0)
        assert milestones[1] == datetime(2026, 1, 15, 10, 0, 0)
        assert milestones[2] == datetime(2026, 1, 16, 14, 30, 0)

    def test_get_all_milestones_empty(self):
        """Empty config returns empty list."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"semesters": {}},
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            assert sched.get_all_milestones() == []

    def test_run_website_update(self):
        """Calls generate and deploy on WebsiteService."""
        mock_service = MagicMock()
        mock_service.generate.return_value = True
        mock_service.last_generation_skipped = False

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={
                    "website": {"pages_project_name": "test-project"},
                    "semesters": {},
                },
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        mock_service.generate.assert_called_once_with(minify=True)
        mock_service.deploy.assert_called_once_with(project_name="test-project")

    def test_run_website_update_generate_false_skips_deploy(self):
        """When generate returns False, deploy is not called."""
        mock_service = MagicMock()
        mock_service.generate.return_value = False

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"semesters": {}},
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        mock_service.generate.assert_called_once_with(minify=True)
        mock_service.deploy.assert_not_called()

    def test_run_website_update_failure(self):
        """Handles exception from WebsiteService gracefully."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config",
                return_value={"semesters": {}},
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                side_effect=RuntimeError("deploy failed"),
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
