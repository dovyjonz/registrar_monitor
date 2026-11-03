"""Tests for scheduler polling workflow."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from registrarmonitor.automation.scheduler import TwoPhaseScheduler
from registrarmonitor.cli.commands import PollResult

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


class TestSchedulerPollResult:
    @patch("registrarmonitor.automation.scheduler.DataDownloader")
    def test_single_poll_uses_structured_poll_result_score(self, downloader_cls):
        downloader_cls.return_value.download = AsyncMock(return_value="data.xlsx")
        scheduler = TwoPhaseScheduler(no_telegram=True)

        async def run_test():
            with patch(
                "registrarmonitor.cli.commands.PollCommand.run_with_result",
                new_callable=AsyncMock,
            ) as run:
                run.return_value = PollResult(
                    success=True,
                    semester="Summer 2026",
                    snapshot_id_before=1,
                    snapshot_id_after=2,
                    changed=True,
                    change_score=12.5,
                )
                return await scheduler._single_poll_and_process()

        assert asyncio.run(run_test()) == 12.5
