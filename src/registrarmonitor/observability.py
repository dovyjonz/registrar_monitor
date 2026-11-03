"""Optional Sentry error reporting, enabled only when ``SENTRY_DSN`` is set."""

import logging
import os

logger = logging.getLogger(__name__)

_initialized = False


def init_sentry() -> bool:
    """Initialize Sentry once per process. Returns True when it is active."""
    global _initialized
    if _initialized:
        return True
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    import sentry_sdk

    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_ENVIRONMENT", "production"),
        send_default_pii=False,
        traces_sample_rate=0.0,
    )
    _initialized = True
    logger.info("Sentry error reporting enabled")
    return True
