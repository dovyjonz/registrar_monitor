"""Tests for the scheduler module (beyond heat decay)."""

from datetime import datetime, timedelta
from unittest.mock import patch

from registrarmonitor.automation.scheduler import (
    SchedulingDecision,
    SchedulingLevel,
    get_current_zone_type,
    parse_schedule_file,
)


class TestSchedulingLevel:
    """Tests for SchedulingLevel enum."""

    def test_level_labels(self):
        """Scheduling levels should have expected string labels."""
        assert SchedulingLevel.EXTREME.label == "extreme"
        assert SchedulingLevel.HIGH.label == "high"
        assert SchedulingLevel.MODERATE.label == "moderate"
        assert SchedulingLevel.LOW.label == "low"
        assert SchedulingLevel.SLEEP.label == "sleep"

    def test_level_intervals(self):
        """Scheduling levels should have expected intervals."""
        assert SchedulingLevel.EXTREME.interval == 12
        assert SchedulingLevel.HIGH.interval == 120
        assert SchedulingLevel.MODERATE.interval == 300
        assert SchedulingLevel.LOW.interval == 1200
        assert SchedulingLevel.SLEEP.interval == 3600

    def test_from_label(self):
        """Should create level from string label."""
        assert SchedulingLevel.from_label("extreme") == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_label("high") == SchedulingLevel.HIGH
        assert SchedulingLevel.from_label("moderate") == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_label("low") == SchedulingLevel.LOW
        assert SchedulingLevel.from_label("sleep") == SchedulingLevel.SLEEP

    def test_from_score(self):
        """Should create level from activity score."""
        assert SchedulingLevel.from_score(50.0) == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_score(30.0) == SchedulingLevel.EXTREME
        assert SchedulingLevel.from_score(15.0) == SchedulingLevel.HIGH
        assert SchedulingLevel.from_score(10.0) == SchedulingLevel.HIGH
        assert SchedulingLevel.from_score(5.0) == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_score(1.0) == SchedulingLevel.MODERATE
        assert SchedulingLevel.from_score(0.5) == SchedulingLevel.LOW
        assert SchedulingLevel.from_score(0.0) == SchedulingLevel.LOW

    def test_is_more_urgent_than(self):
        """Should correctly compare urgency levels."""
        assert SchedulingLevel.EXTREME.is_more_urgent_than(SchedulingLevel.HIGH)
        assert SchedulingLevel.HIGH.is_more_urgent_than(SchedulingLevel.MODERATE)
        assert SchedulingLevel.MODERATE.is_more_urgent_than(SchedulingLevel.LOW)
        assert not SchedulingLevel.LOW.is_more_urgent_than(SchedulingLevel.HIGH)


class TestParseScheduleFile:
    """Tests for parse_schedule_file function."""

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_valid_schedule_config(self, mock_get_config):
        """Valid milestones/deadlines should be parsed into correct zones."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "deadlines": [
                        ["2026-06-01T23:59:00", "Moderate Deadline"]
                    ],
                    "priorities": {
                        "seniors": [
                            ["2026-05-15T09:00:00", "Milestone 1"]
                        ]
                    }
                }
            }
        }

        result = parse_schedule_file(force_reload=True)

        assert SchedulingLevel.EXTREME in result
        assert SchedulingLevel.HIGH in result
        assert SchedulingLevel.MODERATE in result

        # Milestone 2026-05-15T09:00:00:
        # extreme: milestone - 5 min to milestone + 10 min
        # high: milestone + 10 min to milestone + 30 min
        assert len(result[SchedulingLevel.EXTREME]) == 1
        assert result[SchedulingLevel.EXTREME][0] == (
            datetime(2026, 5, 15, 8, 55),
            datetime(2026, 5, 15, 9, 10),
        )

        assert len(result[SchedulingLevel.HIGH]) == 1
        assert result[SchedulingLevel.HIGH][0] == (
            datetime(2026, 5, 15, 9, 10),
            datetime(2026, 5, 15, 9, 30),
        )

        # Deadline 2026-06-01T23:59:00:
        # moderate: deadline - 30 min to deadline + 30 min
        assert len(result[SchedulingLevel.MODERATE]) == 1
        assert result[SchedulingLevel.MODERATE][0] == (
            datetime(2026, 6, 1, 23, 29),
            datetime(2026, 6, 2, 0, 29),
        )

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_empty_schedule_config(self, mock_get_config):
        """Empty milestones/deadlines should return empty zones."""
        mock_get_config.return_value = {"semesters": {}}

        result = parse_schedule_file(force_reload=True)

        assert result[SchedulingLevel.EXTREME] == []
        assert result[SchedulingLevel.HIGH] == []
        assert result[SchedulingLevel.MODERATE] == []

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
        assert len(result[SchedulingLevel.EXTREME]) == 1


class TestGetCurrentZoneType:
    """Tests for get_current_zone_type function."""

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_low_when_no_schedule(self, mock_get_config):
        """Should return LOW when no milestones or deadlines are defined."""
        mock_get_config.return_value = {"semesters": {}}
        result = get_current_zone_type()
        assert result == SchedulingLevel.LOW

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_extreme_zone_active(self, mock_get_config):
        """Should return EXTREME when in extreme zone time."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "priorities": {
                        "seniors": [["2026-05-15T09:00:00", "Milestone"]]
                    }
                }
            }
        }
        # Extreme zone: 08:55 to 09:10
        mock_now = datetime(2026, 5, 15, 8, 57, 0)
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch("registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime):
            result = get_current_zone_type()
            assert result == SchedulingLevel.EXTREME

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_high_zone_active(self, mock_get_config):
        """Should return HIGH when in high zone time."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "priorities": {
                        "seniors": [["2026-05-15T09:00:00", "Milestone"]]
                    }
                }
            }
        }
        # High zone: 09:10 to 09:30
        mock_now = datetime(2026, 5, 15, 9, 15, 0)
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch("registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime):
            result = get_current_zone_type()
            assert result == SchedulingLevel.HIGH

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_moderate_zone_active(self, mock_get_config):
        """Should return MODERATE when in moderate zone time."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "deadlines": [["2026-06-01T23:59:00", "Deadline"]]
                }
            }
        }
        # Moderate zone: 23:29 to 00:29
        mock_now = datetime(2026, 6, 1, 23, 40, 0)
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch("registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime):
            result = get_current_zone_type()
            assert result == SchedulingLevel.MODERATE

    @patch("registrarmonitor.automation.scheduler.get_config")
    def test_sleep_outside_windows(self, mock_get_config):
        """Should return SLEEP when outside defined zones but zones exist."""
        mock_get_config.return_value = {
            "semesters": {
                "summer2026": {
                    "deadlines": [["2026-06-01T23:59:00", "Deadline"]]
                }
            }
        }
        mock_now = datetime(2026, 6, 1, 12, 0, 0)
        class MockDateTime(datetime):
            @classmethod
            def now(cls, tz=None):
                return mock_now

        with patch("registrarmonitor.automation.scheduler.datetime.datetime", MockDateTime):
            result = get_current_zone_type()
            assert result == SchedulingLevel.SLEEP


class TestSchedulingDecision:
    """Tests for SchedulingDecision dataclass."""

    def test_to_dict(self):
        """SchedulingDecision should serialize to dictionary."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30),
            change_score=5.0,
            current_heat=10.0,
            baseline_level=SchedulingLevel.LOW,
            reactive_level=SchedulingLevel.HIGH,
            final_level=SchedulingLevel.HIGH,
            final_interval=120,
        )

        result = decision.to_dict()

        assert result["change_score"] == 5.0
        assert result["current_heat"] == 10.0
        assert result["final_level"] == "high"
        assert result["final_interval_seconds"] == 120
        assert result["baseline_level"] == "low"

    def test_timestamp_serialization(self):
        """Timestamp should be serialized as ISO format string."""
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 30, 45),
            change_score=0.0,
            current_heat=0.0,
            baseline_level=SchedulingLevel.LOW,
            reactive_level=SchedulingLevel.LOW,
            final_level=SchedulingLevel.LOW,
            final_interval=1200,
        )

        result = decision.to_dict()

        assert "2024-01-15" in result["timestamp"]
        assert "10:30" in result["timestamp"]
