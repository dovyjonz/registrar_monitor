import json
import logging

import pytest

from registrarmonitor.core.logging_config import setup_logging


@pytest.fixture(autouse=True)
def restore_root_logger():
    root = logging.getLogger()
    handlers, level = list(root.handlers), root.level
    yield
    for handler in list(root.handlers):
        handler.close()
    root.handlers[:] = handlers
    root.setLevel(level)


def test_cli_logging_writes_single_plain_text_file(tmp_path, capsys):
    setup_logging(level="INFO", log_dir=str(tmp_path))
    logging.getLogger("registrarmonitor.test").info("hello semester=%s", "fall2026")

    assert [p.name for p in tmp_path.iterdir()] == ["registrar_monitor.log"]
    line = (tmp_path / "registrar_monitor.log").read_text().strip()
    assert line.endswith("INFO    registrarmonitor.test: hello semester=fall2026")
    assert "\033[" not in capsys.readouterr().out


def test_json_logging_skips_file_handler(tmp_path, capsys):
    setup_logging(level="INFO", log_dir=str(tmp_path), json_logs=True)
    logging.getLogger("registrarmonitor.test").warning("degraded")

    assert list(tmp_path.iterdir()) == []
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["level"] == "WARNING"
    assert payload["message"] == "degraded"


def test_http_transport_loggers_stay_at_warning(tmp_path):
    setup_logging(level="DEBUG", log_dir=str(tmp_path))

    assert logging.getLogger("httpx").level == logging.WARNING
    assert logging.getLogger("httpcore").level == logging.WARNING
