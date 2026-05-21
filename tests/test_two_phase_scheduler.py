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
            baseline_level=SchedulingLevel.HOT,
            final_interval=60,
        )

        result = decision.to_dict()

        assert result["timestamp"] == "2024-01-15T09:30:00"
        assert result["change_score"] == 15.5
        assert result["mode"] == "burst"
        assert result["consecutive_low"] == 0
        assert result["baseline_level"] == "hot"
        assert result["final_interval_seconds"] == 60
        assert result["final_interval_minutes"] == 1.0


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
        scheduler.get_next_poll_interval(1.0)  # Below BURST_EXIT_THRESHOLD (3.0)
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

        # Three consecutive low scores (below BURST_EXIT_THRESHOLD of 3.0)
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

    def test_quiet_interval_decay_progression(self, scheduler):
        """Test that quiet mode interval starts at 300s and decays by 1.5x on zero change score."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # First poll: quiet_interval starts at 300.0. Since last_is_day is None,
            # day_night_transition is False. It decays by 1.5x to 450.
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 450
            assert scheduler.quiet_interval == 450.0

            # Second poll: consecutive zero change score, decays to 450 * 1.5 = 675
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 675
            assert scheduler.quiet_interval == 675.0

            # Third poll: decays to 675 * 1.5 = 1012.5 -> int is 1012
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 1012
            assert scheduler.quiet_interval == 1012.5

    def test_quiet_interval_resets_on_non_zero_score(self, scheduler):
        """Test that quiet interval resets back to 300s on a non-zero change score."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # Decay once
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 450

            # Poll with non-zero change score (e.g., 2.0)
            interval, _ = scheduler.get_next_poll_interval(2.0)
            assert interval == 300
            assert scheduler.quiet_interval == 300.0

    def test_quiet_interval_caps_during_day_and_night(self, scheduler):
        """Test quiet mode caps: 1h (3600s) during day, 2h (7200s) during night."""
        # 1. Day hours cap: 3600s
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)

            # Keep polling with zero score to decay until it hits day cap (3600s)
            for _ in range(10):
                interval, _ = scheduler.get_next_poll_interval(0.0)
            
            assert interval == 3600
            assert scheduler.quiet_interval == 3600.0

        # Create a new scheduler to avoid transition resets from previous state
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as log_f:
            log_file = log_f.name
        with patch("registrarmonitor.automation.scheduler.get_config") as mock_get_config:
            mock_get_config.return_value = {"semesters": {}}
            night_scheduler = TwoPhaseScheduler(log_file=log_file)

        # 2. Night hours cap: 7200s
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 2, 0, 0)

            # Keep polling with zero score to decay until it hits night cap (7200s)
            for _ in range(15):
                interval, _ = night_scheduler.get_next_poll_interval(0.0)
            
            assert interval == 7200
            assert night_scheduler.quiet_interval == 7200.0

    def test_quiet_interval_resets_on_day_night_transition(self, scheduler):
        """Test that quiet interval resets to 300s when crossing day/night boundary."""
        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            # Start during day (12 PM)
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 450

            # Move to night (9 PM)
            mock_dt.now.return_value = datetime(2024, 1, 15, 21, 0, 0)
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 300
            assert scheduler.quiet_interval == 300.0

            # Decay again at night
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 450

            # Move back to day (9 AM)
            mock_dt.now.return_value = datetime(2024, 1, 16, 9, 0, 0)
            interval, _ = scheduler.get_next_poll_interval(0.0)
            assert interval == 300
            assert scheduler.quiet_interval == 300.0

    def test_burst_interval_extreme(self, scheduler):
        """Test burst mode interval for extreme scores."""
        # Enter burst mode with extreme score
        interval, _ = scheduler.get_next_poll_interval(30.0)
        assert interval == 10  # extreme burst interval (10s)

    def test_burst_interval_high(self, scheduler):
        """Test burst mode interval for high scores."""
        # Enter burst mode
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
        """SLEEP baseline must enforce its cap (3600s during day, 7200s during night)."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".log", delete=False) as log_f:
            log_file = log_f.name

        with patch("registrarmonitor.automation.scheduler.get_config") as mock_get_config, \
             patch("registrarmonitor.automation.scheduler.get_current_zone_type",
                   return_value=SchedulingLevel.SLEEP), \
             patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt:
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0) # Day hours
            mock_get_config.return_value = {"semesters": {}}
            sched = TwoPhaseScheduler(log_file=log_file)
            
            # Let it decay until it caps
            for _ in range(15):
                interval, decision = sched.get_next_poll_interval(0.0)
            
            assert interval == 3600

    def test_milestone_alignment(self, scheduler):
        """Test that get_next_poll_interval shortens the sleep interval to align exactly with an upcoming milestone."""
        now = datetime(2026, 5, 15, 9, 58, 0)
        milestone = datetime(2026, 5, 15, 10, 0, 0) # 2 minutes (120s) in the future

        with patch("registrarmonitor.automation.scheduler.datetime.datetime") as mock_dt, \
             patch.object(scheduler, "get_all_milestones", return_value=[milestone]):
            mock_dt.now.return_value = now
            
            # Normal calculated quiet interval would be 450s (since quiet_interval starts at 300 and decays)
            # But milestone is at now + 120s, which is within the 450s window.
            # So the interval should be shortened to 120s!
            interval, decision = scheduler.get_next_poll_interval(0.0)
            assert interval == 120
