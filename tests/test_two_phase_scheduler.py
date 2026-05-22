"""Tests for the TwoPhaseScheduler."""

from datetime import datetime
import tempfile
from unittest.mock import patch

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
            decay_counter=0,
            baseline_level=SchedulingLevel.HOT,
            final_interval=60,
            reset_condition=False,
        )

        result = decision.to_dict()

        assert result["timestamp"] == "2024-01-15T09:30:00"
        assert result["change_score"] == 15.5
        assert result["mode"] == "burst"
        assert result["consecutive_low"] == 0
        assert result["decay_counter"] == 0
        assert result["baseline_level"] == "hot"
        assert result["final_interval_seconds"] == 60
        assert result["final_interval_minutes"] == 1.0
        assert result["reset_condition"] is False


class TestTwoPhaseScheduler:
    """Tests for TwoPhaseScheduler."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with a temporary log file and mock config."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as log_f:
            log_file = log_f.name

        with patch("registrarmonitor.automation.scheduler.get_config") as mock_get_config:
            mock_get_config.return_value = {"semesters": {}}
            yield TwoPhaseScheduler(log_file=log_file)

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
        # First interval after mode change should be 300 due to Reset Condition
        assert interval == 300
        assert decision.reset_condition is True

        # Second poll in burst mode with same score should not trigger mode change, so it uses burst interval
        interval, decision = scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"
        assert interval <= 60  # Burst mode aggressive interval
        assert decision.reset_condition is False

    def test_burst_mode_stays_burst_on_high_score(self, scheduler):
        """Test that scheduler stays in burst mode with continued activity."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
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
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # First low score
        scheduler.get_next_poll_interval(1.0)  # Below BURST_EXIT_THRESHOLD (5.0 in new spec hysteresis)
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
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # Three consecutive low scores (below BURST_EXIT_THRESHOLD of 5.0)
        scheduler.get_next_poll_interval(1.0)
        scheduler.get_next_poll_interval(1.0)
        interval, decision = scheduler.get_next_poll_interval(1.0)

        assert scheduler.mode == "quiet"
        assert scheduler.consecutive_low == 3  # b_n is 3 in the cycle of transition
        assert decision.reset_condition is True  # Mode change triggers reset

        # Next poll: resets consecutive_low to 0 since mode is now quiet
        interval, decision = scheduler.get_next_poll_interval(0.0)
        assert scheduler.consecutive_low == 0

    def test_consecutive_low_resets_on_activity(self, scheduler):
        """Test that consecutive low counter resets when activity resumes."""
        # Enter burst mode
        scheduler.get_next_poll_interval(15.0)
        scheduler.get_next_poll_interval(15.0)
        assert scheduler.mode == "burst"

        # Two low scores
        scheduler.get_next_poll_interval(1.0)
        scheduler.get_next_poll_interval(1.0)
        assert scheduler.consecutive_low == 2

        # Activity resumes (above BURST_EXIT_THRESHOLD of 5.0)
        scheduler.get_next_poll_interval(5.0)
        assert scheduler.consecutive_low == 0
        assert scheduler.mode == "burst"

    def test_quiet_interval_decay_progression(self, scheduler):
        """Test that quiet mode interval starts at 1800s and compounds only when k_n > 1."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # First poll: S_n = 0.0. k_n = 1.
            # Base interval is 1800s. Exponent is max(0, 1 - 1) = 0.
            # i_mode = 1800 * 1.5^0 = 1800s.
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 1800
            assert scheduler.decay_counter == 1

            # Second poll: S_n = 0.0. k_n = 2.
            # Exponent is max(0, 2 - 1) = 1.
            # i_mode = 1800 * 1.5^1 = 2700s.
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 2700
            assert scheduler.decay_counter == 2

            # Third poll: S_n = 0.0. k_n = 3.
            # Exponent is max(0, 3 - 1) = 2.
            # i_mode = 1800 * 1.5^2 = 4050s.
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 4050
            assert scheduler.decay_counter == 3

    def test_quiet_interval_resets_on_non_zero_score(self, scheduler):
        """Test that quiet interval resets back to 300s on a non-zero change score."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # Decay to k_n = 1
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 1800

            # Poll with non-zero change score (e.g., 2.0)
            # Reset condition evaluated: (S_n > 0) and (k_{n-1} > 0) is True!
            interval, decision = scheduler.get_next_poll_interval(2.0)
            assert interval == 300
            assert decision.reset_condition is True
            assert scheduler.decay_counter == 0

    def test_quiet_interval_caps_during_day_and_night(self, scheduler):
        """Test quiet mode caps: 2h (7200s) during day, 4h (14400s) during night."""
        # 1. Day hours cap: 7200s
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # Keep polling with zero score to decay until it hits day cap (7200s)
            for _ in range(6):
                interval, _ = scheduler.get_next_poll_interval(0.0)
            
            assert interval == 7200

        # Create a new scheduler to avoid transition resets from previous state
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as log_f:
            log_file = log_f.name
        with patch("registrarmonitor.automation.scheduler.get_config") as mock_get_config:
            mock_get_config.return_value = {"semesters": {}}
            night_scheduler = TwoPhaseScheduler(log_file=log_file)

        # 2. Night hours cap: 14400s
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 2, 0, 0)

            # Keep polling with zero score to decay until it hits night cap (14400s)
            for _ in range(8):
                interval, _ = night_scheduler.get_next_poll_interval(0.0)
            
            assert interval == 14400

    def test_quiet_interval_resets_on_day_night_transition(self, scheduler):
        """Test that quiet interval resets to 300s when crossing day/night boundary."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            # Start during day (12 PM)
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 1800

            # Move to night (9 PM)
            mock_dt.now.return_value = datetime(2024, 1, 15, 21, 0, 0)
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 300
            assert decision.reset_condition is True

            # Decay again at night
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 1800

            # Move back to day (9 AM)
            mock_dt.now.return_value = datetime(2024, 1, 16, 9, 0, 0)
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 300
            assert decision.reset_condition is True

    def test_burst_interval_extreme(self, scheduler):
        """Test burst mode interval for extreme scores."""
        # Enter burst mode first
        scheduler.get_next_poll_interval(15.0)
        # Now we are in burst mode, poll with extreme score
        interval, _ = scheduler.get_next_poll_interval(30.0)
        assert interval == 10  # extreme burst interval (10s)

    def test_burst_interval_high(self, scheduler):
        """Test burst mode interval for high scores."""
        # Enter burst mode first
        scheduler.get_next_poll_interval(15.0)
        # Now we are in burst mode, poll with high score
        interval, _ = scheduler.get_next_poll_interval(15.0)
        assert interval <= 60  # high burst interval

    def test_baseline_level_respected(self, scheduler):
        """Test that baseline level from schedule file is respected."""
        # Setup scheduler with HOT baseline
        with patch("registrarmonitor.automation.scheduler.get_current_zone_type", return_value=SchedulingLevel.HOT):
            interval, decision = scheduler.get_next_poll_interval(0.0)
            # Should be capped by HOT baseline interval (300)
            assert interval <= 300

    def test_sleep_tier_enforces_long_interval(self):
        """SLEEP baseline must enforce its cap (7200s during day, 14400s during night)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as log_f:
            log_file = log_f.name

        with patch("registrarmonitor.automation.scheduler.get_config") as mock_get_config, \
             patch("registrarmonitor.automation.scheduler.get_current_zone_type",
                   return_value=SchedulingLevel.SLEEP), \
             patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0) # Day hours
            mock_get_config.return_value = {"semesters": {}}
            sched = TwoPhaseScheduler(log_file=log_file)
            
            # Let it decay until it caps (7200s)
            for _ in range(15):
                interval, decision = sched.get_next_poll_interval(0.0)
            
            assert interval == 7200

    def test_milestone_alignment(self, scheduler):
        """Test that get_next_poll_interval shortens the sleep interval to align exactly with an upcoming milestone."""
        now = datetime(2026, 5, 15, 9, 58, 0)
        milestone = datetime(2026, 5, 15, 10, 0, 0) # 2 minutes (120s) in the future

        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt, \
             patch.object(scheduler, "get_all_milestones", return_value=[milestone]):
            mock_dt.now.return_value = now
            
            # Normal calculated quiet interval would be 1800s (since k_n=1)
            # But milestone is at now + 120s, which is within the 1800s window.
            # So the interval should be shortened to 120s!
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 120

