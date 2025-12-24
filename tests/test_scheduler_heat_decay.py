"""Tests for the HybridScheduler heat decay mechanism."""

from unittest.mock import patch

import pytest

from registrarmonitor.automation.scheduler import (
    HybridScheduler,
    SchedulingLevel,
)


@pytest.fixture
def scheduler():
    """Create a scheduler with no schedule file (LOW level always)."""
    with patch(
        "registrarmonitor.automation.scheduler.get_current_zone_type",
        return_value=SchedulingLevel.LOW,
    ):
        return HybridScheduler(schedule_file="nonexistent.txt", heat_decay_factor=0.8)


class TestHeatDecay:
    """Test heat decay behavior."""

    def test_heat_rises_instantly_with_high_score(self, scheduler):
        """Heat should instantly rise to match a high change score."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.LOW,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_next_zone_change",
                return_value=(None, SchedulingLevel.LOW),
            ),
        ):
            # First poll with high activity
            interval, decision = scheduler.get_next_poll_interval(
                last_change_score=50.0
            )

            assert scheduler.current_heat == 50.0
            assert decision.current_heat == 50.0
            assert decision.reactive_level == SchedulingLevel.EXTREME

    def test_heat_decays_slowly(self, scheduler):
        """Heat should decay by 20% each cycle when activity drops to 0."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.LOW,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_next_zone_change",
                return_value=(None, SchedulingLevel.LOW),
            ),
        ):
            # First poll with high activity
            scheduler.get_next_poll_interval(last_change_score=50.0)
            assert scheduler.current_heat == 50.0

            # Second poll with no activity - heat should decay
            scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == 40.0  # 50 * 0.8

            # Third poll with no activity
            scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == 32.0  # 40 * 0.8

            # Fourth poll
            scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == pytest.approx(25.6)  # 32 * 0.8

    def test_heat_maintains_high_level_longer(self, scheduler):
        """Heat should keep us in HIGH level for several cycles after activity."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.LOW,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_next_zone_change",
                return_value=(None, SchedulingLevel.LOW),
            ),
        ):
            # Start with high activity (score = 20, which is HIGH level)
            _, decision1 = scheduler.get_next_poll_interval(last_change_score=20.0)
            assert decision1.reactive_level == SchedulingLevel.HIGH

            # Even with 0 activity, heat keeps us in HIGH for several cycles
            _, decision2 = scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == 16.0  # 20 * 0.8
            assert decision2.reactive_level == SchedulingLevel.HIGH  # Still >= 10

            _, decision3 = scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == 12.8  # 16 * 0.8
            assert decision3.reactive_level == SchedulingLevel.HIGH

            _, decision4 = scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == pytest.approx(10.24)  # 12.8 * 0.8
            assert decision4.reactive_level == SchedulingLevel.HIGH

    def test_new_activity_overrides_decay(self, scheduler):
        """New activity should instantly update heat if higher than decayed value."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.LOW,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_next_zone_change",
                return_value=(None, SchedulingLevel.LOW),
            ),
        ):
            # Initial high activity
            scheduler.get_next_poll_interval(last_change_score=50.0)
            assert scheduler.current_heat == 50.0

            # Lower activity but still significant
            scheduler.get_next_poll_interval(last_change_score=0.0)
            assert scheduler.current_heat == 40.0  # Decayed

            # New burst of activity higher than decayed heat
            scheduler.get_next_poll_interval(last_change_score=60.0)
            assert scheduler.current_heat == 60.0  # New activity wins

    def test_baseline_high_zone_respected(self):
        """HIGH zone baseline should still be respected even with low heat."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_current_zone_type",
                return_value=SchedulingLevel.HIGH,
            ),
            patch(
                "registrarmonitor.automation.scheduler.get_next_zone_change",
                return_value=(None, SchedulingLevel.LOW),
            ),
        ):
            scheduler = HybridScheduler(
                schedule_file="nonexistent.txt", heat_decay_factor=0.8
            )

            # No activity, low heat, but in HIGH zone
            _, decision = scheduler.get_next_poll_interval(last_change_score=0.0)

            # Should be HIGH due to baseline, not LOW
            assert decision.final_level == SchedulingLevel.HIGH
            assert decision.final_interval == 120
