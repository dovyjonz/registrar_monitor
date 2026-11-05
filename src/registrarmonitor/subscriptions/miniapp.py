"""Telegram Mini App API: ``initData`` authentication and watch routes.

The Mini App is the dashboard opened inside Telegram. It calls these routes on the
bot process, which owns the bot token and the bot store, so no Worker sits between
the page and the data. The user is always derived from the signed ``initData``,
never from anything the client states about itself.
"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import timedelta
from urllib.parse import parse_qsl

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from .catalog import SubscriptionCatalog
from .models import SubscriptionTarget
from .payloads import decode_target, encode_target
from .store import SubscriptionStore

logger = logging.getLogger(__name__)

INIT_DATA_MAX_AGE = timedelta(hours=24)
CLOCK_SKEW_SECONDS = 60
AUTH_SCHEME = "tma "
MAX_CODE_LENGTH = 80

TOUCH_INTERVAL_SECONDS = 300.0
CatalogProvider = Callable[[], Awaitable[SubscriptionCatalog | None]]


class InvalidInitData(ValueError):
    """The ``initData`` is malformed, forged, or too old."""


def validate_init_data(
    init_data: str,
    bot_token: str,
    *,
    max_age: timedelta = INIT_DATA_MAX_AGE,
    now: float | None = None,
) -> int:
    """Verify Telegram's ``initData`` signature and return the Telegram user id.

    Implements https://core.telegram.org/bots/webapps#validating-data-received-via-the-mini-app:
    the secret key is ``HMAC_SHA256("WebAppData", bot_token)`` and the signature is
    ``HMAC_SHA256(secret_key, data_check_string)`` over every field except ``hash``.
    """
    fields = dict(parse_qsl(init_data, keep_blank_values=True))
    received_hash = fields.pop("hash", "")
    if not received_hash:
        raise InvalidInitData("initData has no hash")
    data_check_string = "\n".join(f"{key}={fields[key]}" for key in sorted(fields))
    secret_key = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    expected = hmac.new(
        secret_key, data_check_string.encode(), hashlib.sha256
    ).hexdigest()
    if not hmac.compare_digest(expected, received_hash.lower()):
        raise InvalidInitData("initData signature does not match")
    try:
        auth_date = int(fields["auth_date"])
        user_id = int(json.loads(fields["user"])["id"])
    except (KeyError, ValueError, TypeError) as error:
        raise InvalidInitData("initData has no valid user or auth_date") from error
    current = time.time() if now is None else now
    if auth_date > current + CLOCK_SKEW_SECONDS:
        raise InvalidInitData("initData auth_date is in the future")
    if current - auth_date > max_age.total_seconds():
        raise InvalidInitData("initData is expired")
    return user_id


def _error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": message}, status_code=status)


@dataclass(frozen=True)
class MiniAppApi:
    """Watch routes for Mini App users, backed by the bot's own store."""

    store: SubscriptionStore
    catalog: CatalogProvider
    bot_token: str
    site_origin: str
    touch_interval: float = TOUCH_INTERVAL_SECONDS
    clock: Callable[[], float] = time.monotonic
    _touched: dict[int, float] = field(default_factory=dict, init=False, repr=False)

    def routes(self) -> list[Route]:
        return [
            Route("/miniapp/watches", self.list_watches, methods=["GET"]),
            Route("/miniapp/watches", self.add_watch, methods=["POST"]),
            Route("/miniapp/watches/{watch_id}", self.remove_watch, methods=["DELETE"]),
        ]

    async def _authenticate(self, request: Request) -> int | JSONResponse:
        """Return the verified Telegram user id, or the error response to send."""
        origin = request.headers.get("origin")
        if origin is not None and origin != self.site_origin:
            return _error(403, "Origin not allowed")
        header = request.headers.get("authorization", "")
        if not header.startswith(AUTH_SCHEME):
            return _error(401, "Missing Telegram initData")
        try:
            user_id = validate_init_data(header[len(AUTH_SCHEME) :], self.bot_token)
        except InvalidInitData as error:
            logger.info("Rejected Mini App request: %s", error)
            return _error(401, "Invalid Telegram initData")
        # A private chat shares its id with the user, as in the bot's own handlers.
        now = self.clock()
        last = self._touched.get(user_id)
        if last is None or now - last >= self.touch_interval:
            self._touched[user_id] = now
            await asyncio.to_thread(
                self.store.touch_user,
                telegram_user_id=user_id,
                private_chat_id=user_id,
            )
        return user_id

    async def _watches(self, user_id: int, **extra) -> JSONResponse:
        targets = await asyncio.to_thread(self.store.list_subscriptions, user_id)
        catalog = await self.catalog()
        return JSONResponse(
            {
                "semester": catalog.snapshot.semester if catalog else None,
                "watches": [
                    {
                        "id": encode_target(target),
                        "semester": target.semester,
                        "course": target.course_code,
                        "section": target.section_code,
                    }
                    for target in targets
                ],
                **extra,
            },
            headers={"Cache-Control": "no-store"},
        )

    async def list_watches(self, request: Request) -> JSONResponse:
        user_id = await self._authenticate(request)
        if isinstance(user_id, JSONResponse):
            return user_id
        return await self._watches(user_id)

    async def add_watch(self, request: Request) -> JSONResponse:
        user_id = await self._authenticate(request)
        if isinstance(user_id, JSONResponse):
            return user_id
        target = await self._target_from_body(request)
        if target is None:
            return _error(400, "Expected semester, course, and optional section")
        catalog = await self.catalog()
        if catalog is None:
            return _error(503, "No enrollment snapshot is available")
        if not catalog.resolve(target):
            return _error(404, "That course or section is not in the latest snapshot")
        added = await asyncio.to_thread(self.store.add_watch, user_id, target)
        return await self._watches(user_id, added=added)

    @staticmethod
    async def _target_from_body(request: Request) -> SubscriptionTarget | None:
        try:
            body = await request.json()
            parts = [body["semester"], body["course"], body.get("section") or ""]
        except (ValueError, KeyError, TypeError, AttributeError):
            return None
        if not all(
            isinstance(part, str) and len(part) <= MAX_CODE_LENGTH for part in parts
        ):
            return None
        return SubscriptionTarget(*parts)

    async def remove_watch(self, request: Request) -> JSONResponse:
        user_id = await self._authenticate(request)
        if isinstance(user_id, JSONResponse):
            return user_id
        try:
            target = decode_target(request.path_params["watch_id"])
        except ValueError:
            return _error(400, "Invalid watch id")
        removed = await asyncio.to_thread(self.store.unsubscribe, user_id, target)
        return await self._watches(user_id, removed=removed)
