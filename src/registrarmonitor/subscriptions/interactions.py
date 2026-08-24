"""Private-chat commands and inline interactions for course subscriptions."""

import asyncio
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable, MutableMapping
from copy import deepcopy
from html import escape

from telegram import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    LinkPreviewOptions,
    Update,
)
from telegram.constants import ChatType, ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)
from telegram.helpers import escape_markdown

from ..data.snapshot_comparator import SnapshotComparator
from ..utils import get_section_sort_key
from ..website.config import BASE_URL, course_to_slug, semester_to_slug
from .catalog import SubscriptionCatalog
from .formatting import render_personal_digest
from .models import SubscriptionTarget
from .payloads import (
    decode_course_import,
    decode_target,
    encode_target,
)
from .store import SubscriptionStore

CatalogProvider = Callable[[], Awaitable[SubscriptionCatalog | None]]
PAGE_SIZE = 6
CATALOG_PAGE_SIZE = 8
SECTION_PAGE_SIZE = 12
SECTION_TYPE_LABELS = {
    "L": "Lectures",
    "S": "Seminars",
    "D": "Discussions",
    "R": "Recitations",
    "B": "Labs",
    "Lb": "Labs",
}


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
        application.add_handler(CommandHandler("catalog", self.catalog))
        application.add_handler(CommandHandler("import", self.import_watches))
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
                link_preview_options=self._link_preview_options(),
                reply_markup=InlineKeyboardMarkup(
                    [[self._target_button("Confirm", "sub", target)]]
                ),
            )
            return
        catalog = await self._catalog_provider()
        semester = catalog.snapshot.semester if catalog else "not available"
        await message.reply_text(
            f"<b>Registrar Monitor</b>\n\n"
            "Track enrollment changes for a course or specific sections.\n\n"
            f"<b>Semester:</b> {escape(semester)}",
            parse_mode=ParseMode.HTML,
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
            await message.reply_text(self._watch_prompt())
            return
        user = update.effective_user
        if user is not None:
            await self._show_search(
                message.reply_text, query, user.id, context.user_data
            )

    async def watch_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or message.text is None or user is None:
            return
        if context.user_data is not None and context.user_data.pop(
            "awaiting_sections", False
        ):
            await self._accept_typed_sections(
                message.reply_text, message.text, user.id, context.user_data
            )
            return
        if context.user_data is None or not context.user_data.pop(
            "awaiting_watch", False
        ):
            return
        await self._show_search(
            message.reply_text, message.text, user.id, context.user_data
        )

    async def catalog(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None:
            return
        await self._show_catalog(message.reply_text, user.id, context.user_data, page=0)

    async def import_watches(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None or message.text is None:
            return
        await self._show_import_receipt(
            message.reply_text, message.text, context.user_data
        )

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
            targets = await asyncio.to_thread(self.store.list_subscriptions, user.id)
            if not targets:
                await message.reply_text(
                    "You have no watches yet.",
                    reply_markup=InlineKeyboardMarkup(
                        [[InlineKeyboardButton("Add watch", callback_data="watch")]]
                    ),
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
                            for target in targets[index : index + 2]
                        ]
                        for index in range(0, len(targets), 2)
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
        await self._show_course(
            message.reply_text,
            catalog,
            matches[0],
            user.id,
            context.user_data,
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
            "<b>Alert settings</b>\n\n"
            "Personal alerts follow successful channel reports; they are not sent "
            "after every registrar poll.",
            parse_mode=ParseMode.HTML,
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
            "<b>Courses</b>\n"
            "/watch - add course or section watches\n"
            "/catalog - browse every current course\n"
            "/import - paste starred courses copied from the website\n"
            "/status - latest stored enrollment\n\n"
            "<b>Your alerts</b>\n"
            "/subscriptions - review or edit watches\n"
            "/settings - clear watches or delete bot data",
            parse_mode=ParseMode.HTML,
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
        count = len(await asyncio.to_thread(self.store.list_subscriptions, user.id))
        await message.reply_text(
            "<b>Developer test mode</b>\n\n"
            f"{snapshot}\n"
            f"Watches: {count}\n\n"
            "Previewing a simulated change does not modify enrollment or send a "
            "real notification.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Simulate watched change",
                            callback_data="test:simulate",
                        )
                    ]
                ]
            ),
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
            await self._view_target(
                query.edit_message_text, data[5:], user_id, context.user_data
            )
        elif data.startswith("pick:"):
            await self._toggle_section_selection(
                query.edit_message_text, data[5:], user_id, context.user_data
            )
        elif data.startswith("apply:"):
            await self._apply_section_selection(
                query.edit_message_text, data[6:], user_id, context.user_data
            )
        elif data == "sections:confirm":
            await self._confirm_section_selection(
                query.edit_message_text, user_id, context.user_data
            )
        elif data.startswith("spage:"):
            try:
                page = max(0, int(data[6:]))
            except ValueError:
                page = 0
            await self._show_current_course_page(
                query.edit_message_text, user_id, context.user_data, page
            )
        elif data.startswith("type:"):
            await self._request_typed_sections(
                query.edit_message_text, data[5:], user_id, context.user_data
            )
        elif data == "import:confirm":
            await self._confirm_import(
                query.edit_message_text, user_id, context.user_data
            )
        elif data.startswith("catalog:"):
            try:
                page = max(0, int(data[8:]))
            except ValueError:
                page = 0
            await self._show_catalog(
                query.edit_message_text, user_id, context.user_data, page=page
            )
        elif data == "test:simulate" and self.test_user_id == user_id:
            await self._simulate_change_preview(query.edit_message_text, user_id)
        elif data.startswith("sub:"):
            await self._change_subscription(
                query.edit_message_text, user_id, data[4:], True
            )
        elif data.startswith("rm:"):
            await self._confirm_unsubscribe(query.edit_message_text, data[3:])
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
            await query.edit_message_text(self._watch_prompt())
        elif data == "clear":
            await query.edit_message_text(
                "Clear all subscriptions?",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Confirm clear", callback_data="clear:yes")]]
                ),
            )
        elif data == "clear:yes":
            count = await asyncio.to_thread(self.store.clear_subscriptions, user_id)
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
            await asyncio.to_thread(self.store.delete_user, user_id)
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
        await asyncio.to_thread(
            self.store.touch_user,
            telegram_user_id=user.id,
            private_chat_id=chat.id,
        )
        return True

    def _is_allowed(self, user_id: int) -> bool:
        return self.test_user_id is None or user_id == self.test_user_id

    async def _show_search(
        self,
        send,
        query: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        catalog = await self._catalog_provider()
        if catalog is None:
            await send("No enrollment snapshot is available.")
            return
        exact_target = catalog.exact_target(query)
        if exact_target is not None:
            changed = await asyncio.to_thread(
                self.store.add_watch, user_id, exact_target
            )
            if not changed and not exact_target.is_course:
                message = (
                    f"Your whole-course watch already includes "
                    f"{self._target_label(exact_target)}. Use Edit watch to switch "
                    "to selected sections."
                )
            else:
                message = f"Watching {self._target_label(exact_target)}."
            await send(
                message,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [self._target_button("Edit watch", "view", exact_target)],
                        [InlineKeyboardButton("My watches", callback_data="subs:0")],
                    ]
                ),
            )
            return
        matches = catalog.search(query)
        if not matches:
            await send("No matching course found.")
            return
        if len(matches) == 1:
            await self._show_course(send, catalog, matches[0], user_id, user_data)
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

    async def _show_course(
        self,
        send,
        catalog: SubscriptionCatalog,
        course,
        user_id: int,
        user_data: MutableMapping | None,
        *,
        page: int = 0,
    ) -> None:
        target = SubscriptionTarget(catalog.snapshot.semester, course.course_code)
        watched = set(await asyncio.to_thread(self.store.list_subscriptions, user_id))
        selected = set() if target in watched else self._section_codes(watched, target)
        self._save_section_selection(user_data, target, selected)
        self._save_current_course(user_data, target, page)
        await send(
            self._course_text(
                target.semester, course, catalog.snapshot.timestamp, page=page
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=self._link_preview_options(),
            reply_markup=self._course_keyboard(
                target.semester, course, watched, selected, page=page
            ),
        )

    async def _view_target(
        self,
        edit,
        payload: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
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
        if course is not None:
            await self._show_course(edit, catalog, course, user_id, user_data)

    async def _toggle_section_selection(
        self,
        edit,
        payload: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or target.is_course or not catalog.resolve(target):
            await edit("That section is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is None:
            return
        course_target = SubscriptionTarget(target.semester, target.course_code)
        selected = await self._load_section_selection(user_data, course_target, user_id)
        if target.section_code in selected:
            selected.remove(target.section_code)
        else:
            selected.add(target.section_code)
        self._save_section_selection(user_data, course_target, selected)
        page = self._current_section_page(user_data)
        await edit(
            self._course_text(
                target.semester, course, catalog.snapshot.timestamp, page=page
            ),
            parse_mode=ParseMode.HTML,
            link_preview_options=self._link_preview_options(),
            reply_markup=self._course_keyboard(
                target.semester,
                course,
                set(await asyncio.to_thread(self.store.list_subscriptions, user_id)),
                selected,
                page=page,
            ),
        )

    async def _apply_section_selection(
        self,
        edit,
        payload: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not target.is_course or not catalog.resolve(target):
            await edit("That course is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is None:
            return
        selected = await self._load_section_selection(user_data, target, user_id)
        self._save_current_course(
            user_data, target, self._current_section_page(user_data)
        )
        summary = (
            "\n".join(
                f"• <code>{escape(section_id)}</code>"
                for section_id in sorted(selected)
            )
            or "• No section watches"
        )
        await edit(
            "<b>Review section watches</b>\n\n"
            f"Semester: {escape(target.semester)}\n"
            f"<b>{escape(target.course_code)}</b>\n{summary}\n\n"
            "This replaces the section watches for this course.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Confirm", callback_data="sections:confirm"
                        ),
                        self._target_button("Back", "view", target),
                    ]
                ]
            ),
        )

    async def _confirm_section_selection(
        self,
        edit,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        target = self._current_course(user_data)
        if target is None:
            await edit("This section selection has expired.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not catalog.resolve(target):
            await edit("That course is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is None:
            return
        selected = await self._load_section_selection(user_data, target, user_id)
        await asyncio.to_thread(
            self.store.replace_section_watches, user_id, target, selected
        )
        count = len(selected)
        await edit(
            f"<b>Section watches updated</b>\n\n"
            f"{escape(target.course_code)} - {count} "
            f"{'section' if count == 1 else 'sections'} selected.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        self._target_button("Review course", "view", target),
                        InlineKeyboardButton("My watches", callback_data="subs:0"),
                    ]
                ]
            ),
        )

    async def _request_typed_sections(
        self,
        edit,
        payload: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not target.is_course or not catalog.resolve(target):
            await edit("That course is not in the latest snapshot.")
            return
        self._save_current_course(
            user_data, target, self._current_section_page(user_data)
        )
        if user_data is not None:
            user_data["awaiting_sections"] = True
        await edit(
            f"<b>Type sections for {escape(target.course_code)}</b>\n\n"
            "Send section IDs separated by spaces or commas, for example:\n"
            "<code>1L, 3L, 10PLb</code>\n\n"
            "Send <code>none</code> to clear the section selection.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[self._target_button("Cancel", "view", target)]]
            ),
        )

    async def _accept_typed_sections(
        self,
        send,
        text: str,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        target = self._current_course(user_data)
        if target is None:
            await send("This section selection has expired. Use /watch again.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not catalog.resolve(target):
            await send("That course is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is None:
            return
        available = {section_id.upper(): section_id for section_id in course.sections}
        requested = (
            []
            if text.strip().lower() == "none"
            else [
                value.upper() for value in re.split(r"[\s,;]+", text.strip()) if value
            ]
        )
        unknown = sorted(set(requested) - available.keys())
        if unknown:
            await send(
                "Unknown section IDs: "
                + ", ".join(f"<code>{escape(value)}</code>" for value in unknown)
                + "\nTry again with IDs shown in the course status.",
                parse_mode=ParseMode.HTML,
            )
            if user_data is not None:
                user_data["awaiting_sections"] = True
            return
        selected = {available[value] for value in requested}
        self._save_section_selection(user_data, target, selected)
        await self._apply_section_selection(
            send, encode_target(target), user_id, user_data
        )

    async def _show_current_course_page(
        self,
        edit,
        user_id: int,
        user_data: MutableMapping | None,
        page: int,
    ) -> None:
        target = self._current_course(user_data)
        if target is None:
            await edit("This course selection has expired.")
            return
        catalog = await self._catalog_provider()
        if catalog is None or not catalog.resolve(target):
            await edit("That course is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is not None:
            selected = await self._load_section_selection(user_data, target, user_id)
            page_count = max(
                1, (len(course.sections) + SECTION_PAGE_SIZE - 1) // SECTION_PAGE_SIZE
            )
            page = min(page, page_count - 1)
            self._save_current_course(user_data, target, page)
            await edit(
                self._course_text(
                    target.semester, course, catalog.snapshot.timestamp, page=page
                ),
                parse_mode=ParseMode.HTML,
                link_preview_options=self._link_preview_options(),
                reply_markup=self._course_keyboard(
                    target.semester,
                    course,
                    set(
                        await asyncio.to_thread(self.store.list_subscriptions, user_id)
                    ),
                    selected,
                    page=page,
                ),
            )

    async def _show_catalog(
        self,
        send,
        user_id: int,
        user_data: MutableMapping | None,
        *,
        page: int,
    ) -> None:
        del user_id, user_data
        catalog = await self._catalog_provider()
        if catalog is None:
            await send("No enrollment snapshot is available.")
            return
        courses = sorted(
            catalog.snapshot.courses.values(), key=lambda course: course.course_code
        )
        page_count = max(1, (len(courses) + CATALOG_PAGE_SIZE - 1) // CATALOG_PAGE_SIZE)
        page = min(page, page_count - 1)
        visible = courses[page * CATALOG_PAGE_SIZE : (page + 1) * CATALOG_PAGE_SIZE]
        lines = []
        buttons = []
        for course in visible:
            status = (
                "FULL"
                if course.is_filled
                else "NEAR"
                if course.is_near_filled
                else "OPEN"
            )
            lines.append(
                f"[{status}] <code>{escape(course.course_code)}</code>  "
                f"{course.average_fill:.0%}"
            )
            buttons.append(
                self._target_button(
                    course.course_code,
                    "view",
                    SubscriptionTarget(catalog.snapshot.semester, course.course_code),
                )
            )
        keyboard = [buttons[index : index + 2] for index in range(0, len(buttons), 2)]
        navigation = []
        if page > 0:
            navigation.append(
                InlineKeyboardButton("Previous", callback_data=f"catalog:{page - 1}")
            )
        if page + 1 < page_count:
            navigation.append(
                InlineKeyboardButton("Next", callback_data=f"catalog:{page + 1}")
            )
        if navigation:
            keyboard.append(navigation)
        keyboard.append([InlineKeyboardButton("Search", callback_data="watch")])
        await send(
            f"<b>Semester:</b> {escape(catalog.snapshot.semester)}\n"
            f"<b>Course catalog</b> - {page + 1}/{page_count}\n\n"
            + "\n".join(lines)
            + "\n\n[OPEN] Open   [NEAR] Near full   [FULL] Full",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(keyboard),
        )

    async def _show_import_receipt(
        self,
        send,
        value: str,
        user_data: MutableMapping | None,
    ) -> None:
        try:
            semester, course_codes = decode_course_import(value)
        except ValueError:
            await send(
                "This website selection is invalid. Copy it again from the "
                "dashboard's starred-course button."
            )
            return
        catalog = await self._catalog_provider()
        if catalog is None or semester != catalog.snapshot.semester:
            await send("This import is not for the current enrollment semester.")
            return
        targets = []
        for course_code in course_codes:
            matches = catalog.search(course_code)
            if len(matches) != 1 or matches[0].course_code != course_code:
                await send("A bookmarked course is no longer in the current catalog.")
                return
            targets.append(SubscriptionTarget(semester, matches[0].course_code))
        if user_data is not None:
            user_data["pending_import"] = [encode_target(target) for target in targets]
        summary = "\n".join(
            f"• <code>{escape(target.course_code)}</code> - whole course"
            for target in targets
        )
        await send(
            "<b>Review website import</b>\n\n"
            f"Semester: {escape(semester)}\n{summary}\n\n"
            "These starred courses will be added as whole-course watches. Nothing is "
            "added until you confirm.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [
                        InlineKeyboardButton(
                            "Confirm import", callback_data="import:confirm"
                        )
                    ],
                    [InlineKeyboardButton("Cancel", callback_data="subs:0")],
                ]
            ),
        )

    async def _confirm_import(
        self,
        edit,
        user_id: int,
        user_data: MutableMapping | None,
    ) -> None:
        payloads = user_data.pop("pending_import", []) if user_data is not None else []
        try:
            targets = [decode_target(payload) for payload in payloads]
        except ValueError:
            targets = []
        catalog = await self._catalog_provider()
        if (
            not targets
            or catalog is None
            or any(
                not target.is_course or not catalog.resolve(target)
                for target in targets
            )
        ):
            await edit("This website import has expired.")
            return
        for target in targets:
            await asyncio.to_thread(self.store.add_watch, user_id, target)
        summary = "\n".join(
            f"• <code>{escape(target.course_code)}</code>" for target in targets
        )
        await edit(
            f"<b>Website bookmarks imported</b>\n\n{summary}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("My watches", callback_data="subs:0")]]
            ),
        )

    async def _simulate_change_preview(self, edit, user_id: int) -> None:
        catalog = await self._catalog_provider()
        if catalog is None:
            await edit("No enrollment snapshot is available.")
            return
        stored_targets = await asyncio.to_thread(self.store.list_subscriptions, user_id)
        targets = [target for target in stored_targets if catalog.resolve(target)]
        if not targets:
            await edit("Add at least one current watch before simulating a change.")
            return
        target = targets[0]
        previous = deepcopy(catalog.snapshot)
        current = deepcopy(catalog.snapshot)
        course = current.courses[target.course_code]
        section = (
            course.sections.get(target.section_code)
            if not target.is_course
            else next(iter(self._sorted_sections(course)), None)
        )
        if section is None:
            await edit("The watched course has no current sections to simulate.")
            return
        section.enrollment = (
            section.enrollment + 1
            if not section.capacity or section.enrollment < section.capacity
            else max(0, section.enrollment - 1)
        )
        section.fill = section.enrollment / section.capacity if section.capacity else 0
        course.average_fill = sum(item.fill for item in course.sections.values()) / len(
            course.sections
        )
        course._invalidate_cache()
        comparison = SnapshotComparator().compare_snapshots(current, previous)
        chunks = render_personal_digest(comparison, current, previous, targets)
        if not chunks:
            await edit("The selected watch did not produce a reportable simulation.")
            return
        heading = escape_markdown(
            "Simulation only - no enrollment data changed.", version=2
        )
        await edit(
            f"{heading}\n\n{chunks[0]}",
            parse_mode=ParseMode.MARKDOWN_V2,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("My watches", callback_data="subs:0")]]
            ),
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
        operation = self.store.add_watch if subscribe else self.store.unsubscribe
        changed = await asyncio.to_thread(
            operation,
            user_id,
            target,
        )
        if changed:
            message = f"{'Watching' if subscribe else 'Removed'} {self._target_label(target)}."
        else:
            message = (
                f"Already watching {self._target_label(target)}."
                if subscribe
                else f"Already removed {self._target_label(target)}."
            )
        next_action = "rm" if subscribe else "sub"
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

    async def _confirm_unsubscribe(self, edit, payload: str) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        await edit(
            f"Stop watching {self._target_label(target)}?",
            reply_markup=InlineKeyboardMarkup(
                [
                    [self._target_button("Confirm remove", "unsub", target)],
                    [self._target_button("Back", "view", target)],
                ]
            ),
        )

    async def _show_subscriptions(self, send, user_id: int, *, page: int) -> None:
        targets = await asyncio.to_thread(self.store.list_subscriptions, user_id)
        if not targets:
            await send(
                "<b>Your watches</b>\n\nNo watches yet.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("Add watch", callback_data="watch")]]
                ),
            )
            return
        page_count = max(1, (len(targets) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, page_count - 1)
        visible = targets[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        edit_buttons = [
            self._target_button(f"Edit {self._target_label(target)}", "view", target)
            for target in visible
        ]
        keyboard = [
            edit_buttons[index : index + 2] for index in range(0, len(edit_buttons), 2)
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
        keyboard.append([InlineKeyboardButton("Add watch", callback_data="watch")])
        by_semester: dict[str, list[SubscriptionTarget]] = defaultdict(list)
        for target in visible:
            by_semester[target.semester].append(target)
        sections = []
        for semester, semester_targets in by_semester.items():
            labels = "\n".join(
                f"• <code>{escape(self._target_label(target))}</code>"
                for target in semester_targets
            )
            sections.append(f"<b>{escape(semester)}</b>\n{labels}")
        await send(
            "<b>Your watches</b>\n\n" + "\n\n".join(sections),
            parse_mode=ParseMode.HTML,
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
                [
                    InlineKeyboardButton("Browse catalog", callback_data="catalog:0"),
                    InlineKeyboardButton("Search", callback_data="watch"),
                ],
                [InlineKeyboardButton("My subscriptions", callback_data="subs:0")],
            ]
        )

    @staticmethod
    def _watch_prompt() -> str:
        return (
            "Send a course code to add it now, for example CSCI 115. "
            "For one section, send CSCI 115 / 1L. You can also send a title "
            "to search."
        )

    def _course_keyboard(
        self,
        semester: str,
        course,
        watched: set[SubscriptionTarget],
        selected_sections: set[str],
        *,
        page: int = 0,
    ) -> InlineKeyboardMarkup:
        course_target = SubscriptionTarget(semester, course.course_code)
        course_watched = course_target in watched
        rows: list[list[InlineKeyboardButton]] = [
            [
                self._target_button(
                    "[x] Whole course" if course_watched else "Watch whole course",
                    "rm" if course_watched else "sub",
                    course_target,
                )
            ]
        ]
        sections = self._sorted_sections(course)
        page_count = max(
            1, (len(sections) + SECTION_PAGE_SIZE - 1) // SECTION_PAGE_SIZE
        )
        page = min(page, page_count - 1)
        visible = sections[page * SECTION_PAGE_SIZE : (page + 1) * SECTION_PAGE_SIZE]
        section_buttons = []
        for section in visible:
            target = SubscriptionTarget(
                semester, course.course_code, section.section_id
            )
            section_buttons.append(
                self._target_button(
                    f"{'[x]' if section.section_id in selected_sections else '[ ]'} "
                    f"{section.section_id}",
                    "pick",
                    target,
                )
            )
        rows.extend(
            section_buttons[index : index + 2]
            for index in range(0, len(section_buttons), 2)
        )
        if page_count > 1:
            navigation = []
            if page > 0:
                navigation.append(
                    InlineKeyboardButton(
                        "Previous sections", callback_data=f"spage:{page - 1}"
                    )
                )
            navigation.append(
                InlineKeyboardButton(
                    f"{page + 1}/{page_count}", callback_data=f"spage:{page}"
                )
            )
            if page + 1 < page_count:
                navigation.append(
                    InlineKeyboardButton(
                        "Next sections", callback_data=f"spage:{page + 1}"
                    )
                )
            rows.append(navigation)
            rows.append(
                [self._target_button("Type section IDs", "type", course_target)]
            )
        rows.append(
            [
                self._target_button(
                    f"Apply section watches - {len(selected_sections)} selected",
                    "apply",
                    course_target,
                )
            ]
        )
        rows.append(
            [
                InlineKeyboardButton(
                    "Website preview",
                    url=self._course_url(semester, course.course_code),
                ),
                InlineKeyboardButton("My watches", callback_data="subs:0"),
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
        page: int = 0,
    ) -> str:
        title = f" - {escape(course.course_title)}" if course.course_title else ""
        lines = [
            f"<b>Semester:</b> {escape(semester)}",
            "",
            f"<b>{escape(course.course_code)}</b>{title}",
            f"<i>Updated {escape(updated_at)}</i>",
        ]
        sections = SubscriptionInteractions._sorted_sections(course)
        if section_code:
            sections = [
                section for section in sections if section.section_id == section_code
            ]
        elif len(sections) > SECTION_PAGE_SIZE:
            page_count = (len(sections) + SECTION_PAGE_SIZE - 1) // SECTION_PAGE_SIZE
            page = min(max(0, page), page_count - 1)
            start = page * SECTION_PAGE_SIZE
            lines.extend(
                [
                    "",
                    (
                        f"<i>Sections {start + 1}-"
                        f"{min(start + SECTION_PAGE_SIZE, len(sections))} "
                        f"of {len(sections)}</i>"
                    ),
                ]
            )
            sections = sections[start : start + SECTION_PAGE_SIZE]
        grouped: dict[str, list] = defaultdict(list)
        for section in sections:
            grouped[section.section_type].append(section)
        for section_type, group in grouped.items():
            label = SECTION_TYPE_LABELS.get(section_type, section_type or "Sections")
            width = max(len(section.section_id) for section in group)
            rows = "\n".join(
                f"{section.section_id:<{width}}  {section.enrollment:>2}/{section.capacity:<2}  "
                f"{'FULL' if section.capacity and section.enrollment >= section.capacity else 'open'}"
                for section in group
            )
            lines.extend(["", f"<b>{escape(label)}</b>", f"<pre>{escape(rows)}</pre>"])
        lines.extend(
            [
                "",
                f'<a href="{SubscriptionInteractions._course_url(semester, course.course_code)}">View on the website</a>',
            ]
        )
        return "\n".join(lines)

    @staticmethod
    def _sorted_sections(course) -> list:
        return sorted(
            course.sections.values(),
            key=lambda section: get_section_sort_key(
                section.section_id, section.section_type
            ),
        )

    @staticmethod
    def _section_codes(
        targets: set[SubscriptionTarget], course: SubscriptionTarget
    ) -> set[str]:
        return {
            target.section_code
            for target in targets
            if target.semester == course.semester
            and target.course_code == course.course_code
            and not target.is_course
        }

    @staticmethod
    def _selection_key(target: SubscriptionTarget) -> str:
        return f"section_selection:{target.semester}:{target.course_code}"

    async def _load_section_selection(
        self,
        user_data: MutableMapping | None,
        target: SubscriptionTarget,
        user_id: int,
    ) -> set[str]:
        key = self._selection_key(target)
        if user_data is not None and key in user_data:
            return set(user_data[key])
        watched = set(await asyncio.to_thread(self.store.list_subscriptions, user_id))
        selected = self._section_codes(watched, target)
        self._save_section_selection(user_data, target, selected)
        return selected

    @classmethod
    def _save_section_selection(
        cls,
        user_data: MutableMapping | None,
        target: SubscriptionTarget,
        selected: set[str],
    ) -> None:
        if user_data is not None:
            user_data[cls._selection_key(target)] = sorted(selected)

    @staticmethod
    def _save_current_course(
        user_data: MutableMapping | None,
        target: SubscriptionTarget,
        page: int,
    ) -> None:
        if user_data is not None:
            user_data["current_course"] = encode_target(target)
            user_data["section_page"] = page

    @staticmethod
    def _current_course(
        user_data: MutableMapping | None,
    ) -> SubscriptionTarget | None:
        if user_data is None:
            return None
        try:
            target = decode_target(str(user_data.get("current_course", "")))
        except ValueError:
            return None
        return (
            target
            if target.is_course
            else SubscriptionTarget(target.semester, target.course_code)
        )

    @staticmethod
    def _current_section_page(user_data: MutableMapping | None) -> int:
        if user_data is None:
            return 0
        try:
            return max(0, int(user_data.get("section_page", 0)))
        except (TypeError, ValueError):
            return 0

    @staticmethod
    def _course_url(semester: str, course_code: str) -> str:
        return (
            f"{BASE_URL}/courses/{semester_to_slug(semester)}/"
            f"{course_to_slug(course_code)}/"
        )

    @staticmethod
    def _link_preview_options() -> LinkPreviewOptions:
        return LinkPreviewOptions(prefer_large_media=True, show_above_text=False)
