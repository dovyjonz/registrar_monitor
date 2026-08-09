"""Tests for the scheduler module (beyond heat decay)."""

import time
from zoneinfo import ZoneInfo

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from datetime import datetime
from unittest.mock import AsyncMock, patch

from registrarmonitor.automation.scheduler import (
    SchedulingDecision,
    SchedulingLevel,
    TwoPhaseScheduler,
    get_current_zone_type,
    parse_schedule_file,
)
from registrarmonitor.cli.commands import PollResult
from registrarmonitor.website.config import get_milestones


class TestSchedulingLevel:
    """Tests for SchedulingLevel enum."""

    def test_level_labels(self):
        """Scheduling levels should have expected string labels."""
        assert SchedulingLevel.HOT.label == "hot"
        assert SchedulingLevel.SLEEP.label == "sleep"

    def test_level_intervals(self):
        """Scheduling levels should have expected intervals."""
        assert SchedulingLevel.HOT.interval == 300
        assert SchedulingLevel.SLEEP.interval == 3600

    def test_from_label(self):
        """Should create level from string label."""
        assert SchedulingLevel.from_label("hot") == SchedulingLevel.HOT
        assert SchedulingLevel.from_label("sleep") == SchedulingLevel.SLEEP

    def test_from_score(self):
        """Should create level from activity score."""
        assert SchedulingLevel.from_score(50.0) == SchedulingLevel.HOT
        assert SchedulingLevel.from_score(1.0) == SchedulingLevel.HOT
        assert SchedulingLevel.from_score(0.5) == SchedulingLevel.SLEEP
        assert SchedulingLevel.from_score(0.0) == SchedulingLevel.SLEEP

    def test_is_more_urgent_than(self):
        """Should correctly compare urgency levels."""
        assert SchedulingLevel.HOT.is_more_urgent_than(SchedulingLevel.SLEEP)
        assert not SchedulingLevel.SLEEP.is_more_urgent_than(SchedulingLevel.HOT)


class TestParseScheduleFile:
    """Tests for parse_schedule_file function."""

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_valid_schedule_config(self, mock_get_config):
        """Valid milestones/deadlines should be parsed into correct zones."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "deadlines": [["2026-06-01T23:59:00", "Moderate Deadline"]],
                    "priorities": {"seniors": [["2026-05-15T09:00:00", "Milestone 1"]]},
                }
            }
        }

        result = parse_schedule_file(force_reload=True)

        assert SchedulingLevel.HOT in result
        assert SchedulingLevel.SLEEP in result

        # Milestone 2026-05-15T09:00:00:
        # HOT: milestone - 5 min to milestone + 30 min
        assert len(result[SchedulingLevel.HOT]) == 2

        # first window (Milestone 1):
        assert result[SchedulingLevel.HOT][0] == (
            datetime(2026, 5, 15, 8, 55),
            datetime(2026, 5, 15, 9, 30),
        )

        # second window (Moderate Deadline):
        assert result[SchedulingLevel.HOT][1] == (
            datetime(2026, 6, 1, 23, 54),
            datetime(2026, 6, 2, 0, 29),
        )

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_empty_schedule_config(self, mock_get_config):
        """Empty milestones/deadlines should return empty zones."""
        mock_get_config.return_value = {"semesters": {}}

        result = parse_schedule_file(force_reload=True)

        assert result[SchedulingLevel.HOT] == []
        assert result[SchedulingLevel.SLEEP] == []

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_invalid_milestone_skipped(self, mock_get_config):
        """Invalid date format in milestone should be skipped gracefully."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "priorities": {
                        "seniors": [
                            ["invalid-date", "Milestone 1"],
                            ["2026-05-15T09:00:00", "Milestone 2"],
                        ]
                    }
                }
            }
        }

        result = parse_schedule_file(force_reload=True)

        # Only valid milestone should be parsed
        assert len(result[SchedulingLevel.HOT]) == 1


class TestGetCurrentZoneType:
    """Tests for get_current_zone_type function."""

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_sleep_when_no_schedule(self, mock_get_config):
        """Should return SLEEP when no milestones or deadlines are defined."""
        mock_get_config.return_value = {"semesters": {}}
        result = get_current_zone_type()
        assert result == SchedulingLevel.SLEEP

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_hot_zone_active(self, mock_get_config):
        """Should return HOT when in HOT zone time."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "priorities": {"seniors": [["2026-05-15T09:00:00", "Milestone"]]}
                }
            }
        }
        # HOT zone: 08:55 to 09:30
        mock_now = datetime(2026, 5, 15, 8, 57, 0)

        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch(
            "registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime
        ):
            result = get_current_zone_type()
            assert result == SchedulingLevel.HOT

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_sleep_outside_windows(self, mock_get_config):
        """Should return SLEEP when outside defined zones but zones exist."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {"deadlines": [["2026-06-01T23:59:00", "Deadline"]]}
            }
        }
        mock_now = datetime(2026, 6, 1, 12, 0, 0)

        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch(
            "registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime
        ):
            result = get_current_zone_type()
            assert result == SchedulingLevel.SLEEP

    @pytest.mark.parametrize("process_timezone", ["UTC", "Asia/Almaty"])
    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_registrar_times_are_process_timezone_independent(
        self, mock_get_config, monkeypatch, process_timezone
    ):
        """Schedule decisions and browser timestamps use the registrar timezone."""
        mock_get_config.return_value = {
            "timezone": "Asia/Almaty",
            "semesters": {
                "Fall 2026": {
                    "priorities": {
                        "1": [["2026-08-05T09:00:00", "Y4+"]],
                    }
                }
            },
        }
        instant = datetime(2026, 8, 5, 4, 0, tzinfo=ZoneInfo("UTC")).timestamp()

        monkeypatch.setenv("TZ", process_timezone)
        time.tzset()

        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return cls.fromtimestamp(instant, tz)

        try:
            with patch(
                "registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime
            ):
                assert get_current_zone_type() == SchedulingLevel.HOT

            with patch(
                "registrarmonitor.website.config._load_settings",
                return_value=mock_get_config.return_value,
            ):
                milestone = get_milestones("Fall 2026")[0]
                assert milestone["time"] == ("2026-08-05T09:00:00+05:00")
                assert milestone["priority"] == "1"
        finally:
            monkeypatch.undo()
            time.tzset()


class TestSchedulingDecision:
    """Tests for SchedulingDecision dataclass."""

    def test_to_dict(self):
        """SchedulingDecision should serialize to dictionary."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30),
            change_score=5.0,
            current_heat=10.0,
            baseline_level=SchedulingLevel.SLEEP,
            reactive_level=SchedulingLevel.HOT,
            final_level=SchedulingLevel.HOT,
            final_interval=120,
        )

        result = decision.to_dict()

        assert result["change_score"] == 5.0
        assert result["current_heat"] == 10.0
        assert result["final_level"] == "hot"
        assert result["final_interval_seconds"] == 120
        assert result["baseline_level"] == "sleep"

    def test_timestamp_serialization(self):
        """Timestamp should be serialized as ISO format string."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30, 45),
            change_score=0.0,
            current_heat=0.0,
            baseline_level=SchedulingLevel.SLEEP,
            reactive_level=SchedulingLevel.SLEEP,
            final_level=SchedulingLevel.SLEEP,
            final_interval=3600,
        )

        result = decision.to_dict()

        assert "2024-01-15" in result["timestamp"]
        assert "10:30" in result["timestamp"]


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

        import asyncio

        assert asyncio.run(run_test()) == 12.5
