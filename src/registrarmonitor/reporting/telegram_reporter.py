"""
Telegram reporting module for sending enrollment change notifications.
"""

import argparse
import asyncio
import os

from telegram import Bot
from telegram.constants import ParseMode
from telegram.error import TelegramError

from ..config import get_config


class TelegramReporter:
    """Telegram reporting functionality with configuration management."""

    def __init__(self):
        self.config = get_config()
        self.bot_token = self.config["telegram"]["bot_token"]
        self.chat_id = self.config["telegram"]["chat_id"]
        self.text_reports_dir = self.config["directories"]["text_reports"]
        self.file_write_delay = self.config.get("notifications", {}).get(
            "file_write_delay", 3
        )
        self.dry_run = self.config.get("notifications", {}).get("dry_run", False)

        self.bot = Bot(token=self.bot_token)

    def _read_file_content(
        self,
        file_path: str,
        mode: str = "r",
        encoding: str | None = None,
        limit: int = -1,
    ) -> str:
        """Helper to read file content synchronously."""
        with open(file_path, mode, encoding=encoding) as f:
            return str(f.read(limit))

    async def send_text_report(self, file_path: str):
        """Send a text report via Telegram."""
        await asyncio.sleep(self.file_write_delay)  # Wait for file to be fully written

        if not os.path.exists(file_path):
            print(f"TXT file {file_path} disappeared before sending.")
            return

        filename = os.path.basename(file_path)

        if self.dry_run:
            try:
                # Read preview in a separate thread
                content_preview = await asyncio.to_thread(
                    self._read_file_content, file_path, "r", "utf-8", 1000
                )
                print(
                    f"[DRY RUN] Would send TXT report: {file_path}\nFilename: {filename}\nContent Preview (first 1000 chars):\n{content_preview}..."
                )
            except Exception as e:
                print(f"[DRY RUN] Error reading TXT file for preview: {e}")
            return

        try:
            # Read full content in a separate thread
            content = await asyncio.to_thread(
                self._read_file_content, file_path, "r", "utf-8"
            )

            # Split content if it's too long for Telegram
            max_length = 4000  # Telegram message limit is 4096, leave some room
            if len(content) <= max_length:
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"```\n{content}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            else:
                # Split on course boundaries for long reports
                await self._send_long_report(content)

            print(f"Successfully sent TXT report: {filename}")
        except TelegramError as e:
            print(f"Error sending TXT report {filename}: {e}")
        except FileNotFoundError:
            print(f"Error: TXT file not found at {file_path} during send attempt.")
        except Exception as e:
            print(f"An unexpected error occurred sending TXT report {filename}: {e}")

    async def _send_long_report(self, content: str):
        """Split long reports on course boundaries and send multiple messages."""
        lines = content.split("\n")
        max_length = 4000

        # Find header lines (first few lines before courses start)
        header_lines = []
        course_start_idx = 0

        for i, line in enumerate(lines):
            if i % 1000 == 0:
                await asyncio.sleep(0)
            # Look for the first line that looks like a course code (not indented, not empty, not header)
            stripped = line.strip()
            if (
                stripped
                and not line.startswith(" ")
                and not line.startswith("Previous Snapshot:")
                and not line.startswith("Current Snapshot:")
                and not line.startswith("Overall Fill:")
                and not line.startswith("No significant changes")
            ):
                course_start_idx = i
                break
            header_lines.append(line)

        # Group course sections
        current_chunk = []
        current_length = 0

        # Add header to first chunk
        if header_lines:
            current_chunk.extend(header_lines)
            current_length = sum(len(line) + 1 for line in header_lines)

        i = course_start_idx
        block_count = 0
        while i < len(lines):
            # Yield control back to event loop every 50 course blocks to avoid
            # excessive scheduling overhead while still keeping the app responsive
            block_count += 1
            if block_count % 50 == 0:
                await asyncio.sleep(0)

            # Find the next course block
            course_block = []
            course_block_length = 0

            # Read first line of the block
            line = lines[i]
            course_block.append(line)
            course_block_length += len(line) + 1
            i += 1

            # Read rest of the block until next course, yielding every 1000 lines
            # so a single huge block cannot monopolize the event loop
            inner_count = 0
            while i < len(lines):
                inner_count += 1
                if inner_count % 1000 == 0:
                    await asyncio.sleep(0)

                line = lines[i]
                is_course_start = (
                    line.strip()
                    and not line.startswith(" ")
                    and not line.startswith("No significant changes")
                )
                if is_course_start:
                    break

                course_block.append(line)
                course_block_length += len(line) + 1
                i += 1

            # Check if we need to flush current chunk
            if (
                len(current_chunk) > len(header_lines)
                and current_length + course_block_length + 10 > max_length
            ):
                # Send current chunk
                chunk_text = "\n".join(current_chunk)
                if chunk_text.strip():
                    await self.bot.send_message(
                        chat_id=self.chat_id,
                        text=f"```\n{chunk_text}\n```",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                current_chunk = []
                current_length = 0

            # Add current block to chunk
            current_chunk.extend(course_block)
            current_length += course_block_length

        # Send final chunk
        if current_chunk:
            chunk_text = "\n".join(current_chunk)
            if chunk_text.strip():
                await self.bot.send_message(
                    chat_id=self.chat_id,
                    text=f"```\n{chunk_text}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )


def main():
    """Main entry point for the telegram reporter."""
    parser = argparse.ArgumentParser(
        description="Telegram Reporter for Enrollment Data"
    )
    parser.add_argument("--send-txt", type=str, help="Send a specific text file")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")

    args = parser.parse_args()

    if args.send_txt:
        reporter = TelegramReporter()

        if args.dry_run:
            reporter.dry_run = True

        async def send_files():
            if args.send_txt and os.path.exists(args.send_txt):
                await reporter.send_text_report(args.send_txt)

        asyncio.run(send_files())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
