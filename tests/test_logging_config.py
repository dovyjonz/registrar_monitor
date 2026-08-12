"""Tests for logging configuration module."""

import logging
from unittest.mock import patch

import pytest

pytestmark = pytest.mark.unit

from registrarmonitor.core.logging_config import (
    ColoredFormatter,
    get_logger,
    log_method_calls,
    log_performance,
    setup_logging,
)


@pytest.fixture(autouse=True)
def _reset_logging_state():
    yield
    # Reset global logging state to avoid leaking to other tests
    from registrarmonitor.core import logging_config as lc

    lc._logging_setup_done = False
    root = logging.getLogger()
    root.handlers.clear()


class TestColoredFormatter:
    def test_adds_color_to_levelname(self):
        formatter = ColoredFormatter("%(levelname)s")
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        # Should contain ANSI color code for INFO (green)
        assert "\033[32m" in formatted
        assert "\033[0m" in formatted
        assert record.levelname == "INFO"

    def test_does_not_color_unknown_level(self):
        formatter = ColoredFormatter("%(levelname)s")
        record = logging.LogRecord(
            name="test",
            level=logging.DEBUG + 100,
            pathname="",
            lineno=0,
            msg="test",
            args=(),
            exc_info=None,
        )
        formatted = formatter.format(record)
        assert "\033[" not in formatted


class TestSetupLogging:
    def test_guard_prevents_duplicate_setup(self):
        with patch("registrarmonitor.core.logging_config._logging_setup_done", False):
            setup_logging(force_setup=True)
            # Call again without force - should be no-op
            with patch(
                "registrarmonitor.core.logging_config.logging.getLogger"
            ) as mock_get_logger:
                setup_logging()
                mock_get_logger.assert_not_called()

    def test_force_setup_resets(self):
        with patch("registrarmonitor.core.logging_config._logging_setup_done", True):
            with patch(
                "registrarmonitor.core.logging_config.logging.getLogger"
            ) as mock_get_logger:
                setup_logging(force_setup=True)
                mock_get_logger.assert_called()

    def test_console_handler_added(self):
        setup_logging(enable_console=True, enable_file=False, force_setup=True)
        root_logger = logging.getLogger()
        handler_types = [type(h).__name__ for h in root_logger.handlers]
        assert "StreamHandler" in handler_types

    def test_file_handler_not_added_when_disabled(self):
        with (
            patch("registrarmonitor.core.logging_config._logging_setup_done", False),
            patch("registrarmonitor.core.logging_config.logging.getLogger"),
            patch(
                "registrarmonitor.core.logging_config.logging.handlers.RotatingFileHandler"
            ) as mock_rfh,
        ):
            setup_logging(enable_console=False, enable_file=False)
            mock_rfh.assert_not_called()

    def test_log_directory_not_created_when_file_logging_is_disabled(self, tmp_path):
        log_dir = tmp_path / "unused"

        setup_logging(
            log_dir=str(log_dir),
            enable_console=False,
            enable_file=False,
            force_setup=True,
        )

        assert not log_dir.exists()

    def test_console_colors_do_not_leak_into_file_log(self, tmp_path):
        setup_logging(log_dir=str(tmp_path), force_setup=True)

        logging.getLogger("test").info("hello")
        contents = (tmp_path / "registrar_monitor.log").read_text()

        assert "hello" in contents
        assert "\033[" not in contents


class TestGetLogger:
    def test_get_logger_with_name(self):
        logger = get_logger("my_module")
        assert logger.name == "my_module"
        assert isinstance(logger, logging.Logger)

    def test_get_logger_without_name(self):
        logger = get_logger()
        assert isinstance(logger, logging.Logger)


def test_log_performance_decorator():
    """Test that log_performance wraps and calls the function."""
    called = []

    @log_performance
    def my_func(a, b):
        called.append((a, b))
        return a + b

    result = my_func(1, 2)
    assert result == 3
    assert called == [(1, 2)]


def test_log_performance_reraises():
    """Test that log_performance re-raises exceptions."""

    @log_performance
    def failing_func():
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        failing_func()


def test_log_performance_records_traceback(caplog):
    @log_performance
    def failing_func():
        raise ValueError("boom")

    with caplog.at_level(logging.ERROR), pytest.raises(ValueError, match="boom"):
        failing_func()

    assert caplog.records[-1].exc_info is not None


def test_log_method_calls():
    """Test that log_method_calls decorates all public methods."""

    @log_method_calls
    class MyService:
        def do_work(self):
            return 42

        def _private(self):
            return 0

    svc = MyService()
    assert svc.do_work() == 42
