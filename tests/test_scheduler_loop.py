"""Tests for TwoPhaseScheduler loop and orchestration methods."""

import asyncio
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _use_mock_decision_logger(mock_decision_logger):
    """Pull in mock_decision_logger from conftest for this module."""


from registrarmonitor.automation.scheduler import TwoPhaseScheduler, isolated_command


class TestTwoPhaseSchedulerLoop:
    """Tests for TwoPhaseScheduler loop and orchestration."""

    @pytest.fixture
    def scheduler(self, make_settings):
        """Create a scheduler with mocked settings."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_settings",
                return_value=make_settings(semesters={}),
            ),
        ):
            yield TwoPhaseScheduler(no_telegram=True)

    def test_run_website_update(self, make_settings):
        """Generation, then deploy, each run as a capped child process."""
        mock_service = MagicMock()
        mock_service.is_any_semester_active.return_value = True

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_settings",
                return_value=make_settings(
                    website={"generation_timeout_seconds": 7200}, semesters={}
                ),
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=0),
            ) as run,
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        generate, deploy = run.call_args_list
        assert generate.args[0][-2:] == ["registrarmonitor.main", "deploy"]
        assert generate.kwargs["timeout"] == 7200
        assert deploy.args[0][-2] == "-c"
        assert "WebsiteService().deploy()" in deploy.args[0][-1]
        for call in (generate, deploy):
            assert call.kwargs["preexec_fn"] is not None

    def test_run_website_update_generate_failure_skips_deploy(self, make_settings):
        """A failed isolated generation never publishes stale output."""
        mock_service = MagicMock()
        mock_service.is_any_semester_active.return_value = True

        with (
            patch(
                "registrarmonitor.automation.scheduler.get_settings",
                return_value=make_settings(
                    website={"generation_timeout_seconds": 3600}, semesters={}
                ),
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                return_value=mock_service,
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=1),
            ) as run,
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

        run.assert_called_once()  # no deploy child after a failed generation

    def test_run_website_update_failure(self, make_settings):
        """Handles exception from WebsiteService gracefully."""
        with (
            patch(
                "registrarmonitor.automation.scheduler.get_settings",
                return_value=make_settings(semesters={}),
            ),
            patch(
                "registrarmonitor.services.website_service.WebsiteService",
                side_effect=RuntimeError("deploy failed"),
            ),
            patch(
                "registrarmonitor.automation.scheduler.subprocess.run",
                return_value=MagicMock(returncode=0),
            ),
        ):
            sched = TwoPhaseScheduler(no_telegram=True)
            sched._run_website_update()

    @pytest.mark.asyncio
    @pytest.mark.parametrize(("unchanged", "checks"), [(True, 0), (False, 1)])
    async def test_unchanged_poll_in_the_queue_skips_the_update_check(
        self, scheduler, unchanged, checks
    ):
        """Only a poll that stored something looks for reportable changes."""
        download = asyncio.create_task(asyncio.sleep(0, result="/tmp/new.xls"))
        await scheduler.pending_polls.put((download, datetime(2024, 1, 15, 10, 0)))
        consumer = asyncio.create_task(scheduler._process_pending_polls_loop())
        poll = MagicMock(unchanged=unchanged, change_score=1.0)
        with (
            patch.object(scheduler, "_process_file", AsyncMock(return_value=poll)),
            patch.object(scheduler, "_check_and_trigger_updates", AsyncMock()) as check,
        ):
            await asyncio.wait_for(scheduler.pending_polls.join(), 2)
        consumer.cancel()
        assert check.await_count == checks
        assert scheduler._last_change_score == 1.0


class TestIsolatedCommand:
    @staticmethod
    def _which(available):
        return lambda name: f"/usr/bin/{name}" if name in available else None

    def test_scope_with_memory_cap_and_low_priority(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        with patch(
            "registrarmonitor.automation.scheduler.shutil.which",
            self._which({"systemd-run", "nice", "ionice"}),
        ):
            command = isolated_command(["python", "-m", "build"], "600M")

        assert command == [
            "systemd-run",
            "--user",
            "--scope",
            "--quiet",
            "--collect",
            "-p",
            "MemoryMax=600M",
            "-p",
            "CPUWeight=20",
            "-p",
            "IOWeight=20",
            "--",
            "nice",
            "-n",
            "10",
            "ionice",
            "-c",
            "3",
            "python",
            "-m",
            "build",
        ]

    @pytest.mark.parametrize("runtime_dir", [None, ""])
    def test_without_a_user_manager_it_only_lowers_priority(
        self, monkeypatch, runtime_dir
    ):
        if runtime_dir is None:
            monkeypatch.delenv("XDG_RUNTIME_DIR", raising=False)
        else:
            monkeypatch.setenv("XDG_RUNTIME_DIR", runtime_dir)
        with patch(
            "registrarmonitor.automation.scheduler.shutil.which",
            self._which({"systemd-run", "nice", "ionice"}),
        ):
            command = isolated_command(["build"], "600M")
        assert command == ["nice", "-n", "10", "ionice", "-c", "3", "build"]

    def test_runs_directly_when_no_tools_exist(self, monkeypatch):
        monkeypatch.setenv("XDG_RUNTIME_DIR", "/run/user/1000")
        with patch(
            "registrarmonitor.automation.scheduler.shutil.which", lambda name: None
        ):
            assert isolated_command(["build"], "600M") == ["build"]
