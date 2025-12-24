"""Tests for the TwoPhaseScheduler."""

from datetime import datetime
import tempfile

import pytest

from registrarmonitor.automation.scheduler import (
    TwoPhaseScheduler,
    TwoPhaseDecision,
    SchedulingLevel,
)


class TestTwoPhaseDecision:
    """Tests for TwoPhaseDecision."""

    def test_to_dict(self):
        """Test decision serialization to dictionary."""
        timestamp = datetime(2024, 1, 15, 9, 30, 0)
        decision = TwoPhaseDecision(
            timestamp=timestamp,
            change_score=15.5,
            mode="burst",
            consecutive_low=0,
            baseline_level=SchedulingLevel.HIGH,
            final_interval=60,
        )

        result = decision.to_dict()

        assert result["timestamp"] == "2024-01-15T09:30:00"
        assert result["change_score"] == 15.5
        assert result["mode"] == "burst"
        assert result["consecutive_low"] == 0
        assert result["baseline_level"] == "high"
        assert result["final_interval_seconds"] == 60
        assert result["final_interval_minutes"] == 1.0


class TestTwoPhaseScheduler:
    """Tests for TwoPhaseScheduler."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with a temporary schedule file."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False) as f:
            f.write("# Empty schedule file\n")
            schedule_file = f.name

        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as log_f:
            log_file = log_f.name

        return TwoPhaseScheduler(schedule_file=schedule_file, log_file=log_file)

    def test_initial_mode_is_quiet(self, scheduler):
        """Test that scheduler starts in quiet mode."""
        assert scheduler.mode == "quiet"
        assert scheduler.consecutive_low == 0

    def test_quiet_mode_stays_quiet_on_low_score(self, scheduler):
        """Test that scheduler stays in quiet mode with low scores."""
        # Score below threshold should keep quiet mode
        interval, decision = scheduler.get_next_poll_interval(5.0)

        assert scheduler.mode == "quiet"
        assert decision.mode == "quiet"
        assert interval >= 300  # Quiet mode interval

    def test_quiet_to_burst_transition(self, scheduler):
        """Test transition from quiet to burst mode on high score."""
        # Score above BURST_ENTRY_THRESHOLD (12.0) should trigger burst
        interval, decision = scheduler.get_next_poll_interval(15.0)

        assert scheduler.mode == "burst"
        assert decision.mode == "burst"
        assert interval <= 60  # Burst mode aggressive interval

    def test_burst_mode_stays_burst_on_high_score(self, scheduler):
        """Test that scheduler stays in burst mode with continued activity."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # Continue with high activity
        interval, decision = scheduler.get_next_poll_interval(10.0)

        assert scheduler.mode == "burst"
        assert scheduler.consecutive_low == 0

    def test_consecutive_low_counter(self, scheduler):
        """Test that consecutive low counter increments correctly."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # First low score
        scheduler.get_next_poll_interval(1.0)  # Below BURST_EXIT_THRESHOLD (2.0)
        assert scheduler.consecutive_low == 1
        assert scheduler.mode == "burst"

        # Second low score
        scheduler.get_next_poll_interval(1.0)
        assert scheduler.consecutive_low == 2
        assert scheduler.mode == "burst"

    def test_burst_to_quiet_transition_after_consecutive_low(self, scheduler):
        """Test transition back to quiet after 3 consecutive low scores."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # Three consecutive low scores (below BURST_EXIT_THRESHOLD of 2.0)
        scheduler.get_next_poll_interval(1.0)
        scheduler.get_next_poll_interval(1.0)
        interval, decision = scheduler.get_next_poll_interval(1.0)

        assert scheduler.mode == "quiet"
        assert scheduler.consecutive_low == 0  # Reset after transition

    def test_consecutive_low_resets_on_activity(self, scheduler):
        """Test that consecutive low counter resets when activity resumes."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # Two low scores
        scheduler.get_next_poll_interval(1.0)
        scheduler.get_next_poll_interval(1.0)
        assert scheduler.consecutive_low == 2

        # Activity resumes (above BURST_EXIT_THRESHOLD)
        scheduler.get_next_poll_interval(5.0)
        assert scheduler.consecutive_low == 0
        assert scheduler.mode == "burst"

    def test_quiet_interval_silent(self, scheduler):
        """Test quiet mode interval for silent state (score < 1)."""
        interval, _ = scheduler.get_next_poll_interval(0.0)
        # silent interval is 1200s, but may be capped by baseline
        assert interval <= 1200

    def test_quiet_interval_idle(self, scheduler):
        """Test quiet mode interval for idle state (score 1-3)."""
        interval, _ = scheduler.get_next_poll_interval(2.0)
        # idle interval is 600s, but may be capped by baseline
        assert interval <= 600

    def test_quiet_interval_active(self, scheduler):
        """Test quiet mode interval for active state (score >= 3)."""
        interval, _ = scheduler.get_next_poll_interval(5.0)
        # active interval is 300s
        assert interval <= 300

    def test_burst_interval_extreme(self, scheduler):
        """Test burst mode interval for extreme scores."""
        # Enter burst mode with extreme score
        interval, _ = scheduler.get_next_poll_interval(30.0)
        assert interval == 15  # extreme burst interval

    def test_burst_interval_high(self, scheduler):
        """Test burst mode interval for high scores."""
        # Enter burst mode
        interval, _ = scheduler.get_next_poll_interval(15.0)
        assert interval <= 60  # high burst interval

    def test_baseline_level_respected(self, scheduler):
        """Test that baseline level from schedule.txt is respected."""
        # In quiet mode with low score, interval should respect baseline
        interval, decision = scheduler.get_next_poll_interval(0.0)

        # Final interval should be min of calculated and baseline
        assert interval <= decision.baseline_level.interval
