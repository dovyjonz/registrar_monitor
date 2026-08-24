"""Monitor the production systemd services and alert the test operator."""

import asyncio
import logging
import subprocess
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass

from telegram import Bot

from ..config import get_config

logger = logging.getLogger(__name__)

MONITORED_SERVICE_UNITS = (
    "registrarmonitor.service",
    "registrarmonitor-bot.service",
)
DEFAULT_INTERVAL_SECONDS = 30
SYSTEMD_TIMEOUT_SECONDS = 5

ServiceChecker = Callable[[str], "ServiceStatus"]
Notifier = Callable[[str], Awaitable[None]]


@dataclass(frozen=True)
class ServiceStatus:
    """The observed systemd state for one service unit."""

    unit: str
    state: str

    @property
    def healthy(self) -> bool:
        return self.state == "active"


@dataclass(frozen=True)
class HealthSnapshot:
    """The states observed for all monitored units in one check."""

    services: tuple[ServiceStatus, ...]

    @property
    def healthy(self) -> bool:
        return all(service.healthy for service in self.services)


class SystemdServiceChecker:
    """Read a unit's state without requiring service-management privileges."""

    def __call__(self, unit: str) -> ServiceStatus:
        try:
            result = subprocess.run(
                ["systemctl", "is-active", unit],
                capture_output=True,
                check=False,
                text=True,
                timeout=SYSTEMD_TIMEOUT_SECONDS,
            )
        except subprocess.TimeoutExpired:
            state = "timeout"
        except (FileNotFoundError, OSError):
            state = "unavailable"
        else:
            state = result.stdout.strip() or "unknown"
        return ServiceStatus(unit, state)


class HealthMonitor:
    """Poll service states and send one alert per outage and recovery."""

    def __init__(
        self,
        checker: ServiceChecker,
        notify: Notifier,
        *,
        service_units: Sequence[str] = MONITORED_SERVICE_UNITS,
        interval_seconds: float = DEFAULT_INTERVAL_SECONDS,
    ) -> None:
        if not service_units:
            raise ValueError("At least one service unit is required")
        if interval_seconds <= 0:
            raise ValueError("Health monitor interval must be positive")
        self._checker = checker
        self._notify = notify
        self._service_units = tuple(service_units)
        self._interval_seconds = interval_seconds
        self._outage_alerted = False

    async def check_once(self) -> HealthSnapshot:
        """Check all units and notify only when health changes state."""
        statuses = await asyncio.gather(
            *(asyncio.to_thread(self._checker, unit) for unit in self._service_units)
        )
        snapshot = HealthSnapshot(tuple(statuses))
        if snapshot.healthy:
            if self._outage_alerted and await self._try_notify(
                self._recovery_message(snapshot)
            ):
                self._outage_alerted = False
        elif not self._outage_alerted and await self._try_notify(
            self._outage_message(snapshot)
        ):
            self._outage_alerted = True
        return snapshot

    async def run(self) -> None:
        """Run continuously until the service receives cancellation."""
        while True:
            try:
                await self.check_once()
            except asyncio.CancelledError:
                raise
            except Exception as error:  # pragma: no cover - final safety boundary
                logger.error(
                    "Health check cycle failed error_type=%s",
                    type(error).__name__,
                )
            await asyncio.sleep(self._interval_seconds)

    async def _try_notify(self, message: str) -> bool:
        try:
            await self._notify(message)
        except Exception as error:  # Telegram failures must not kill the monitor.
            logger.warning(
                "Health alert delivery failed error_type=%s",
                type(error).__name__,
            )
            return False
        return True

    @staticmethod
    def _outage_message(snapshot: HealthSnapshot) -> str:
        return "\n".join(
            [
                "🚨 Registrar Monitor health alert",
                "",
                "A monitored service is not active:",
                *(
                    f"- {service.unit}: {service.state}"
                    for service in snapshot.services
                ),
            ]
        )

    @staticmethod
    def _recovery_message(snapshot: HealthSnapshot) -> str:
        return "\n".join(
            [
                "✅ Registrar Monitor services recovered",
                "",
                "All monitored services are active:",
                *(
                    f"- {service.unit}: {service.state}"
                    for service in snapshot.services
                ),
            ]
        )


async def run_health_monitor() -> None:
    """Run the configured production health monitor."""
    config = get_config()
    telegram_config = config.get("telegram", {})
    bot_token = telegram_config.get("bot_token")
    test_user_id = config.get("telegram_bot", {}).get("test_user_id")
    if not bot_token:
        raise ValueError("TELEGRAM_BOT_TOKEN is required for the health monitor")
    if not test_user_id:
        raise ValueError("TELEGRAM_BOT_TEST_USER_ID is required for the health monitor")

    health_config = config.get("health_monitor", {})
    interval_seconds = health_config.get("interval_seconds", DEFAULT_INTERVAL_SECONDS)
    async with Bot(token=bot_token) as bot:

        async def notify(message: str) -> None:
            await bot.send_message(chat_id=test_user_id, text=message)

        await HealthMonitor(
            SystemdServiceChecker(),
            notify,
            interval_seconds=interval_seconds,
        ).run()
