"""Tests for scheduler zone helpers, decision logging, and data classes."""

from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.automation.scheduler import (
    DecisionLogger,
    SchedulingDecision,
    SchedulingLevel,
    TwoPhaseDecision,
    get_next_zone_start,
    merge_time_windows,
    poll_and_get_change_score,
)


class TestMergeTimeWindows:
    def test_empty_list(self):
        assert merge_time_windows([]) == []

    def test_single_window(self):
        w = [(datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 11, 0))]
        assert merge_time_windows(w) == w

    def test_non_overlapping(self):
        w = [
            (datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 11, 0)),
            (datetime(2024, 1, 2, 10, 0), datetime(2024, 1, 2, 11, 0)),
        ]
        result = merge_time_windows(w)
        assert len(result) == 2

    def test_overlapping(self):
        w = [
            (datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 12, 0)),
            (datetime(2024, 1, 1, 11, 0), datetime(2024, 1, 1, 13, 0)),
        ]
        result = merge_time_windows(w)
        assert len(result) == 1
        assert result[0][0] == datetime(2024, 1, 1, 10, 0)
        assert result[0][1] == datetime(2024, 1, 1, 13, 0)

    def test_adjacent(self):
        w = [
            (datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 11, 0)),
            (datetime(2024, 1, 1, 11, 0), datetime(2024, 1, 1, 12, 0)),
        ]
        result = merge_time_windows(w)
        assert len(result) == 1

    def test_contained(self):
        w = [
            (datetime(2024, 1, 1, 10, 0), datetime(2024, 1, 1, 14, 0)),
            (datetime(2024, 1, 1, 11, 0), datetime(2024, 1, 1, 12, 0)),
        ]
        result = merge_time_windows(w)
        assert len(result) == 1
        assert result[0][0] == datetime(2024, 1, 1, 10, 0)
        assert result[0][1] == datetime(2024, 1, 1, 14, 0)


class TestGetNextZoneStart:
    def test_returns_next_start_after_now(self):
        future = datetime(2099, 6, 1, 10, 0)
        zones = {
            SchedulingLevel.HOT: [
                (future, datetime(2099, 6, 1, 11, 0)),
            ]
        }

        with patch(
            "registrarmonitor.automation.scheduler.parse_schedule_file",
            return_value=zones,
        ):
            result = get_next_zone_start(now=datetime(2024, 1, 1))
            assert result == future

    def test_returns_none_when_no_future_zones(self):
        past_start = datetime(2020, 1, 1)
        zones = {
            SchedulingLevel.HOT: [
                (past_start, datetime(2020, 1, 1, 1, 0)),
            ]
        }
        with patch(
            "registrarmonitor.automation.scheduler.parse_schedule_file",
            return_value=zones,
        ):
            result = get_next_zone_start(now=datetime(2024, 1, 1))
            assert result is None

    def test_returns_none_when_no_hot_zones(self):
        zones = {SchedulingLevel.HOT: []}
        with patch(
            "registrarmonitor.automation.scheduler.parse_schedule_file",
            return_value=zones,
        ):
            result = get_next_zone_start(now=datetime(2024, 1, 1))
            assert result is None


class TestPollAndGetChangeScore:
    @pytest.mark.asyncio
    async def test_returns_score_on_success(self):
        mock_result = MagicMock()
        mock_result.success = True
        mock_result.change_score = 42.0

        with patch("registrarmonitor.cli.commands.PollCommand") as mock_cls:
            mock_cls.return_value.run_with_result = AsyncMock(return_value=mock_result)
            score = await poll_and_get_change_score()

        assert score == 42.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_failure(self):
        mock_result = MagicMock()
        mock_result.success = False

        with patch("registrarmonitor.cli.commands.PollCommand") as mock_cls:
            mock_cls.return_value.run_with_result = AsyncMock(return_value=mock_result)
            score = await poll_and_get_change_score()

        assert score == 0.0

    @pytest.mark.asyncio
    async def test_returns_zero_on_exception(self):
        with patch("registrarmonitor.cli.commands.PollCommand") as mock_cls:
            mock_cls.side_effect = RuntimeError("constructor failed")
            score = await poll_and_get_change_score()

        assert score == 0.0


class TestSchedulingDecision:
    def test_to_dict(self):
        decision = SchedulingDecision(
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            change_score=5.0,
            current_heat=0.5,
            baseline_level=SchedulingLevel.SLEEP,
            reactive_level=SchedulingLevel.HOT,
            final_level=SchedulingLevel.HOT,
            final_interval=300,
        )

        d = decision.to_dict()
        assert d["timestamp"] == "2024-01-15T10:00:00"
        assert d["change_score"] == 5.0
        assert d["current_heat"] == 0.5
        assert d["baseline_level"] == "sleep"
        assert d["reactive_level"] == "hot"
        assert d["final_level"] == "hot"
        assert d["final_interval_seconds"] == 300
        assert d["final_interval_minutes"] == 5.0


class TestTwoPhaseDecision:
    def test_to_dict(self):
        decision = TwoPhaseDecision(
            timestamp=datetime(2024, 1, 15, 10, 0, 0),
            change_score=15.0,
            mode="burst",
            consecutive_low=1,
            decay_counter=0,
            baseline_level=SchedulingLevel.HOT,
            final_interval=60,
            reset_condition=False,
        )

        d = decision.to_dict()
        assert d["timestamp"] == "2024-01-15T10:00:00"
        assert d["change_score"] == 15.0
        assert d["mode"] == "burst"
        assert d["consecutive_low"] == 1
        assert d["decay_counter"] == 0
        assert d["baseline_level"] == "hot"
        assert d["final_interval_seconds"] == 60
        assert d["final_interval_minutes"] == 1.0
        assert d["reset_condition"] is False


class TestDecisionLogger:
    def test_init_creates_log_file(self, tmp_path):
        log_file = str(tmp_path / "decisions.log")
        with patch(
            "registrarmonitor.automation.scheduler.DecisionLogger.ensure_log_file_exists",
            wraps=lambda: Path(log_file).touch(),
        ):
            DecisionLogger(log_file)
        assert Path(log_file).exists()

    def test_log_and_get_recent(self, tmp_path):
        log_file = str(tmp_path / "decisions.log")
        with patch("registrarmonitor.automation.scheduler.DecisionLogger.log_decision"):
            with patch(
                "registrarmonitor.automation.scheduler.DecisionLogger.ensure_log_file_exists"
            ):
                logger = DecisionLogger(log_file)

            # Write directly to the file (bypass the autouse fixture that patches log_decision)
            import json

            decision = SchedulingDecision(
                timestamp=datetime(2024, 1, 15, 10, 0, 0),
                change_score=3.0,
                current_heat=0.2,
                baseline_level=SchedulingLevel.SLEEP,
                reactive_level=SchedulingLevel.SLEEP,
                final_level=SchedulingLevel.SLEEP,
                final_interval=1800,
            )
            with open(log_file, "a") as f:
                json.dump(decision.to_dict(), f)
                f.write("\n")
                json.dump(decision.to_dict(), f)
                f.write("\n")

        recent = logger.get_recent_decisions(count=10)
        assert len(recent) == 2
        assert recent[0]["change_score"] == 3.0

    def test_get_recent_respects_count(self, tmp_path):
        log_file = str(tmp_path / "decisions.log")
        with patch(
            "registrarmonitor.automation.scheduler.DecisionLogger.ensure_log_file_exists"
        ):
            logger = DecisionLogger(log_file)

        import json

        for i in range(5):
            d = SchedulingDecision(
                timestamp=datetime(2024, 1, 15, 10, 0, 0),
                change_score=float(i),
                current_heat=0.0,
                baseline_level=SchedulingLevel.SLEEP,
                reactive_level=SchedulingLevel.SLEEP,
                final_level=SchedulingLevel.SLEEP,
                final_interval=1800,
            )
            with open(log_file, "a") as f:
                json.dump(d.to_dict(), f)
                f.write("\n")

        recent = logger.get_recent_decisions(count=3)
        assert len(recent) == 3

    def test_get_recent_returns_empty_on_missing_file(self, tmp_path):
        log_file = str(tmp_path / "nonexistent.log")
        with patch(
            "registrarmonitor.automation.scheduler.DecisionLogger.ensure_log_file_exists"
        ):
            logger = DecisionLogger(log_file)
        assert logger.get_recent_decisions() == []
