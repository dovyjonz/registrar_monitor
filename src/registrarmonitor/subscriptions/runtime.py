"""Webhook and Mini App runtime for the Telegram subscription bot."""

import asyncio
import hmac
import logging
import re
import sqlite3
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from time import monotonic
from typing import Any, Protocol
from urllib.parse import urlsplit
from weakref import WeakValueDictionary

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.cors import CORSMiddleware
from starlette.requests import Request
from starlette.responses import Response
from starlette.routing import Route
from telegram import (
    BotCommand,
    BotCommandScopeChat,
    MenuButtonDefault,
    MenuButtonWebApp,
    Update,
    WebAppInfo,
)
from telegram.ext import Application, BaseUpdateProcessor, ChatMemberHandler

from ..cli.utils import find_active_semester
from ..config import Settings, get_settings
from ..data.database_manager import DatabaseManager
from .catalog import SubscriptionCatalog
from .dispatcher import SubscriptionDispatcher
from .interactions import SubscriptionInteractions
from .miniapp import MiniAppApi
from .models import BotDiagnostics
from .publication import ReportPublication
from .store import SubscriptionStore

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Open Registrar Monitor"),
    BotCommand("watch", "Watch a course or section"),
    BotCommand("watches", "Manage your watches"),
    BotCommand("help", "Get help"),
]
BOT_DIAGNOSTICS_COMMAND = BotCommand("test", "Open developer diagnostics")
BOT_ALLOWED_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER]
MAX_CONCURRENT_UPDATES = 8
MAX_QUEUED_UPDATES = 1024
SLOW_UPDATE_SECONDS = 3.0
MENU_BUTTON_TEXT = "Open app"


SECRET_TOKEN_PATTERN = re.compile(r"[A-Za-z0-9_-]{1,256}")  # Telegram's alphabet


@dataclass(frozen=True)
class WebhookConfig:
    """Where Telegram reaches the bot, and how its requests are authenticated."""

    public_url: str
    secret_token: str
    listen: str = "127.0.0.1"
    port: int = 8443

    @property
    def url_path(self) -> str:
        return urlsplit(self.public_url).path.lstrip("/")

    @classmethod
    def from_settings(cls, settings: Settings) -> "WebhookConfig":
        """Read the webhook settings, failing fast on anything unsafe or missing.

        The secret token is mandatory: it is the only proof that a request to the
        public tunnel hostname really comes from Telegram.
        """
        secret = settings.telegram_webhook_secret
        secret_token = secret.get_secret_value().strip() if secret else ""
        if not secret_token:
            raise ValueError("TELEGRAM_WEBHOOK_SECRET is required for the bot webhook")
        if SECRET_TOKEN_PATTERN.fullmatch(secret_token) is None:
            raise ValueError(
                "TELEGRAM_WEBHOOK_SECRET must be 1-256 characters of A-Z, a-z, "
                "0-9, underscore, or hyphen"
            )
        public_url = (settings.telegram_webhook_url or "").strip()
        parts = urlsplit(public_url)
        if parts.scheme != "https" or not parts.hostname or parts.query:
            raise ValueError("TELEGRAM_WEBHOOK_URL must be a public https URL")
        return cls(
            public_url=public_url,
            secret_token=secret_token,
            listen=settings.telegram_bot.webhook_listen,
            port=settings.telegram_bot.webhook_port,
        )


def create_web_app(
    application: Application,
    webhook: WebhookConfig,
    *,
    miniapp: MiniAppApi | None = None,
    site_origin: str = "",
) -> Starlette:
    """Build the ASGI app: the Telegram webhook plus the optional Mini App routes.

    Follows python-telegram-bot's custom webhook pattern: verify the secret header,
    then hand the update to the application's own queue.
    """
    expected = webhook.secret_token.encode()

    async def telegram(request: Request) -> Response:
        received = request.headers.get("x-telegram-bot-api-secret-token", "")
        if not hmac.compare_digest(received.encode(), expected):
            return Response(status_code=403)
        try:
            update = Update.de_json(await request.json(), application.bot)
        except (ValueError, TypeError):
            return Response(status_code=400)
        if update is None:
            return Response(status_code=400)
        await application.update_queue.put(update)
        return Response()

    routes = [Route(f"/{webhook.url_path}", telegram, methods=["POST"])]
    middleware = []
    if miniapp is not None:
        routes.extend(miniapp.routes())
        middleware.append(
            Middleware(
                CORSMiddleware,
                allow_origins=[site_origin],
                allow_methods=["GET", "POST", "DELETE"],
                allow_headers=["Authorization", "Content-Type"],
                max_age=600,
            )
        )
    return Starlette(routes=routes, middleware=middleware)


class OperationalStatsStore(Protocol):
    def operational_stats(self) -> dict[str, int]: ...


class BotRuntimeDiagnostics:
    """Keep a small, identifier-free operational view for developer test mode."""

    def __init__(
        self,
        store: OperationalStatsStore,
        *,
        clock: Callable[[], float] = monotonic,
    ) -> None:
        self._store = store
        self._clock = clock
        self._started_at = clock()
        self._logs: deque[str] = deque(maxlen=5)

    def record(self, record: logging.LogRecord) -> None:
        self._logs.append(f"{record.levelname}: {record.getMessage()}")

    async def snapshot(self) -> BotDiagnostics:
        stats = await asyncio.to_thread(self._store.operational_stats)
        elapsed = max(0, int(self._clock() - self._started_at))
        hours, remainder = divmod(elapsed, 3600)
        minutes, seconds = divmod(remainder, 60)
        uptime = f"{hours}h {minutes}m" if hours else f"{minutes}m {seconds}s"
        return BotDiagnostics(
            uptime=uptime,
            users=stats["users"],
            active_users=stats["active_users"],
            pending_deliveries=stats["pending_deliveries"],
            recent_logs=tuple(self._logs),
        )


class RecentSubscriptionLogHandler(logging.Handler):
    def __init__(self, diagnostics: BotRuntimeDiagnostics) -> None:
        super().__init__(level=logging.INFO)
        self._diagnostics = diagnostics

    def emit(self, record: logging.LogRecord) -> None:
        self._diagnostics.record(record)


class LatencyTrackingUpdateProcessor(BaseUpdateProcessor):
    """Process different chats concurrently while preserving per-chat order."""

    def __init__(
        self,
        max_concurrent_updates: int,
        *,
        slow_update_seconds: float,
    ) -> None:
        super().__init__(max(MAX_QUEUED_UPDATES, max_concurrent_updates))
        self.slow_update_seconds = slow_update_seconds
        self._active_updates = asyncio.Semaphore(max_concurrent_updates)
        self._chat_locks: WeakValueDictionary[int, asyncio.Lock] = WeakValueDictionary()

    async def do_process_update(
        self,
        update: object,
        coroutine: Awaitable[Any],
    ) -> None:
        started = monotonic()
        handler_started: float | None = None
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        try:
            if chat_id is None:
                async with self._active_updates:
                    handler_started = monotonic()
                    await coroutine
            else:
                lock = self._chat_locks.get(chat_id)
                if lock is None:
                    lock = asyncio.Lock()
                    self._chat_locks[chat_id] = lock
                async with lock:
                    async with self._active_updates:
                        handler_started = monotonic()
                        await coroutine
        finally:
            finished = monotonic()
            elapsed = finished - started
            queue_wait = (
                handler_started - started if handler_started is not None else elapsed
            )
            handler_duration = (
                finished - handler_started if handler_started is not None else 0.0
            )
            update_type = self._update_type(update)
            log = logger.warning if elapsed >= self.slow_update_seconds else logger.info
            log(
                "Telegram update processed type=%s queue_wait_ms=%.0f "
                "handler_duration_ms=%.0f duration_ms=%.0f slow=%s",
                update_type,
                queue_wait * 1000,
                handler_duration * 1000,
                elapsed * 1000,
                elapsed >= self.slow_update_seconds,
            )

    async def initialize(self) -> None:
        pass

    async def shutdown(self) -> None:
        pass

    @staticmethod
    def _update_type(update: object) -> str:
        if getattr(update, "callback_query", None) is not None:
            return "callback_query"
        if getattr(update, "message", None) is not None:
            return "message"
        if getattr(update, "my_chat_member", None) is not None:
            return "my_chat_member"
        return "other"


def bot_commands() -> list[BotCommand]:
    """Return the command menu visible to ordinary users."""
    return list(BOT_COMMANDS)


class SubscriptionBotRuntime:
    """Own the Telegram webhook, command handlers, recovery, and delivery dispatch."""

    def __init__(self) -> None:
        settings = get_settings()
        if settings.telegram_bot_token is None:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for the subscription bot")
        token = settings.telegram_bot_token.get_secret_value()
        self.settings = settings
        self.webhook = WebhookConfig.from_settings(settings)
        self.test_user_id = settings.telegram_bot_test_user_id
        self.store = SubscriptionStore(settings.directories.telegram_bot_state)
        self.diagnostics = BotRuntimeDiagnostics(self.store)
        self._diagnostics_log_handler = RecentSubscriptionLogHandler(self.diagnostics)
        self._catalog_lock = asyncio.Lock()
        self._catalog_key: tuple[str, int] | None = None
        self._catalog: SubscriptionCatalog | None = None
        update_processor = LatencyTrackingUpdateProcessor(
            MAX_CONCURRENT_UPDATES,
            slow_update_seconds=SLOW_UPDATE_SECONDS,
        )
        self.application = (
            Application.builder()
            .token(token)
            .concurrent_updates(update_processor)
            .build()
        )
        self.miniapp_enabled = bool(settings.website.miniapp_api_url)
        site = urlsplit(settings.website.base_url)
        self.site_origin = f"{site.scheme}://{site.netloc}"
        self.web_app = create_web_app(
            self.application,
            self.webhook,
            miniapp=(
                MiniAppApi(self.store, self._latest_catalog, token, self.site_origin)
                if self.miniapp_enabled
                else None
            ),
            site_origin=self.site_origin,
        )
        SubscriptionInteractions(
            self.store,
            self._latest_catalog,
            test_user_id=self.test_user_id,
            diagnostics=self.diagnostics.snapshot,
        ).register(self.application)
        self.application.add_handler(
            ChatMemberHandler(self._chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
        )

    async def run(self) -> None:
        """Serve webhook and Mini App requests until cancelled, then stop cleanly."""
        initialized = False
        started = False
        server: uvicorn.Server | None = None
        serve_task: asyncio.Task | None = None
        delivery_task: asyncio.Task | None = None
        subscription_logger = logging.getLogger("registrarmonitor.subscriptions")
        diagnostics_handler = getattr(self, "_diagnostics_log_handler", None)
        if diagnostics_handler is not None:
            subscription_logger.addHandler(diagnostics_handler)
        try:
            await self.application.initialize()
            initialized = True
            await self._configure_commands()
            await self._configure_menu_button()
            await self.application.start()
            started = True
            server = uvicorn.Server(
                uvicorn.Config(
                    self.web_app,
                    host=self.webhook.listen,
                    port=self.webhook.port,
                    log_config=None,
                    access_log=False,
                )
            )
            serve_task = asyncio.create_task(server.serve())
            while not server.started:
                if serve_task.done():
                    serve_task.result()
                    raise RuntimeError("The bot web server stopped during startup")
                await asyncio.sleep(0.05)
            # Telegram only learns the URL once the listener accepts requests.
            await self.application.bot.set_webhook(
                self.webhook.public_url,
                secret_token=self.webhook.secret_token,
                allowed_updates=BOT_ALLOWED_UPDATES,
            )
            logger.info("Telegram webhook listening on port %d", self.webhook.port)
            delivery_task = asyncio.create_task(self._delivery_loop())
            done, _ = await asyncio.wait(
                {serve_task, delivery_task}, return_when=asyncio.FIRST_COMPLETED
            )
            for task in done:
                task.result()
        finally:
            if server is not None:
                server.should_exit = True
            if delivery_task is not None:
                delivery_task.cancel()
            await asyncio.gather(
                *(task for task in (serve_task, delivery_task) if task is not None),
                return_exceptions=True,
            )
            if started:
                await self.application.stop()
            if initialized:
                await self.application.shutdown()
            if diagnostics_handler is not None:
                subscription_logger.removeHandler(diagnostics_handler)

    async def _configure_commands(self) -> None:
        await self.application.bot.set_my_commands(bot_commands())
        if self.test_user_id is not None:
            await self.application.bot.set_my_commands(
                [*bot_commands(), BOT_DIAGNOSTICS_COMMAND],
                scope=BotCommandScopeChat(self.test_user_id),
            )
            logger.info("Telegram developer diagnostics access is enabled")

    async def _configure_menu_button(self) -> None:
        """Point the chat menu button at the Mini App, or restore Telegram's default."""
        button = (
            MenuButtonWebApp(
                MENU_BUTTON_TEXT, WebAppInfo(url=self.settings.website.base_url)
            )
            if self.miniapp_enabled
            else MenuButtonDefault()
        )
        await self.application.bot.set_chat_menu_button(menu_button=button)

    async def _latest_catalog(self) -> SubscriptionCatalog | None:
        async with self._catalog_lock:
            semester = await asyncio.to_thread(find_active_semester)
            if not semester:
                return None
            snapshot_id = await asyncio.to_thread(self._latest_snapshot_id, semester)
            if not snapshot_id:
                return None
            key = (semester, snapshot_id)
            if self._catalog_key == key:
                return self._catalog
            snapshot = await asyncio.to_thread(
                self._load_snapshot, semester, snapshot_id
            )
            self._catalog_key = key
            self._catalog = SubscriptionCatalog(snapshot) if snapshot else None
            return self._catalog

    @staticmethod
    def _latest_snapshot_id(semester: str) -> int | None:
        database = DatabaseManager.create_read_only_for_semester(semester)
        return database.get_latest_snapshot_id()

    @staticmethod
    def _load_snapshot(semester: str, snapshot_id: int):
        database = DatabaseManager.create_read_only_for_semester(semester)
        return database.get_snapshot_data(snapshot_id)

    async def _delivery_loop(self) -> None:
        bot_config = self.settings.telegram_bot
        dispatcher = SubscriptionDispatcher(
            self.store,
            None,
            self.application.bot,
            reader_for_semester=DatabaseManager.create_read_only_for_semester,
            retry_base_seconds=bot_config.retry_base_seconds,
            retry_max_seconds=bot_config.retry_max_seconds,
            per_chat_interval=bot_config.per_chat_interval_seconds,
            global_interval=1 / bot_config.global_rate_per_second,
        )
        cleanup_interval = bot_config.cleanup_interval_seconds
        recovery_interval = 60
        next_cleanup = 0.0
        next_recovery = 0.0
        loop = asyncio.get_running_loop()
        while True:
            if loop.time() >= next_recovery:
                await asyncio.to_thread(self._recover_pending_batches)
                next_recovery = loop.time() + recovery_interval
            if loop.time() >= next_cleanup:
                await asyncio.to_thread(
                    self.store.cleanup,
                    completed_batch_days=bot_config.completed_batch_days,
                    inactive_user_days=bot_config.inactive_user_days,
                )
                next_cleanup = loop.time() + cleanup_interval
            if not await dispatcher.dispatch_one():
                await asyncio.sleep(1)

    def _recover_pending_batches(self) -> None:
        semesters = {batch.semester for batch in self.store.list_pending_batches()}
        for semester in semesters:
            try:
                database = DatabaseManager.create_read_only_for_semester(semester)
                ReportPublication(self.store, database).recover(semester=semester)
            except sqlite3.OperationalError as error:
                if "locked" not in str(error).lower():
                    raise
                logger.warning(
                    "Enrollment database is busy; pending batch recovery deferred"
                )

    async def _chat_member(self, update: Update, _context) -> None:
        change = update.my_chat_member
        if change is None or update.effective_chat is None:
            return
        if change.new_chat_member.status in {"kicked", "left"}:
            await asyncio.to_thread(
                self.store.deactivate_user, update.effective_chat.id
            )
