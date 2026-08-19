"""Send enrollment change reports through Telegram."""

import argparse
import asyncio
import os

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..config import get_config
from .telegram_formatting import render_report_chunks


class TelegramReporter:
    """Telegram reporting with configuration-managed credentials."""

    def __init__(self):
        self.config = get_config()
        telegram_config = self.config.get("telegram", {})
        self.bot_token = telegram_config.get("bot_token")
        self.chat_id = telegram_config.get("chat_id")
        if not self.bot_token or not self.chat_id:
            raise ValueError(
                "Telegram credentials are missing. Set TELEGRAM_BOT_TOKEN and "
                "TELEGRAM_CHAT_ID in the environment or a local .env file."
            )
        self.text_reports_dir = self.config["directories"]["text_reports"]
        self.file_write_delay = self.config.get("notifications", {}).get(
            "file_write_delay", 3
        )
        self.dry_run = self.config.get("notifications", {}).get("dry_run", False)
        self.bot = Bot(token=self.bot_token)

    def _read_file_content(
        self,
        file_path: str,
        encoding: str | None = None,
        limit: int = -1,
    ) -> str:
        with open(file_path, encoding=encoding) as file:
            return file.read(limit)

    async def send_text_report(self, file_path: str):
        """Read, render, and send one text report."""
        await asyncio.sleep(self.file_write_delay)
        if not os.path.exists(file_path):
            print(f"TXT file {file_path} disappeared before sending.")
            return

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

        try:
            content = await asyncio.to_thread(
                self._read_file_content, file_path, "utf-8"
            )
            await self._send_long_report(content)
            print(f"Successfully sent TXT report: {filename}")
        except TelegramError as error:
            print(f"Error sending TXT report {filename}: {error}")
        except FileNotFoundError:
            print(f"Error: TXT file not found at {file_path} during send attempt.")
        except Exception as error:
            print(
                f"An unexpected error occurred sending TXT report {filename}: {error}"
            )

    async def _send_long_report(self, content: str):
        """Render and send reports split on complete course boundaries."""
        for chunk in render_report_chunks(content):
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
