"""Tests for main CLI dispatch functions."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.main import (
    async_main,
    cli_main,
    handle_bot_command,
    handle_db_command,
    handle_deploy_command,
    handle_doctor_command,
    handle_poll_command,
    handle_report_command,
    handle_run_command,
    handle_schedule_command,
    handle_status_command,
)

# ── Helpers ───────────────────────────────────────────────────────


def make_args(command: str, **kwargs):
    """Build a simple argparse.Namespace-like object."""
    args = MagicMock()
    args.command = command
    args.debug = False
    for k, v in kwargs.items():
        setattr(args, k, v)
    return args


# ── Handle command tests ──────────────────────────────────────────


class TestHandlePollCommand:
    @pytest.mark.asyncio
    async def test_success_returns_zero(self):
        with patch("registrarmonitor.main.PollCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=True)
            args = make_args("poll", file=None)
            code = await handle_poll_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_failure_returns_one(self):
        with patch("registrarmonitor.main.PollCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=False)
            args = make_args("poll", file=None)
            code = await handle_poll_command(args)

        assert code == 1


class TestHandleReportCommand:
    @pytest.mark.asyncio
    async def test_success_returns_zero(self):
        with patch("registrarmonitor.main.ReportCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=True)
            args = make_args("report", no_telegram=False, stateful=False)
            code = await handle_report_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_failure_returns_one(self):
        with patch("registrarmonitor.main.ReportCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=False)
            args = make_args("report", no_telegram=False, stateful=False)
            code = await handle_report_command(args)

        assert code == 1


class TestHandleRunCommand:
    @pytest.mark.asyncio
    async def test_success_returns_zero(self):
        with patch("registrarmonitor.main.RunCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=True)
            args = make_args("run", no_telegram=False, deploy=False)
            code = await handle_run_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_failure_returns_one(self):
        with patch("registrarmonitor.main.RunCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=False)
            args = make_args("run", no_telegram=False, deploy=False)
            code = await handle_run_command(args)

        assert code == 1


class TestHandleScheduleCommand:
    @pytest.mark.asyncio
    async def test_runs_and_returns_zero(self):
        with patch("registrarmonitor.main.ScheduleCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock()
            args = make_args("schedule", no_telegram=False)
            code = await handle_schedule_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_zero(self):
        with patch("registrarmonitor.main.ScheduleCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(side_effect=KeyboardInterrupt)
            args = make_args("schedule", no_telegram=False)
            code = await handle_schedule_command(args)

        assert code == 0


@pytest.mark.asyncio
async def test_handle_bot_command_runs_runtime():
    with patch(
        "registrarmonitor.subscriptions.runtime.SubscriptionBotRuntime"
    ) as runtime_cls:
        runtime_cls.return_value.run = AsyncMock()
        assert await handle_bot_command(make_args("bot")) == 0

    runtime_cls.return_value.run.assert_awaited_once_with()


class TestHandleDeployCommand:
    @pytest.mark.asyncio
    async def test_success_returns_zero(self):
        with patch("registrarmonitor.main.DeployCommand") as mock_cls:
            mock_cls.return_value.run.return_value = True
            args = make_args(
                "deploy",
                deploy=False,
                semester=None,
                force=False,
                no_minify=False,
                project="registrar-monitor",
                branch=None,
            )
            code = await handle_deploy_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_failure_returns_one(self):
        with patch("registrarmonitor.main.DeployCommand") as mock_cls:
            mock_cls.return_value.run.return_value = False
            args = make_args(
                "deploy",
                deploy=False,
                semester=None,
                force=False,
                no_minify=False,
                project="registrar-monitor",
                branch=None,
            )
            code = await handle_deploy_command(args)

        assert code == 1


class TestHandleDoctorCommand:
    @pytest.mark.asyncio
    async def test_json_report_returns_report_status(self, capsys):
        report = {
            "format": 1,
            "ok": True,
            "summary": {"pass": 1, "warn": 0, "fail": 0},
            "checks": [],
        }
        with patch("registrarmonitor.main.build_doctor_report", return_value=report):
            code = await handle_doctor_command(
                make_args("doctor", json=True, output=None)
            )

        assert code == 0
        assert '"ok": true' in capsys.readouterr().out


class TestHandleStatusCommand:
    @pytest.mark.asyncio
    async def test_found_returns_zero(self):
        with patch("registrarmonitor.main.StatusCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=True)
            args = make_args("status", courses=["CS 101"], semester=None)
            code = await handle_status_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_not_found_returns_one(self):
        with patch("registrarmonitor.main.StatusCommand") as mock_cls:
            mock_cls.return_value.run = AsyncMock(return_value=False)
            args = make_args("status", courses=["CS 999"], semester=None)
            code = await handle_status_command(args)

        assert code == 1


class TestHandleDbCommand:
    @pytest.mark.asyncio
    async def test_stats_success(self):
        with patch("registrarmonitor.main.DatabaseCommands") as mock_cls:
            mock_cls.return_value.stats = AsyncMock(return_value=True)
            args = make_args("db", db_command="stats")
            code = await handle_db_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_cleanup_success(self):
        with patch("registrarmonitor.main.DatabaseCommands") as mock_cls:
            mock_cls.return_value.cleanup = AsyncMock(return_value=True)
            args = make_args("db", db_command="cleanup", keep=50)
            code = await handle_db_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_dedupe_success(self):
        with patch("registrarmonitor.main.DatabaseCommands") as mock_cls:
            mock_cls.return_value.dedupe_instructor_changes = AsyncMock(
                return_value=True
            )
            args = make_args(
                "db", db_command="dedupe-instructor-changes", dry_run=False
            )
            code = await handle_db_command(args)

        assert code == 0

    @pytest.mark.asyncio
    async def test_invalid_db_command_returns_one(self):
        args = make_args("db", db_command="invalid")
        code = await handle_db_command(args)

        assert code == 1


# ── async_main dispatch tests ─────────────────────────────────────


class TestAsyncMain:
    @pytest.mark.asyncio
    async def test_dispatches_poll_command(self):
        with (
            patch("registrarmonitor.main.create_parser") as mock_parser_builder,
            patch("registrarmonitor.main.setup_logging"),
            patch(
                "registrarmonitor.main.handle_poll_command",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_handler,
        ):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = MagicMock(
                command="poll", debug=False, log_level="INFO"
            )
            mock_parser_builder.return_value = mock_parser

            code = await async_main()

        assert code == 0
        mock_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_dispatches_run_command(self):
        with (
            patch("registrarmonitor.main.create_parser") as mock_parser_builder,
            patch("registrarmonitor.main.setup_logging"),
            patch(
                "registrarmonitor.main.handle_run_command",
                new_callable=AsyncMock,
                return_value=0,
            ) as mock_handler,
        ):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = MagicMock(
                command="run", debug=False, log_level="INFO"
            )
            mock_parser_builder.return_value = mock_parser

            code = await async_main()

        assert code == 0
        mock_handler.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_unknown_command_prints_help(self):
        with (
            patch("registrarmonitor.main.create_parser") as mock_parser_builder,
            patch("registrarmonitor.main.setup_logging"),
        ):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = MagicMock(
                command=None, debug=False, log_level="INFO"
            )
            mock_parser_builder.return_value = mock_parser

            code = await async_main()

        assert code == 1
        mock_parser.print_help.assert_called_once()

    @pytest.mark.asyncio
    async def test_keyboard_interrupt_returns_130(self):
        with (
            patch("registrarmonitor.main.create_parser") as mock_parser_builder,
            patch("registrarmonitor.main.setup_logging"),
            patch(
                "registrarmonitor.main.handle_poll_command",
                side_effect=KeyboardInterrupt,
            ),
        ):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = MagicMock(
                command="poll", debug=False, log_level="INFO"
            )
            mock_parser_builder.return_value = mock_parser

            code = await async_main()

        assert code == 130

    @pytest.mark.asyncio
    async def test_unexpected_exception_returns_1(self):
        with (
            patch("registrarmonitor.main.create_parser") as mock_parser_builder,
            patch("registrarmonitor.main.setup_logging"),
            patch(
                "registrarmonitor.main.handle_poll_command",
                side_effect=Exception("boom"),
            ),
        ):
            mock_parser = MagicMock()
            mock_parser.parse_args.return_value = MagicMock(
                command="poll", debug=False, log_level="INFO"
            )
            mock_parser_builder.return_value = mock_parser

            code = await async_main()

        assert code == 1


class TestCliMain:
    def test_calls_sys_exit_with_return_code(self):
        with (
            patch("registrarmonitor.main.asyncio.run", return_value=0),
            patch("registrarmonitor.main.sys.exit") as mock_exit,
        ):
            cli_main()

        mock_exit.assert_called_once_with(0)

    def test_keyboard_interrupt_exits_130(self):
        with (
            patch("registrarmonitor.main.asyncio.run", side_effect=KeyboardInterrupt),
            patch("registrarmonitor.main.sys.exit") as mock_exit,
        ):
            cli_main()

        mock_exit.assert_called_once_with(130)
