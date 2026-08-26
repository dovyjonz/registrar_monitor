"""Tests for the independent production service health monitor."""

import pytest

from registrarmonitor.services import health_monitor as health_monitor_module
from registrarmonitor.services.health_monitor import (
    HealthMonitor,
    ServiceStatus,
)

pytestmark = pytest.mark.unit


def make_monitor(
    states: dict[str, str],
    messages: list[str],
) -> HealthMonitor:
    def check(unit: str) -> ServiceStatus:
        return ServiceStatus(unit, states[unit])

    async def notify(message: str) -> None:
        messages.append(message)

    return HealthMonitor(
        check,
        notify,
        service_units=tuple(states),
        unhealthy_checks_before_alert=2,
    )


@pytest.mark.asyncio
async def test_alerts_once_for_an_outage_and_notifies_on_recovery():
    states = {
        "registrarmonitor.service": "active",
        "registrarmonitor-bot.service": "active",
    }
    messages: list[str] = []
    monitor = make_monitor(states, messages)

    healthy = await monitor.check_once()
    assert healthy.healthy
    assert messages == []

    states["registrarmonitor-bot.service"] = "failed"
    outage = await monitor.check_once()
    assert not outage.healthy
    assert messages == []

    await monitor.check_once()
    assert len(messages) == 1
    assert "registrarmonitor-bot.service: failed" in messages[0]

    await monitor.check_once()
    assert len(messages) == 1

    states["registrarmonitor-bot.service"] = "active"
    recovered = await monitor.check_once()
    assert recovered.healthy
    assert len(messages) == 2
    assert "recovered" in messages[1].lower()


@pytest.mark.asyncio
async def test_ignores_repeated_single_check_timeouts():
    states = {
        "registrarmonitor.service": "active",
        "registrarmonitor-bot.service": "active",
    }
    messages: list[str] = []
    monitor = make_monitor(states, messages)

    for _ in range(3):
        states.update(
            {
                "registrarmonitor.service": "timeout",
                "registrarmonitor-bot.service": "timeout",
            }
        )
        await monitor.check_once()
        states.update(
            {
                "registrarmonitor.service": "active",
                "registrarmonitor-bot.service": "active",
            }
        )
        await monitor.check_once()

    assert messages == []


@pytest.mark.asyncio
async def test_retries_alert_when_telegram_delivery_fails():
    states = {
        "registrarmonitor.service": "failed",
        "registrarmonitor-bot.service": "active",
    }
    attempts = 0
    messages: list[str] = []

    def check(unit: str) -> ServiceStatus:
        return ServiceStatus(unit, states[unit])

    async def notify(message: str) -> None:
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise RuntimeError("Telegram unavailable")
        messages.append(message)

    monitor = HealthMonitor(
        check,
        notify,
        service_units=tuple(states),
        unhealthy_checks_before_alert=2,
    )

    await monitor.check_once()
    await monitor.check_once()
    await monitor.check_once()

    assert attempts == 2
    assert len(messages) == 1


def test_service_status_is_healthy_only_when_systemd_reports_active():
    assert ServiceStatus("registrarmonitor.service", "active").healthy
    assert not ServiceStatus("registrarmonitor.service", "failed").healthy


@pytest.mark.asyncio
async def test_runtime_sends_alerts_to_configured_test_operator(monkeypatch):
    sent: list[tuple[int, str]] = []

    class FakeBot:
        def __init__(self, *, token: str):
            assert token == "test-token"

        async def __aenter__(self):
            return self

        async def __aexit__(self, _exc_type, _exc_value, _traceback):
            return None

        async def send_message(self, *, chat_id: int, text: str):
            sent.append((chat_id, text))

    class FakeMonitor:
        def __init__(
            self,
            _checker,
            notify,
            *,
            interval_seconds,
            unhealthy_checks_before_alert,
        ):
            assert interval_seconds == 7
            assert unhealthy_checks_before_alert == 3
            self.notify = notify

        async def run(self):
            await self.notify("health alert")

    monkeypatch.setattr(
        health_monitor_module,
        "get_config",
        lambda: {
            "telegram": {"bot_token": "test-token"},
            "telegram_bot": {"test_user_id": 41},
            "health_monitor": {
                "interval_seconds": 7,
                "unhealthy_checks_before_alert": 3,
            },
        },
    )
    monkeypatch.setattr(health_monitor_module, "Bot", FakeBot)
    monkeypatch.setattr(health_monitor_module, "HealthMonitor", FakeMonitor)

    await health_monitor_module.run_health_monitor()

    assert sent == [(41, "health alert")]
