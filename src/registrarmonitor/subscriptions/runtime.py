"""Long-polling runtime for the Telegram subscription bot."""

import asyncio
import logging
from collections.abc import Awaitable
from time import monotonic
from typing import Any
from weakref import WeakValueDictionary

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, BaseUpdateProcessor, ChatMemberHandler

from ..cli.utils import find_active_semester
from ..config import get_config
from ..data.database_manager import DatabaseManager
from .catalog import SubscriptionCatalog
from .dispatcher import SubscriptionDispatcher
from .interactions import SubscriptionInteractions
from .publication import ReportPublication
from .store import SubscriptionStore

logger = logging.getLogger(__name__)

BOT_COMMANDS = [
    BotCommand("start", "Open the subscription bot"),
    BotCommand("watch", "Watch a course or section"),
    BotCommand("catalog", "Browse current courses"),
    BotCommand("import", "Import website selections"),
    BotCommand("subscriptions", "Manage subscriptions"),
    BotCommand("status", "Show current enrollment"),
    BotCommand("settings", "Manage bot data"),
    BotCommand("help", "Show commands"),
]
BOT_TEST_COMMAND = BotCommand("test", "Check developer test mode")
BOT_ALLOWED_UPDATES = [Update.MESSAGE, Update.CALLBACK_QUERY, Update.MY_CHAT_MEMBER]
MAX_CONCURRENT_UPDATES = 8
MAX_QUEUED_UPDATES = 1024
SLOW_UPDATE_SECONDS = 3.0


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
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        try:
            if chat_id is None:
                async with self._active_updates:
                    await coroutine
            else:
                lock = self._chat_locks.get(chat_id)
                if lock is None:
                    lock = asyncio.Lock()
                    self._chat_locks[chat_id] = lock
                async with lock:
                    async with self._active_updates:
                        await coroutine
        finally:
            elapsed = monotonic() - started
            log = logger.warning if elapsed >= self.slow_update_seconds else logger.info
            log(
                "Telegram update processed type=%s duration_ms=%.0f slow=%s",
                self._update_type(update),
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
    """Own Telegram polling, command handlers, recovery, and delivery dispatch."""

    def __init__(self) -> None:
        config = get_config()
        token = config.get("telegram", {}).get("bot_token")
        if not token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required for the subscription bot")
        self.config = config
        self.test_user_id = config.get("telegram_bot", {}).get("test_user_id")
        self.store = SubscriptionStore(config["directories"]["telegram_bot_state"])
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
        SubscriptionInteractions(
            self.store,
            self._latest_catalog,
            test_user_id=self.test_user_id,
        ).register(self.application)
        self.application.add_handler(
            ChatMemberHandler(self._chat_member, ChatMemberHandler.MY_CHAT_MEMBER)
        )

    async def run(self) -> None:
        """Poll until cancelled, then stop Telegram resources cleanly."""
        initialized = False
        started = False
        tasks: list[asyncio.Task[None]] = []
        try:
            await self.application.initialize()
            initialized = True
            webhook = await self.application.bot.get_webhook_info()
            if webhook.url:
                raise RuntimeError(
                    "Telegram bot has an active webhook; remove it explicitly before "
                    "starting long polling"
                )
            await self.application.bot.set_my_commands(bot_commands())
            if self.test_user_id is not None:
                await self.application.bot.set_my_commands(
                    [*bot_commands(), BOT_TEST_COMMAND],
                    scope=BotCommandScopeChat(self.test_user_id),
                )
                logger.warning("Telegram bot developer test mode is active")
            await self.application.start()
            started = True
            tasks = [
                asyncio.create_task(self._poll_updates()),
                asyncio.create_task(self._delivery_loop()),
            ]
            await asyncio.gather(*tasks)
        finally:
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            if started:
                await self.application.stop()
            if initialized:
                await self.application.shutdown()

    async def _poll_updates(self) -> None:
        """Poll without PTB's webhook-deleting Updater bootstrap."""
        offset = None
        while True:
            try:
                updates = await self.application.bot.get_updates(
                    offset=offset,
                    timeout=30,
                    allowed_updates=BOT_ALLOWED_UPDATES,
                )
            except Conflict as error:
                raise RuntimeError(
                    "Telegram updates are already being consumed by another process"
                ) from error
            except NetworkError:
                logger.warning("Telegram update polling failed transiently")
                await asyncio.sleep(5)
                continue
            for update in updates:
                offset = update.update_id + 1
                await self.application.update_queue.put(update)

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
        database = DatabaseManager.create_for_semester(semester)
        return database.get_latest_snapshot_id()

    @staticmethod
    def _load_snapshot(semester: str, snapshot_id: int):
        database = DatabaseManager.create_for_semester(semester)
        return database.get_snapshot_data(snapshot_id)

    async def _delivery_loop(self) -> None:
        bot_config = self.config.get("telegram_bot", {})
        dispatcher = SubscriptionDispatcher(
            self.store,
            None,
            self.application.bot,
            reader_for_semester=DatabaseManager.create_for_semester,
            retry_base_seconds=bot_config.get("retry_base_seconds", 5),
            retry_max_seconds=bot_config.get("retry_max_seconds", 900),
            per_chat_interval=bot_config.get("per_chat_interval_seconds", 1),
            global_interval=1 / bot_config.get("global_rate_per_second", 25),
            test_user_id=self.test_user_id,
        )
        cleanup_interval = bot_config.get("cleanup_interval_seconds", 3600)
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
                    completed_batch_days=bot_config.get("completed_batch_days", 90),
                    inactive_user_days=bot_config.get("inactive_user_days", 180),
                )
                next_cleanup = loop.time() + cleanup_interval
            if not await dispatcher.dispatch_one():
                await asyncio.sleep(1)

    def _recover_pending_batches(self) -> None:
        semesters = {batch.semester for batch in self.store.list_pending_batches()}
        for semester in semesters:
            database = DatabaseManager.create_for_semester(semester)
            ReportPublication(self.store, database).recover(semester=semester)

    async def _chat_member(self, update: Update, _context) -> None:
        change = update.my_chat_member
        user = update.effective_user
        if (
            change is None
            or update.effective_chat is None
            or (
                self.test_user_id is not None
                and (user is None or user.id != self.test_user_id)
            )
        ):
            return
        if change.new_chat_member.status in {"kicked", "left"}:
            await asyncio.to_thread(
                self.store.deactivate_user, update.effective_chat.id
            )
