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
    WebAppInfo,
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
from ..website.share import published_course_share_url
from .catalog import SubscriptionCatalog
from .formatting import render_personal_digest
from .models import BotDiagnostics, SubscriptionTarget
from .payloads import (
    decode_course_import,
    decode_target,
    decode_watch_payload,
    encode_target,
)
from .store import SubscriptionStore

CatalogProvider = Callable[[], Awaitable[SubscriptionCatalog | None]]
DiagnosticsProvider = Callable[[], Awaitable[BotDiagnostics]]
CourseShareUrlProvider = Callable[[str, str], str | None]
PAGE_SIZE = 6
MAX_COURSE_SECTIONS = 12
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
        diagnostics: DiagnosticsProvider | None = None,
        course_share_url: CourseShareUrlProvider = published_course_share_url,
        app_url: str | None = None,
    ) -> None:
        self.store = store
        self._catalog_provider = catalog
        self.test_user_id = test_user_id
        self._diagnostics_provider = diagnostics
        self._course_share_url = course_share_url
        # Set when the Mini App is enabled: the site to open inside Telegram.
        self._app_url = app_url

    def register(self, application: Application) -> None:
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("watch", self.watch))
        application.add_handler(CommandHandler("import", self.import_watches))
        application.add_handler(CommandHandler("watches", self.watches))
        application.add_handler(CommandHandler("help", self.help))
        if self.test_user_id is not None:
            application.add_handler(CommandHandler("test", self.developer_diagnostics))
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
                link = decode_watch_payload(args[0])
            except ValueError:
                await message.reply_text(
                    "This subscription link is invalid. Use /watch to search.",
                    reply_markup=self._home_keyboard(),
                )
                return
            catalog = await self._catalog_provider()
            target = catalog.resolve_link(link) if catalog else None
            if catalog is None or target is None:
                await message.reply_text(
                    "That course or section is not in the latest snapshot. Use "
                    "/watch to search.",
                    reply_markup=self._home_keyboard(),
                )
                return
            course = catalog.course(target.course_code)
            if course is None:
                return
            share_url = self._course_share_url(
                catalog.snapshot.semester, course.course_code
            )
            await message.reply_text(
                f"{self._course_text(catalog.snapshot.semester, course, catalog.snapshot.timestamp, share_url=share_url, section_code=target.section_code)}\n\n"
                f"Watch <code>{escape(self._target_label(target))}</code>?",
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
            "Get a private message when enrollment changes for a watched course "
            "or section.\n\n"
            "Type one or more course codes to add them now, for example "
            "<code>CSCI 115</code> or <code>CSCI 115, PHYS 161 / 1L</code>.\n\n"
            f"<b>Active semester:</b> {escape(semester)}",
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
            await message.reply_text(
                self._watch_prompt(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._add_watch_keyboard(),
            )
            return
        user = update.effective_user
        if user is not None:
            await self._add_exact_watches(message.reply_text, query, user.id)

    async def watch_text(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or message.text is None or user is None:
            return
        await self._add_exact_watches(message.reply_text, message.text, user.id)

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

    async def watches(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
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

    async def help(self, update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        if message is None:
            return
        await message.reply_text(
            "<b>Add a watch</b>\n"
            "Use /watch, then type an exact course code or section:\n"
            "<code>CSCI 115</code>\n"
            "<code>CSCI 115 / 1L</code>\n\n"
            "You can also paste the message copied from the website."
            + (" To browse and search, open the app." if self._app_url else "")
            + "\n\n<b>My watches</b>\n"
            "Use /watches to review watches. Data and settings is available there.",
            parse_mode=ParseMode.HTML,
        )

    async def developer_diagnostics(
        self, update: Update, context: ContextTypes.DEFAULT_TYPE
    ) -> None:
        """Show private operational diagnostics to the configured operator."""
        if not await self._prepare_private(update):
            return
        message = update.effective_message
        user = update.effective_user
        if message is None or user is None or self.test_user_id != user.id:
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
        diagnostics = (
            await self._diagnostics_provider()
            if self._diagnostics_provider is not None
            else None
        )
        if diagnostics is None:
            runtime_status = "Bot: running (runtime statistics unavailable)"
            recent = "• Recent logs unavailable."
        else:
            runtime_status = (
                f"Bot: running (uptime {escape(diagnostics.uptime)})\n"
                f"Users: {diagnostics.users} total, {diagnostics.active_users} active\n"
                f"Pending deliveries: {diagnostics.pending_deliveries}"
            )
            event_lines = "\n".join(
                f"• <code>{escape(event)}</code>" for event in diagnostics.recent_logs
            )
            recent = event_lines or "• No recent log records."
        await message.reply_text(
            "<b>Developer diagnostics</b>\n\n"
            f"{snapshot}\n"
            f"{runtime_status}\n"
            f"Watches: {count}\n\n"
            f"<b>Recent logs</b>\n{recent}\n\n"
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
        if user is None:
            return
        await query.answer()
        if (
            not await self._prepare_private(update, reply=False)
            or query.message is None
        ):
            return
        data = query.data or ""
        user_id = user.id

        if data.startswith("detail:"):
            await self._view_watch(query.edit_message_text, data[7:], user_id)
        elif data == "import:confirm":
            await self._confirm_import(
                query.edit_message_text, user_id, context.user_data
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
            await query.edit_message_text(
                self._watch_prompt(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._add_watch_keyboard(),
            )
        elif data == "settings":
            await self._show_settings(query.edit_message_text)
        elif data == "clear":
            await query.edit_message_text(
                "Clear all watches?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Confirm clear", callback_data="clear:yes"
                            )
                        ],
                        [InlineKeyboardButton("Back", callback_data="settings")],
                    ]
                ),
            )
        elif data == "clear:yes":
            count = await asyncio.to_thread(self.store.clear_subscriptions, user_id)
            await query.edit_message_text(
                f"Cleared {count} watch(es).",
                reply_markup=InlineKeyboardMarkup(
                    [[InlineKeyboardButton("My watches", callback_data="subs:0")]]
                ),
            )
        elif data == "delete":
            await query.edit_message_text(
                "Delete all of your bot data?",
                reply_markup=InlineKeyboardMarkup(
                    [
                        [
                            InlineKeyboardButton(
                                "Confirm delete", callback_data="delete:yes"
                            )
                        ],
                        [InlineKeyboardButton("Back", callback_data="settings")],
                    ]
                ),
            )
        elif data == "delete:yes":
            await asyncio.to_thread(self.store.delete_user, user_id)
            await query.edit_message_text(
                "Your bot data was deleted.", reply_markup=self._home_keyboard()
            )
        else:
            await query.edit_message_text("This button is no longer valid.")

    async def _prepare_private(self, update: Update, *, reply: bool = True) -> bool:
        chat = update.effective_chat
        user = update.effective_user
        if chat is None or user is None:
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

    async def _add_exact_watches(self, send, query: str, user_id: int) -> None:
        """Add watches for exact course or section codes; browsing is in the app."""
        # Mini App-superseded multi-step search, browsing, and section pickers are
        # gone: anything that is not an exact code gets the prompt back.
        catalog = await self._catalog_provider()
        if catalog is None:
            await send(
                "No enrollment snapshot is available.\n\n" + self._watch_prompt(),
                parse_mode=ParseMode.HTML,
                reply_markup=self._add_watch_keyboard(),
            )
            return
        parts = self._watch_query_parts(query)
        if len(parts) > 1:
            await self._show_multiple_exact_watches(send, parts, catalog, user_id)
            return
        exact_target = catalog.exact_target(query)
        if exact_target is not None:
            watched = set(
                await asyncio.to_thread(self.store.list_subscriptions, user_id)
            )
            course_target = SubscriptionTarget(
                exact_target.semester,
                exact_target.course_code,
            )
            already_covered = exact_target in watched or (
                not exact_target.is_course and course_target in watched
            )
            share_url = None
            if not already_covered:
                share_url = self._course_share_url(
                    catalog.snapshot.semester,
                    exact_target.course_code,
                )
            changed = await asyncio.to_thread(
                self.store.add_watch, user_id, exact_target
            )
            if not changed:
                share_url = None
            if not changed and not exact_target.is_course:
                message = (
                    "Your whole-course watch already includes "
                    f"<code>{escape(self._target_label(exact_target))}</code>."
                )
            else:
                message = (
                    f"Watching <code>{escape(self._target_label(exact_target))}</code>."
                )
                if share_url is not None:
                    message += f'\n\n<a href="{escape(share_url, quote=True)}">View on the website</a>'
            await send(
                message,
                parse_mode=ParseMode.HTML,
                **(
                    {"link_preview_options": self._link_preview_options()}
                    if share_url is not None
                    else {}
                ),
                reply_markup=InlineKeyboardMarkup(
                    [
                        [self._target_button("Stop watching", "rm", exact_target)],
                        [InlineKeyboardButton("My watches", callback_data="subs:0")],
                    ]
                ),
            )
            return
        await send(
            "No exact match found.\n\n" + self._watch_prompt(),
            parse_mode=ParseMode.HTML,
            reply_markup=self._add_watch_keyboard(),
        )

    async def _show_multiple_exact_watches(
        self,
        send,
        parts: list[str],
        catalog: SubscriptionCatalog,
        user_id: int,
    ) -> None:
        targets = []
        invalid = []
        for part in parts:
            target = catalog.exact_target(part)
            if target is None:
                invalid.append(part)
            elif target not in targets:
                targets.append(target)
        if invalid:
            invalid_lines = "\n".join(
                f"• <code>{escape(part)}</code>" for part in invalid
            )
            await send(
                "I could not find these exact course or section codes:\n"
                f"{invalid_lines}\n\n{self._watch_prompt()}",
                parse_mode=ParseMode.HTML,
                reply_markup=self._add_watch_keyboard(),
            )
            return
        added = 0
        for target in targets:
            added += await asyncio.to_thread(self.store.add_watch, user_id, target)
        if added == len(targets):
            heading = f"Added {added} watches."
        elif added:
            heading = (
                f"Added {added} watches. {len(targets) - added} were already saved."
            )
        else:
            heading = "All requested watches were already saved."
        summary = "\n".join(
            f"• <code>{escape(self._target_label(target))}</code>" for target in targets
        )
        await send(
            f"<b>{heading}</b>\n\n{summary}",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [[InlineKeyboardButton("My watches", callback_data="subs:0")]]
            ),
        )

    async def _view_watch(
        self,
        edit,
        payload: str,
        user_id: int,
    ) -> None:
        try:
            target = decode_target(payload)
        except ValueError:
            await edit("This button is no longer valid.")
            return
        catalog = await self._catalog_provider()
        watched = set(await asyncio.to_thread(self.store.list_subscriptions, user_id))
        if target not in watched:
            await edit("This watch no longer exists.")
            return
        if catalog is None or not catalog.resolve(target):
            await edit("That course or section is not in the latest snapshot.")
            return
        course = catalog.course(target.course_code)
        if course is None:
            return
        scope = (
            "Whole course"
            if target.is_course
            else f"Section <code>{escape(target.section_code)}</code>"
        )
        share_url = self._course_share_url(target.semester, course.course_code)
        text = (
            "<b>Watch details</b>\n\n"
            f"<b>Current scope:</b> {scope}\n\n"
            + self._course_text(
                target.semester,
                course,
                catalog.snapshot.timestamp,
                share_url=share_url,
                section_code=target.section_code,
            )
        )
        await edit(
            text,
            parse_mode=ParseMode.HTML,
            link_preview_options=self._link_preview_options(),
            reply_markup=InlineKeyboardMarkup(
                [
                    [self._target_button("Stop watching", "rm", target)],
                    [
                        InlineKeyboardButton(
                            "Back to my watches", callback_data="subs:0"
                        )
                    ],
                ]
            ),
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
            message = f"{'Watching' if subscribe else 'Removed'} <code>{escape(self._target_label(target))}</code>."
        else:
            message = (
                f"Already watching <code>{escape(self._target_label(target))}</code>."
                if subscribe
                else f"Already removed <code>{escape(self._target_label(target))}</code>."
            )
        next_action = "rm" if subscribe else "sub"
        next_label = "Stop watching" if subscribe else "Watch again"
        await edit(
            message,
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [self._target_button(next_label, next_action, target)],
                    [InlineKeyboardButton("My watches", callback_data="subs:0")],
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
            f"Stop watching <code>{escape(self._target_label(target))}</code>?",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [self._target_button("Confirm remove", "unsub", target)],
                    [self._target_button("Back", "detail", target)],
                ]
            ),
        )

    async def _show_subscriptions(self, send, user_id: int, *, page: int) -> None:
        targets = await asyncio.to_thread(self.store.list_subscriptions, user_id)
        if not targets:
            await send(
                "<b>My watches</b>\n\nYou have no watches yet. Add one to get private enrollment-change messages.",
                parse_mode=ParseMode.HTML,
                reply_markup=InlineKeyboardMarkup(
                    [
                        [InlineKeyboardButton("Add a watch", callback_data="watch")],
                        [
                            InlineKeyboardButton(
                                "Data and settings", callback_data="settings"
                            )
                        ],
                    ]
                ),
            )
            return
        page_count = max(1, (len(targets) + PAGE_SIZE - 1) // PAGE_SIZE)
        page = min(page, page_count - 1)
        visible = targets[page * PAGE_SIZE : (page + 1) * PAGE_SIZE]
        edit_buttons = [
            self._target_button(self._target_label(target), "detail", target)
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
        keyboard.append([InlineKeyboardButton("Add a watch", callback_data="watch")])
        keyboard.append(
            [InlineKeyboardButton("Data and settings", callback_data="settings")]
        )
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
            "<b>My watches</b>\n\n" + "\n\n".join(sections),
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

    def _home_keyboard(self) -> InlineKeyboardMarkup:
        return InlineKeyboardMarkup(
            [
                [InlineKeyboardButton("Add a watch", callback_data="watch")],
                [InlineKeyboardButton("My watches", callback_data="subs:0")],
            ]
        )

    def _add_watch_keyboard(self) -> InlineKeyboardMarkup:
        rows = [[InlineKeyboardButton("My watches", callback_data="subs:0")]]
        if self._app_url:
            rows.insert(
                0,
                [
                    InlineKeyboardButton(
                        "Browse courses", web_app=WebAppInfo(url=self._app_url)
                    )
                ],
            )
        return InlineKeyboardMarkup(rows)

    def _watch_prompt(self) -> str:
        prompt = (
            "Send a course code to add it now, for example <code>CSCI 115</code>. "
            "For one section, send <code>CSCI 115 / 1L</code>. For several exact "
            "watches, separate them with commas or new lines, for example "
            "<code>CSCI 115, PHYS 161 / 1L</code>."
        )
        if self._app_url:
            prompt += " To search and pick sections, open the app."
        return prompt

    @staticmethod
    def _watch_query_parts(query: str) -> list[str]:
        return [part.strip() for part in re.split(r"[,;\n]+", query) if part.strip()]

    @staticmethod
    async def _show_settings(send) -> None:
        await send(
            "<b>Data and settings</b>\n\nManage your saved watches and private bot data.",
            parse_mode=ParseMode.HTML,
            reply_markup=InlineKeyboardMarkup(
                [
                    [InlineKeyboardButton("Clear all watches", callback_data="clear")],
                    [
                        InlineKeyboardButton(
                            "Delete my bot data", callback_data="delete"
                        )
                    ],
                    [
                        InlineKeyboardButton(
                            "Back to my watches", callback_data="subs:0"
                        )
                    ],
                ]
            ),
        )

    @staticmethod
    def _course_text(
        semester: str,
        course,
        updated_at: str,
        *,
        section_code: str = "",
        share_url: str | None,
    ) -> str:
        title = f" - {escape(course.course_title)}" if course.course_title else ""
        lines = [
            f"<b>Semester:</b> {escape(semester)}",
            "",
            f"<code>{escape(course.course_code)}</code>{title}",
            f"<i>Updated {escape(updated_at)}</i>",
        ]
        sections = SubscriptionInteractions._sorted_sections(course)
        if section_code:
            sections = [
                section for section in sections if section.section_id == section_code
            ]
        elif len(sections) > MAX_COURSE_SECTIONS:
            lines.extend(
                ["", f"<i>First {MAX_COURSE_SECTIONS} of {len(sections)} sections</i>"]
            )
            sections = sections[:MAX_COURSE_SECTIONS]
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
        if share_url:
            lines.extend(
                [
                    "",
                    f'<a href="{escape(share_url, quote=True)}">View on the website</a>',
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
    def _link_preview_options() -> LinkPreviewOptions:
        return LinkPreviewOptions(prefer_large_media=True, show_above_text=True)
