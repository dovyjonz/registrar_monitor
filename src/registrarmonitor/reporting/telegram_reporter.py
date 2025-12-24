"""
Telegram reporting module for sending enrollment reports and change notifications.
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
        self.pdf_output_dir = self.config["directories"]["pdf_output"]
        self.text_reports_dir = self.config["directories"]["text_reports"]
        self.file_write_delay = self.config.get("notifications", {}).get(
            "file_write_delay", 3
        )
        self.dry_run = self.config.get("notifications", {}).get("dry_run", False)

        self.bot = Bot(token=self.bot_token)

    async def send_pdf_report(self, file_path: str):
        """Send a PDF report via Telegram."""
        await asyncio.sleep(self.file_write_delay)  # Wait for file to be fully written

        if not os.path.exists(file_path):
            print(f"PDF file {file_path} disappeared before sending.")
            return

        filename = os.path.basename(file_path)

        if self.dry_run:
            print(f"[DRY RUN] Would send PDF: {file_path}")
            return

        try:
            print(f"Sending PDF: {filename} to chat ID {self.chat_id}")
            with open(file_path, "rb") as pdf_file:
                await self.bot.send_document(
                    chat_id=self.chat_id,
                    document=pdf_file,
                    filename=filename,
                    parse_mode=ParseMode.MARKDOWN_V2,
                )
            print(f"Successfully sent PDF: {filename}")
        except TelegramError as e:
            print(f"Error sending PDF {filename}: {e}")
        except FileNotFoundError:
            print(f"Error: PDF file not found at {file_path} during send attempt.")
        except Exception as e:
            print(f"An unexpected error occurred sending PDF {filename}: {e}")

    async def send_text_report(self, file_path: str):
        """Send a text report via Telegram."""
        await asyncio.sleep(self.file_write_delay)  # Wait for file to be fully written

        if not os.path.exists(file_path):
            print(f"TXT file {file_path} disappeared before sending.")
            return

        filename = os.path.basename(file_path)

        if self.dry_run:
            try:
                with open(file_path, "r", encoding="utf-8") as f:
                    content_preview = f.read(1000)  # Read first 1000 chars for preview
                print(
                    f"[DRY RUN] Would send TXT report: {file_path}\nFilename: {filename}\nContent Preview (first 1000 chars):\n{content_preview}..."
                )
            except Exception as e:
                print(f"[DRY RUN] Error reading TXT file for preview: {e}")
            return

        try:
            with open(file_path, "r", encoding="utf-8") as f:
                content = f.read()

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
        while i < len(lines):
            line = lines[i]

            # Check if this is a new course (not indented and not empty)
            is_course_start = (
                line.strip()
                and not line.startswith(" ")
                and not line.startswith("No significant changes")
            )

            if is_course_start and len(current_chunk) > len(header_lines):
                # Look ahead to see how big this course block will be
                course_block = []
                j = i
                while j < len(lines):
                    next_line = lines[j]
                    course_block.append(next_line)
                    j += 1

                    # Stop at next course or end of content
                    if (
                        j < len(lines)
                        and lines[j].strip()
                        and not lines[j].startswith(" ")
                        and not lines[j].startswith("No significant changes")
                    ):
                        break

                course_block_text = "\n".join(course_block)
                if (
                    current_length + len(course_block_text) + 10 > max_length
                ):  # +10 for code block markup
                    # Send current chunk
                    chunk_text = "\n".join(current_chunk)
                    if chunk_text.strip():
                        await self.bot.send_message(
                            chat_id=self.chat_id,
                            text=f"```\n{chunk_text}\n```",
                            parse_mode=ParseMode.MARKDOWN_V2,
                        )

                    # Start new chunk (without header for subsequent chunks)
                    current_chunk = []
                    current_length = 0

            # Add current line to chunk
            current_chunk.append(line)
            current_length += len(line) + 1  # +1 for newline
            i += 1

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
    parser.add_argument("--send-pdf", type=str, help="Send a specific PDF file")
    parser.add_argument("--send-txt", type=str, help="Send a specific text file")
    parser.add_argument("--dry-run", action="store_true", help="Enable dry run mode")

    args = parser.parse_args()

    if args.send_pdf or args.send_txt:
        reporter = TelegramReporter()

        if args.dry_run:
            reporter.dry_run = True

        async def send_files():
            if args.send_pdf and os.path.exists(args.send_pdf):
                await reporter.send_pdf_report(args.send_pdf)
            if args.send_txt and os.path.exists(args.send_txt):
                await reporter.send_text_report(args.send_txt)

        asyncio.run(send_files())
    else:
        parser.print_help()


if __name__ == "__main__":
    main()
