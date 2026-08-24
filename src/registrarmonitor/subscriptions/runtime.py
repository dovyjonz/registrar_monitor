"""Long-polling runtime for the Telegram subscription bot."""

import asyncio
import logging

from telegram import BotCommand, BotCommandScopeChat, Update
from telegram.error import Conflict, NetworkError
from telegram.ext import Application, ChatMemberHandler

from ..cli.utils import detect_active_semester
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
        self.application = Application.builder().token(token).build()
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
        semester = await detect_active_semester()
        if not semester:
            return None
        database = DatabaseManager.create_for_semester(semester)
        snapshot_id = database.get_latest_snapshot_id()
        snapshot = database.get_snapshot_data(snapshot_id) if snapshot_id else None
        return SubscriptionCatalog(snapshot) if snapshot else None

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
                self._recover_pending_batches()
                next_recovery = loop.time() + recovery_interval
            if loop.time() >= next_cleanup:
                self.store.cleanup(
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
            self.store.deactivate_user(update.effective_chat.id)
