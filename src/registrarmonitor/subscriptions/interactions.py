"""Private-chat commands and inline interactions for course subscriptions."""

from collections.abc import Awaitable, Callable
from html import escape

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from .catalog import SubscriptionCatalog
from .models import SubscriptionTarget
from .payloads import decode_target, encode_target
from .store import SubscriptionStore

CatalogProvider = Callable[[], Awaitable[SubscriptionCatalog | None]]
PAGE_SIZE = 6


class SubscriptionInteractions:
    """Translate Telegram updates into validated subscription operations."""

    def __init__(
        self,
        store: SubscriptionStore,
        catalog: CatalogProvider,
        *,
        test_user_id: int | None = None,
    ) -> None:
        self.store = store
        self._catalog_provider = catalog
        self.test_user_id = test_user_id

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("watch", self.watch))
        application.add_handler(CommandHandler("subscriptions", self.subscriptions))
        application.add_handler(CommandHandler("status", self.status))
        application.add_handler(CommandHandler("settings", self.settings))
        application.add_handler(CommandHandler("help", self.help))
        if self.test_user_id is not None:
            application.add_handler(CommandHandler("test", self.test_mode_status))
        application.add_handler(CallbackQueryHandler(self.callback))
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, self.watch_text)
        )

    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None:
            return
        args = context.args or []
        if args:
            try:
                target = decode_target(args[0])
            except ValueError:
                await message.reply_text(
                    "This subscription link is invalid. Use /watch to search.",
                    reply_markup=self._home_keyboard(),
                )
                return
            catalog = await self._catalog_provider()
            if catalog is None or not catalog.resolve(target):
                await message.reply_text(
                    "That course or section is not in the latest snapshot. Use "
                    "/watch to search.",
                    reply_markup=self._home_keyboard(),
                )
                return
            course = catalog.course(target.course_code)
            if course is None:
                return
            await message.reply_text(
                f"{self._course_text(catalog.snapshot.semester, course, catalog.snapshot.timestamp, section_code=target.section_code)}\n\n"
                f"Watch {self._target_label(target)}?",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[self._target_button("Confirm", "sub", target)]]
                ),
            )
            return
        catalog = await self._catalog_provider()
        semester = catalog.snapshot.semester if catalog else "not available"
        await message.reply_text(
            "Track enrollment changes for a course or section.\n\n"
            f"Current semester: {semester}",
            reply_markup=self._home_keyboard(),
        )

    async def watch(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None:
            return
        query = " ".join(context.args or []).strip()
        if not query:
            if context.user_data is not None:
                context.user_data["awaiting_watch"] = True
            await message.reply_text("Send a course code or title.")
            return
        await self._show_search(message.reply_text, query)

    async def watch_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if context.user_data is None or not context.user_data.pop(
            "awaiting_watch", False
        ):
            return
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None or message.text is None:
            return
        await self._show_search(message.reply_text, message.text)

    async def subscriptions(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        await self._show_subscriptions(
            message.reply_text,
            user.id,
            page=0,
        )

    async def status(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        query = " ".join(context.args or []).strip()
        if not query:
            targets = self.store.list_subscriptions(user.id)
            if not targets:
                await message.reply_text(
                    "You have no subscriptions. Use /status COURSE or /watch."
                )
                return
            await message.reply_text(
                "Choose a watched course or section:",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            self._target_button(
                                self._target_label(target), "view", target
                            )
                        ]
                        for target in targets
                    ]
                ),
            )
            return
        catalog = await self._catalog_provider()
        if catalog is None:
            await message.reply_text("No enrollment snapshot is available.")
            return
        matches = catalog.search(query)
        if not matches:
            await message.reply_text("No matching course found.")
            return
        await message.reply_text(
            self._course_text(
                catalog.snapshot.semester, matches[0], catalog.snapshot.timestamp
            ),
            parse_mode=ParseMode.HTML,
            reply_markup=self._course_keyboard(
                catalog.snapshot.semester, matches[0], user.id
            ),
        )

    async def settings(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(
            "Alerts follow successful Registrar Monitor channel reports; they are "
            "not sent on every registrar poll.",
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Clear subscriptions", callback_data="clear"
                        )
                    ],
                    [InlineKeyboardButton("Delete bot data", callback_data="delete")],
                ]
            ),
        )

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(
            "/watch — add a course or section\n"
            "/subscriptions — manage watches\n"
            "/status — current enrollment\n"
            "/settings — clear or delete data\n"
            "/help — this message"
        )

    async def test_mode_status(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Confirm the restricted bot can read its local state."""
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or self.test_user_id is None:
            return
        catalog = await self._catalog_provider()
        if catalog is None:
            snapshot = "No enrollment snapshot is available."
        else:
            snapshot = (
                f"Semester: {catalog.snapshot.semester}\n"
                f"Latest snapshot: {catalog.snapshot.timestamp}"
            )
        count = len(self.store.list_subscriptions(user.id))
        await message.reply_text(
            "Developer test mode is active.\n"
            f"{snapshot}\n"
            f"Subscriptions: {count}\n"
            "Private bot delivery is restricted to this account."
        )

    async def callback(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        query = update.callback_query
        if query is None:
            return
        user = update.effective_user
        if user is None or not self._is_allowed(user.id):
            return
        await query.answer()
        if (
            not await self._prepare_private(update, reply=False)
            or query.message is None
        ):
            return
        data = query.data or ""
        user_id = user.id

        if data.startswith("view:"):
            await self._view_target(query.edit_message_text, data[5:], user_id)
        elif data.startswith("sub:"):
            await self._change_subscription(
                query.edit_message_text, user_id, data[4:], True
            )
        elif data.startswith("unsub:"):
            await self._change_subscription(
                query.edit_message_text, user_id, data[6:], False
            )
        elif data.startswith("subs:"):
            try:
                page = max(0, int(data[5:]))
            except ValueError:
                page = 0
            await self._show_subscriptions(query.edit_message_text, user_id, page=page)
        elif data == "watch":
            if context.user_data is not None:
                context.user_data["awaiting_watch"] = True
            await query.edit_message_text("Send a course code or title.")
        elif data == "clear":
            await query.edit_message_text(
                "Clear all subscriptions?",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Confirm clear", callback_data="clear:yes")]]
                ),
            )
        elif data == "clear:yes":
            count = self.store.clear_subscriptions(user_id)
            await query.edit_message_text(f"Cleared {count} subscription(s).")
        elif data == "delete":
            await query.edit_message_text(
                "Delete all of your bot data?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Confirm delete", callback_data="delete:yes"
                            )
                        ]
                    ]
                ),
            )
        elif data == "delete:yes":
            self.store.delete_user(user_id)
            await query.edit_message_text("Your bot data was deleted.")
        else:
            await query.edit_message_text("This button is no longer valid.")

    async def _prepare_private(self, update: Update, *, reply: bool = True) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
            return False
        if not self._is_allowed(user.id):
            return False
        if chat.type != ChatType.PRIVATE:
            if reply and update.effective_message:
                await update.effective_message.reply_text(
                    "Use this bot in a private chat."
                )
            return False
        self.store.touch_user(
            telegram_user_id=user.id,
            private_chat_id=chat.id,
        )
        return True

    def _is_allowed(self, user_id: int) -> bool:
        return self.test_user_id is None or user_id == self.test_user_id

    async def _show_search(self, send, query: str) -> None:
        catalog = await self._catalog_provider()
        if catalog is None:
            await send("No enrollment snapshot is available.")
            return
        matches = catalog.search(query)
        if not matches:
            await send("No matching course found.")
            return
        keyboard = [
            [
                self._target_button(
                    course.course_code,
                    "view",
                    SubscriptionTarget(catalog.snapshot.semester, course.course_code),
                )
            ]
            for course in matches
        ]
        await send("Select a course:", reply_markup=InlineKeyboardMarkup(keyboard))

    async def _view_target(self, edit, payload: str, user_id: int) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not catalog.resolve(target):
            await edit("That course is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        await edit(
            self._course_text(target.semester, course, catalog.snapshot.timestamp),
            parse_mode=ParseMode.HTML,
            reply_markup=self._course_keyboard(target.semester, course, user_id),
        )

    async def _change_subscription(
        self, edit, user_id: int, payload: str, subscribe: bool
    ) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not catalog.resolve(target):
            await edit("That course or section is not in the latest snapshot.")
            return
        changed = (
            self.store.subscribe(user_id, target)
            if subscribe
            else self.store.unsubscribe(user_id, target)
        )
        if changed:
            message = f"{'Watching' if subscribe else 'Removed'} {self._target_label(target)}."
        else:
            message = (
                f"Already watching {self._target_label(target)}."
                if subscribe
                else f"Already removed {self._target_label(target)}."
            )
        next_action = "unsub" if subscribe else "sub"
        next_label = "Stop watching" if subscribe else "Watch again"
        await edit(
            message,
            reply_markup=InlineKeyboardMarkup(
                [
                    [self._target_button(next_label, next_action, target)],
                    [InlineKeyboardButton("My subscriptions", callback_data="subs:0")],
                ]
            ),
        )

    async def _show_subscriptions(self, send, user_id: int, *, page: int) -> None:
        targets = self.store.list_subscriptions(user_id)
        if not targets:
            await send("You have no subscriptions. Use /watch to add one.")
            return
        page_count = max(1, (len(targets) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, page_count - 1)
        visible = targets[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        keyboard = [
            [
                self._target_button(
                    f"Remove {self._target_label(target)}", "unsub", target
                )
            ]
            for target in visible
        ]
        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton("Previous", callback_data=f"subs:{page - 1}")
            )
        if page + 1 < page_count:
            navigation.append(
                InlineKeyboardButton("Next", callback_data=f"subs:{page + 1}")
            )
        if navigation:
            keyboard.append(navigation)
        lines = [
            f"{target.semester}: {self._target_label(target)}" for target in visible
        ]
        await send(
            "Your subscriptions:\n" + "\n".join(f"• {line}" for line in lines),
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    @staticmethod
    def _target_label(target: SubscriptionTarget) -> str:
        return (
            f"{target.course_code} / {target.section_code}"
            if target.section_code
            else target.course_code
        )

    @staticmethod
    def _target_button(
        label: str, action: str, target: SubscriptionTarget
    ) -> InlineKeyboardButton:
        return InlineKeyboardButton(
            label,
            callback_data=f"{action}:{encode_target(target)}",
        )

    @staticmethod
    def _home_keyboard() -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Watch a course", callback_data="watch")],
                [InlineKeyboardButton("My subscriptions", callback_data="subs:0")],
            ]
        )

    def _course_keyboard(
        self, semester: str, course, user_id: int
    ) -> InlineKeyboardMarkup:
        watched = set(self.store.list_subscriptions(user_id))
        course_target = SubscriptionTarget(semester, course.course_code)
        course_watched = course_target in watched
        rows = [
            [
                self._target_button(
                    "Stop watching course" if course_watched else "Watch course",
                    "unsub" if course_watched else "sub",
                    course_target,
                )
            ]
        ]
        for section in sorted(
            course.sections.values(), key=lambda item: item.section_id
        ):
            target = SubscriptionTarget(
                semester, course.course_code, section.section_id
            )
            is_watched = target in watched
            rows.append(
                [
                    self._target_button(
                        (
                            f"Stop watching {section.section_id}"
                            if is_watched
                            else f"Watch section {section.section_id}"
                        ),
                        "unsub" if is_watched else "sub",
                        target,
                    )
                ]
            )
        return InlineKeyboardMarkup(rows)

    @staticmethod
    def _course_text(
        semester: str,
        course,
        updated_at: str,
        *,
        section_code: str = "",
    ) -> str:
        title = f" — {escape(course.course_title)}" if course.course_title else ""
        lines = [
            f"<b>{escape(course.course_code)}</b>{title}",
            escape(semester),
            f"Updated: {escape(updated_at)}",
        ]
        sections = sorted(course.sections.values(), key=lambda item: item.section_id)
        if section_code:
            sections = [
                section for section in sections if section.section_id == section_code
            ]
        lines.extend(
            f"{escape(section.section_id)}: {section.enrollment}/{section.capacity} · "
            f"{'full' if section.capacity and section.enrollment >= section.capacity else 'open'}"
            for section in sections
        )
        return "\n".join(lines)
