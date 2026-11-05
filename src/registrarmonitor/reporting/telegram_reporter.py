"""Send enrollment change reports through Telegram."""

import argparse
import asyncio
import os

from telegram import Bot
from telegram.constants import ParseMode

from ..config import get_settings
from ..models import EnrollmentSnapshot
from ..subscriptions.models import SubscriptionTarget
from ..subscriptions.payloads import watch_deep_link
from .telegram_formatting import CourseLink, render_report_chunks


class TelegramReporter:
    """Telegram reporting with configuration-managed credentials."""

    def __init__(self):
        settings = get_settings()
        token, chat = settings.telegram_bot_token, settings.telegram_chat_id
        if token is None or chat is None:
            raise ValueError(
                "Telegram credentials are missing. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID in the environment or a local .env file."
            )
        self.bot_token = token.get_secret_value()
        self.chat_id = chat.get_secret_value()
        self.text_reports_dir = settings.directories.text_reports
        self.dry_run = settings.notifications.dry_run
        self.bot = Bot(token=self.bot_token)
        self._bot_username: str | None = None

    def _read_file_content(
        self,
        file_path: str,
        encoding: str | None = None,
        limit: int = -1,
    ) -> str:
        with open(file_path, encoding=encoding) as file:
            return file.read(limit)

    async def send_text_report(
        self, file_path: str, *, snapshot: EnrollmentSnapshot | None = None
    ) -> None:
        """Read, render, and send one text report.

        With ``snapshot``, each listed course links to a one-tap watch in the bot.
        """
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"TXT file disappeared before sending: {file_path}")

        filename = os.path.basename(file_path)
        if self.dry_run:
            try:
                preview = await asyncio.to_thread(
                    self._read_file_content, file_path, "utf-8", 1000
                )
                print(
                    f"[DRY RUN] Would send TXT report: {file_path}\n"
                    f"Filename: {filename}\n"
                    f"Content Preview (first 1000 chars):\n{preview}..."
                )
            except Exception as error:
                print(f"[DRY RUN] Error reading TXT file for preview: {error}")
            return

        content = await asyncio.to_thread(self._read_file_content, file_path, "utf-8")
        await self._send_long_report(
            content, course_link=await self._course_link(snapshot)
        )
        print(f"Successfully sent TXT report: {filename}")

    async def _course_link(
        self, snapshot: EnrollmentSnapshot | None
    ) -> CourseLink | None:
        """Build the watch deep-link factory for courses in ``snapshot``."""
        if snapshot is None:
            return None
        if self._bot_username is None:
            self._bot_username = (await self.bot.get_me()).username
        username = self._bot_username

        def link(course_code: str) -> str | None:
            if not username or course_code not in snapshot.courses:
                return None
            try:
                return watch_deep_link(
                    username, SubscriptionTarget(snapshot.semester, course_code)
                )
            except ValueError:
                return None  # unrepresentable or overlong: leave the heading plain

        return link

    async def _send_long_report(
        self, content: str, *, course_link: CourseLink | None = None
    ):
        """Render and send reports split on complete course boundaries."""
        for chunk in render_report_chunks(content, course_link=course_link):
            await self.bot.send_message(
                chat_id=self.chat_id,
                text=chunk,
                parse_mode=ParseMode.MARKDOWN_V2,
            )


def main():
    parser = argparse.ArgumentParser(
        description="Telegram Reporter for Enrollment Data"
    )
    parser.add_argument("--send-txt", type=str, help="Send a specific text file")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")
    args = parser.parse_args()

    if not args.send_txt:
        parser.print_help()
        return

    reporter = TelegramReporter()
    if args.dry_run:
        reporter.dry_run = True

    async def send_file():
        if os.path.exists(args.send_txt):
            await reporter.send_text_report(args.send_txt)

    asyncio.run(send_file())


if __name__ == "__main__":
    main()
