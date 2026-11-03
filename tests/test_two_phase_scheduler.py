"""Tests for the TwoPhaseScheduler."""

import tempfile
from datetime import datetime
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import (
    SchedulingLevel,
    TwoPhaseScheduler,
)


class TestTwoPhaseScheduler:
    """Tests for TwoPhaseScheduler."""

    @pytest.fixture
    def scheduler(self):
        """Create a scheduler with a temporary log file and mock config."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as log_f:
            log_file = log_f.name

        with patch(
            "registrarmonitor.automation.scheduler.get_config"
        ) as mock_get_config:
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

    def test_sleep_tier_enforces_long_interval(self):
        """SLEEP baseline must enforce its cap (7200s during day, 14400s during night)."""
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".log", delete=False
        ) as log_f:
            log_file = log_f.name

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_config"
            ) as mock_get_config,
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.SLEEP,
            ),
            patch("registrarmonitor.automation.scheduler.dt.datetime") as mock_dt,
        ):
            mock_dt.now.return_value = datetime(2024, 1, 15, 12, 0, 0)  # Day hours
            mock_get_config.return_value = {"semesters": {}}
            sched = TwoPhaseScheduler(log_file=log_file)

            # Let it decay until it caps (7200s)
            for _ in range(15):
                interval, decision = sched.get_next_poll_interval(0.0)

            assert interval == 7200
