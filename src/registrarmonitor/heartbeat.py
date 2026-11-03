"""External heartbeat ping (Healthchecks.io compatible)."""

import logging
import os

import httpx

logger = logging.getLogger(__name__)

HEARTBEAT_TIMEOUT_SECONDS = 5.0


async def send_heartbeat() -> bool:
    """Ping ``HEARTBEAT_URL`` if configured; never raise.

    Returns True only when the ping was delivered. The URL is a secret (it
    identifies the check), so it is never logged.
    """
    url = os.environ.get("HEARTBEAT_URL", "").strip()
    if not url:
        return False
    try:
        async with httpx.AsyncClient(timeout=HEARTBEAT_TIMEOUT_SECONDS) as client:
            response = await client.get(url)
            response.raise_for_status()
    except Exception as exc:
        logger.warning("Heartbeat ping failed: %s", type(exc).__name__)
        return False
    logger.debug("Heartbeat ping delivered")
    return True
