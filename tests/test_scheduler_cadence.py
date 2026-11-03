"""Tests for the declarative polling cadence."""

from datetime import datetime, timedelta

import pytest

from registrarmonitor.automation.scheduler import Cadence, Schedule

pytestmark = pytest.mark.unit

NOON = datetime(2024, 1, 15, 12, 0)
NIGHT = datetime(2024, 1, 15, 2, 0)
NO_MILESTONES = Schedule()


def run(cadence, scores, now=NOON, schedule=NO_MILESTONES):
    return [cadence.step(score, now, schedule).interval for score in scores]


def enter_burst(cadence, now=NOON):
    first = cadence.step(15.0, now, NO_MILESTONES)
    assert cadence.mode == "burst"
    assert first.reset  # mode change resets to the 5 minute interval
    assert first.interval == 300


class TestQuietMode:
    def test_base_interval_by_score(self):
        assert Cadence().step(0.0, NOON, NO_MILESTONES).interval == 1800
        assert Cadence().step(1.0, NOON, NO_MILESTONES).interval == 1800
        assert Cadence().step(3.0, NOON, NO_MILESTONES).interval == 900
        assert Cadence().step(5.0, NOON, NO_MILESTONES).interval == 300

    def test_silent_polls_back_off_by_half_again(self):
        assert run(Cadence(), [0.0] * 3) == [1800, 2700, 4050]

    def test_backoff_is_capped_by_day_and_night(self):
        assert run(Cadence(), [0.0] * 6)[-1] == 7200
        assert run(Cadence(), [0.0] * 8, now=NIGHT)[-1] == 14400

    def test_activity_after_silence_resets_to_five_minutes(self):
        cadence = Cadence()
        assert run(cadence, [0.0, 2.0]) == [1800, 300]
        assert cadence.silent_polls == 0

    def test_day_night_change_resets(self):
        cadence = Cadence()
        assert cadence.step(0.0, NOON, NO_MILESTONES).interval == 1800
        night = cadence.step(0.0, datetime(2024, 1, 15, 21, 0), NO_MILESTONES)
        assert (night.interval, night.reset) == (300, True)


class TestBurstMode:
    def test_burst_tiers(self):
        cadence = Cadence()
        enter_burst(cadence)
        assert run(cadence, [30.0, 15.0, 7.0, 6.0]) == [10, 60, 120, 120]

    def test_cooling_down_polls_use_the_slowest_burst_tier(self):
        cadence = Cadence()
        enter_burst(cadence)
        assert run(cadence, [1.0, 1.0]) == [180, 180]

    def test_three_low_polls_leave_burst(self):
        cadence = Cadence()
        enter_burst(cadence)
        run(cadence, [1.0, 1.0])
        assert (cadence.mode, cadence.consecutive_low) == ("burst", 2)
        decision = cadence.step(1.0, NOON, NO_MILESTONES)
        assert cadence.mode == "quiet"
        assert decision.reset

    def test_activity_resets_low_counter(self):
        cadence = Cadence()
        enter_burst(cadence)
        run(cadence, [1.0, 1.0, 5.0])
        assert (cadence.mode, cadence.consecutive_low) == ("burst", 0)

    def test_reevaluating_without_update_keeps_state(self):
        cadence = Cadence()
        enter_burst(cadence)
        cadence.step(1.0, NOON, NO_MILESTONES, update=False)
        assert cadence.consecutive_low == 0


class TestSchedule:
    MILESTONE = datetime(2026, 5, 15, 10, 0)

    def schedule(self):
        return Schedule((self.MILESTONE,))

    def test_hot_window_is_five_before_to_thirty_after(self):
        schedule = self.schedule()
        assert not schedule.in_hot_zone(self.MILESTONE - timedelta(minutes=6))
        assert schedule.in_hot_zone(self.MILESTONE - timedelta(minutes=5))
        assert schedule.in_hot_zone(self.MILESTONE + timedelta(minutes=30))
        assert not schedule.in_hot_zone(self.MILESTONE + timedelta(minutes=31))

    def test_hot_zone_caps_interval_at_five_minutes(self):
        now = self.MILESTONE + timedelta(minutes=10)
        cadence = Cadence()
        cadence.step(0.0, now, self.schedule())
        assert cadence.step(0.0, now, self.schedule()).interval == 300

    def test_interval_is_cut_to_the_next_window_start(self):
        now = self.MILESTONE - timedelta(minutes=25)
        interval = Cadence().step(0.0, now, self.schedule()).interval
        assert interval == 1200  # window opens 5 minutes before the milestone

    def test_interval_aligns_with_milestone_when_inside_window(self):
        now = self.MILESTONE - timedelta(seconds=120)
        assert Cadence().step(0.0, now, self.schedule()).interval == 120

    def test_burst_cadence_survives_hot_zone(self):
        now = self.MILESTONE + timedelta(minutes=10)
        cadence = Cadence()
        enter_burst(cadence, now)
        assert cadence.step(30.0, now, self.schedule()).interval == 10

    def test_from_settings_collects_deadlines_and_priorities(self, make_settings):
        settings = make_settings(
            timezone="Asia/Almaty",
            semesters={
                "Fall 2026": {
                    "deadlines": [("2026-08-26T12:00:00", "Drop")],
                    "priorities": {"1": [("2026-08-05T09:00:00", "Y4+")]},
                },
                "Old": {},
            },
        )
        assert Schedule.from_settings(settings).milestones == (
            datetime(2026, 8, 5, 9, 0),
            datetime(2026, 8, 26, 12, 0),
        )

    def test_from_settings_converts_aware_times_to_registrar_time(self, make_settings):
        settings = make_settings(
            timezone="Asia/Almaty",
            semesters={"S": {"deadlines": [("2026-08-26T07:00:00+00:00", "x")]}},
        )
        assert Schedule.from_settings(settings).milestones == (
            datetime(2026, 8, 26, 12, 0),
        )

    def test_repository_settings_produce_a_schedule(self):
        assert Schedule.from_settings().milestones
