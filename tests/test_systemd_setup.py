import re
from pathlib import Path

SETUP_SCRIPT = Path(__file__).parent.parent / "scripts" / "setup_vps.sh"


def test_vps_setup_keeps_canonical_service_paused():
    script = SETUP_SCRIPT.read_text(encoding="utf-8")

    assert "scripts/registrarmonitor.service" in script
    assert "scripts/registrarmonitor-bot.service" in script
    assert "scripts/registrarmonitor-health.service" in script
    assert "scripts/registrarmonitor-network-watchdog.service" in script
    assert "scripts/registrarmonitor-network-watchdog.timer" in script
    assert "sudo cp scripts/registrarmonitor.service /etc/systemd/system/" in script
    assert "sudo cp scripts/registrarmonitor-bot.service /etc/systemd/system/" in script
    assert (
        "sudo cp scripts/registrarmonitor-health.service /etc/systemd/system/" in script
    )
    assert (
        "sudo cp scripts/registrarmonitor-network-watchdog.service /etc/systemd/system/"
        in script
    )
    assert (
        "sudo cp scripts/registrarmonitor-network-watchdog.timer /etc/systemd/system/"
        in script
    )
    assert "ExecStart=/usr/bin/env uv run monitor bot" in script
    assert "ExecStart=/usr/bin/env uv run monitor health-monitor" in script
    assert "ExecStart=$PROJECT_ROOT/scripts/network_watchdog.sh" in script
    assert "OnUnitActiveSec=1min" in script
    assert "registrar-monitor.service" not in script
    assert "--now" not in script
    assert re.search(r"systemctl\s+(?:enable|start|restart)\b", script) is None
    assert "did not change any service's installed or active state" in script
    assert "operator-authorized" in script
    assert "scripts/runtime_doctor.sh" in script
