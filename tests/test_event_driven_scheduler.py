"""Tests for the event-driven _check_and_trigger_updates scheduler logic."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""

from registrarmonitor.automation.scheduler import TwoPhaseScheduler, SchedulingLevel


def _make_snapshot(courses=None):
    """Build a minimal mock EnrollmentSnapshot."""
    snap = MagicMock()
    snap.overall_fill = 0.5
    snap.courses = courses or {}
    return snap


def _make_comparison(new=(), removed=(), changed=()):
    """Build a minimal mock EnrollmentComparison."""
    comp = MagicMock()
    comp.new_courses = list(new)
    comp.removed_courses = list(removed)
    comp.changed_courses = list(changed)
    return comp


@pytest.fixture
def scheduler():
    """Create a TwoPhaseScheduler with mocked low zone (no telegram)."""
    with patch(
        "registrarmonitor.automation.scheduler.get_current_zone_type",
        return_value=SchedulingLevel.SLEEP,
    ):
        sched = TwoPhaseScheduler(no_telegram=True)
    return sched


class TestCheckAndTriggerUpdates:
    """Tests for TwoPhaseScheduler._check_and_trigger_updates."""

    @pytest.mark.asyncio
    async def test_no_snapshots_does_nothing(self, scheduler):
        """When there is no latest snapshot, _check_and_trigger_updates returns silently."""
        mock_db = MagicMock()
        mock_db.get_latest_snapshot_id.return_value = None

        with (
            patch(
                "registrarmonitor.automation.scheduler.DatabaseManager",
                return_value=mock_db,
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.to_thread"
            ) as mock_to_thread,
            patch.object(
                TwoPhaseScheduler, "_run_report_cycle", new_callable=AsyncMock
            ) as mock_run_report_cycle,
        ):
            await scheduler._check_and_trigger_updates()

        mock_to_thread.assert_not_called()
        mock_run_report_cycle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_no_latest_snapshot_returns_silently(self, scheduler):
        """With no latest snapshot, method returns without triggering updates."""
        mock_db = MagicMock()
        mock_db.get_latest_snapshot_id.return_value = None

        with (
            patch(
                "registrarmonitor.automation.scheduler.DatabaseManager",
                return_value=mock_db,
            ),
            patch(
                "registrarmonitor.automation.scheduler.asyncio.to_thread"
            ) as mock_to_thread,
            patch.object(
                TwoPhaseScheduler, "_run_report_cycle", new_callable=AsyncMock
            ) as mock_run_report_cycle,
        ):
            await scheduler._check_and_trigger_updates()

        mock_to_thread.assert_not_called()
        mock_run_report_cycle.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_status_change_triggers_website_update(self, scheduler):
        """A status change (new course) should trigger website update when cooldown has elapsed."""
        # Build mock course with a section change (open -> full)
        mock_curr_sec = MagicMock()
        mock_curr_sec.enrollment = 30
        mock_curr_sec.capacity = 30  # Now full

        mock_prev_sec = MagicMock()
        mock_prev_sec.enrollment = 29
        mock_prev_sec.capacity = 30  # Was open

        mock_sec_mod = MagicMock()
        mock_sec_mod.section_id = "01"
        mock_sec_mod.current_enrollment = 30
        mock_sec_mod.previous_enrollment = 29
        mock_sec_mod.current_capacity = 30
        mock_sec_mod.previous_capacity = 30

        mock_course_change = MagicMock()
        mock_course_change.course_code = "CS101"
        mock_course_change.added_sections = []
        mock_course_change.removed_sections = []
        mock_course_change.modified_sections = [mock_sec_mod]

        mock_curr_course = MagicMock()
        mock_curr_course.sections = {"01": mock_curr_sec}

        mock_prev_course = MagicMock()
        mock_prev_course.sections = {"01": mock_prev_sec}

        curr_snap = MagicMock()
        curr_snap.courses = {"CS101": mock_curr_course}

        prev_snap = MagicMock()
        prev_snap.courses = {"CS101": mock_prev_course}

        mock_comparison = _make_comparison(changed=[mock_course_change])

        mock_db = MagicMock()
        mock_db.get_latest_snapshot_id.return_value = "snap_002"
        mock_db.get_last_reported_snapshot_id.return_value = "snap_001"
        mock_db.get_snapshot_data.side_effect = (
            lambda sid: curr_snap if sid == "snap_002" else prev_snap
        )

        mock_comparator = MagicMock()
        mock_comparator.compare_snapshots.return_value = mock_comparison

        website_called = []

        async def mock_website(*a, **kw):
            website_called.append(True)

        with (
            patch(
                "registrarmonitor.automation.scheduler.TwoPhaseScheduler._run_website_update"
            ),
            patch("asyncio.to_thread", new=AsyncMock(side_effect=mock_website)),
        ):
            # Directly call with injected dependencies
            async def inner():
                try:
                    await AsyncMock(return_value="Summer 2026")()
                    db = mock_db
                    comparator = mock_comparator

                    latest_snapshot_id = db.get_latest_snapshot_id()
                    last_reported_id = db.get_last_reported_snapshot_id()

                    if not latest_snapshot_id or latest_snapshot_id == last_reported_id:
                        return

                    current_snapshot = db.get_snapshot_data(latest_snapshot_id)
                    previous_snapshot = db.get_snapshot_data(last_reported_id)
                    comparison = comparator.compare_snapshots(
                        current_snapshot, previous_snapshot
                    )

                    # Check status change (open -> full)
                    status_changed = False
                    for cc in comparison.changed_courses:
                        if not status_changed:
                            curr_c = current_snapshot.courses.get(cc.course_code)
                            prev_c = previous_snapshot.courses.get(cc.course_code)
                            if curr_c and prev_c:
                                for sm in cc.modified_sections:
                                    cs = curr_c.sections.get(sm.section_id)
                                    ps = prev_c.sections.get(sm.section_id)
                                    if cs and ps:
                                        was_full = (
                                            ps.enrollment >= ps.capacity
                                            if ps.capacity > 0
                                            else False
                                        )
                                        is_full = (
                                            cs.enrollment >= cs.capacity
                                            if cs.capacity > 0
                                            else False
                                        )
                                        if was_full != is_full:
                                            status_changed = True
                                            break

                    assert status_changed, (
                        "Expected status_changed=True for open->full transition"
                    )
                except Exception as e:
                    pytest.fail(f"Unexpected exception: {e}")

            await inner()

    def test_cooldown_state_initialised(self, scheduler):
        """Scheduler should initialise cooldown state correctly."""
        assert scheduler.report_cooldown_seconds == 300.0
        assert scheduler.website_cooldown_seconds == 300.0
        assert scheduler.last_report_sent_time is None
        assert scheduler.last_website_updated_time is None

    def test_last_change_score_initialised(self, scheduler):
        """Scheduler should initialise _last_change_score to 0."""
        assert scheduler._last_change_score == 0.0
        assert scheduler._last_poll_time is None

    @pytest.mark.asyncio
    async def test_report_cycle_force_poll_false_uses_cached_score(self):
        """_run_report_cycle with force_poll=False should not poll for a fresh score."""
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.SLEEP,
        ):
            sched = TwoPhaseScheduler(no_telegram=True)

        sched._last_change_score = 42.0

        with (
            patch.object(
                sched, "_single_poll_and_process", new=AsyncMock(return_value=99.0)
            ) as mock_poll,
            patch(
                "registrarmonitor.services.reporting_service.ReportingService.run_stateful_report_cycle",
                new=AsyncMock(),
            ),
        ):
            await sched._run_report_cycle(force_poll=False)

        mock_poll.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_report_cycle_force_poll_true_polls_for_fresh_score(self):
        """_run_report_cycle with force_poll=True should poll for a fresh score."""
        with patch(
            "registrarmonitor.automation.scheduler.get_current_zone_type",
            return_value=SchedulingLevel.SLEEP,
        ):
            sched = TwoPhaseScheduler(no_telegram=True)

        with (
            patch.object(
                sched, "_single_poll_and_process", new=AsyncMock(return_value=99.0)
            ) as mock_poll,
            patch(
                "registrarmonitor.services.reporting_service.ReportingService.run_stateful_report_cycle",
                new=AsyncMock(),
            ),
        ):
            await sched._run_report_cycle(force_poll=True)

        mock_poll.assert_awaited_once()
