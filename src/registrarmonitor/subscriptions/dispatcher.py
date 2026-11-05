"""Durably deliver personal Telegram change digests."""

import asyncio
import logging
import resource
import time
from collections.abc import Awaitable, Callable
from datetime import timedelta
from typing import Protocol

from telegram import InlineKeyboardButton, InlineKeyboardMarkup
from telegram.constants import ParseMode
from telegram.error import (
    BadRequest,
    Forbidden,
    NetworkError,
    RetryAfter,
    TelegramError,
)

from ..data.snapshot_comparator import SnapshotComparator
from ..models import EnrollmentSnapshot
from .formatting import render_personal_digest
from .store import SubscriptionStore

logger = logging.getLogger(__name__)


class EnrollmentReader(Protocol):
    def get_snapshot_data(self, snapshot_id: int) -> EnrollmentSnapshot | None: ...


class PersonalMessenger(Protocol):
    def send_message(
        self,
        *,
        chat_id: int,
        text: str,
        parse_mode: str,
        reply_markup: InlineKeyboardMarkup | None,
    ) -> Awaitable[object]: ...


class SubscriptionDispatcher:
    """Claim due deliveries, match changes, and persist every send transition."""

    def __init__(
        self,
        store: SubscriptionStore,
        enrollment_db: EnrollmentReader | None,
        messenger: PersonalMessenger,
        *,
        reader_for_semester: Callable[[str], EnrollmentReader] | None = None,
        lease_seconds: int = 120,
        retry_base_seconds: float = 5,
        retry_max_seconds: float = 900,
        per_chat_interval: float = 1.0,
        global_interval: float = 0.04,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.store = store
        self.enrollment_db = enrollment_db
        self.messenger = messenger
        self.reader_for_semester = reader_for_semester
        self.lease_seconds = lease_seconds
        self.retry_base_seconds = retry_base_seconds
        self.retry_max_seconds = retry_max_seconds
        self.per_chat_interval = per_chat_interval
        self.global_interval = global_interval
        self._sleep = sleep
        self._monotonic = monotonic
        self._last_send: dict[int, float] = {}
        self._last_global_send: float | None = None
        self._batch_cache: tuple[tuple, tuple] | None = None
        self._batch_run: tuple[int, float, int] | None = None

    async def dispatch_one(self) -> bool:
        """Process at most one due delivery; return whether work was claimed."""
        delivery = await asyncio.to_thread(
            self.store.claim_due_delivery, lease_seconds=self.lease_seconds
        )
        if delivery is None:
            self._log_batch_run()
            return False
        self._note_batch_delivery(delivery.batch_id)
        user, batch = await asyncio.gather(
            asyncio.to_thread(self.store.get_user, delivery.telegram_user_id),
            asyncio.to_thread(self.store.get_batch, delivery.batch_id),
        )
        if user is None or not user.active or batch is None:
            await asyncio.to_thread(
                self.store.finish_delivery, delivery.delivery_id, status="skipped"
            )
            return True

        targets = await asyncio.to_thread(
            self.store.effective_batch_subscriptions,
            delivery.batch_id,
            delivery.telegram_user_id,
        )
        if not targets:
            await asyncio.to_thread(
                self.store.finish_delivery, delivery.delivery_id, status="skipped"
            )
            return True

        current, previous, comparison = await asyncio.to_thread(
            self._load_batch_comparison, batch
        )
        if current is None or previous is None or comparison is None:
            await asyncio.to_thread(
                self.store.finish_delivery,
                delivery.delivery_id,
                status="permanent_failed",
                category="missing_snapshot",
            )
            return True

        try:
            chunks = await asyncio.to_thread(
                render_personal_digest,
                comparison,
                current,
                previous,
                targets,
            )
        except ValueError:
            logger.warning("A personal delivery could not be rendered")
            await asyncio.to_thread(
                self.store.finish_delivery,
                delivery.delivery_id,
                status="permanent_failed",
                category="rendering",
            )
            return True
        if not chunks:
            await asyncio.to_thread(
                self.store.finish_delivery, delivery.delivery_id, status="skipped"
            )
            return True

        try:
            for index, chunk in enumerate(
                chunks[delivery.next_chunk_index :], start=delivery.next_chunk_index
            ):
                await self._respect_limits(delivery.private_chat_id)
                await self.messenger.send_message(
                    chat_id=delivery.private_chat_id,
                    text=chunk,
                    parse_mode=ParseMode.MARKDOWN_V2,
                    reply_markup=(
                        InlineKeyboardMarkup(
                            [
                                [
                                    InlineKeyboardButton(
                                        "My watches", callback_data="subs:0"
                                    )
                                ]
                            ]
                        )
                        if index == len(chunks) - 1
                        else None
                    ),
                )
                self._last_send[delivery.private_chat_id] = self._monotonic()
                self._last_global_send = self._last_send[delivery.private_chat_id]
                await asyncio.to_thread(
                    self.store.record_chunk_sent,
                    delivery.delivery_id,
                    next_chunk_index=index + 1,
                )
        except RetryAfter as error:
            logger.warning("Telegram requested a personal-delivery retry delay")
            await asyncio.to_thread(
                self.store.retry_delivery,
                delivery.delivery_id,
                delay_seconds=self._retry_after_seconds(error),
                category="rate_limit",
            )
        except Forbidden:
            logger.warning("Telegram personal delivery is permanently unreachable")
            await asyncio.to_thread(
                self.store.deactivate_user, delivery.telegram_user_id
            )
            await asyncio.to_thread(
                self.store.finish_delivery,
                delivery.delivery_id,
                status="permanent_failed",
                category="forbidden",
            )
        except BadRequest:
            logger.warning("Telegram rejected a personal delivery")
            await asyncio.to_thread(
                self.store.finish_delivery,
                delivery.delivery_id,
                status="permanent_failed",
                category="bad_request",
            )
        except (NetworkError, TelegramError):
            logger.warning("Telegram personal delivery failed transiently")
            delay = min(
                self.retry_base_seconds * (2**delivery.attempt_count),
                self.retry_max_seconds,
            )
            await asyncio.to_thread(
                self.store.retry_delivery,
                delivery.delivery_id,
                delay_seconds=delay,
                category="network",
            )
        else:
            await asyncio.to_thread(
                self.store.finish_delivery, delivery.delivery_id, status="sent"
            )
        return True

    def _note_batch_delivery(self, batch_id: int) -> None:
        if self._batch_run is None or self._batch_run[0] != batch_id:
            self._log_batch_run()
            self._batch_run = (batch_id, self._monotonic(), 0)
        run_batch, started, count = self._batch_run
        self._batch_run = (run_batch, started, count + 1)

    def _log_batch_run(self) -> None:
        """Log one line for the digest batch whose deliveries were just processed."""
        if self._batch_run is None:
            return
        batch_id, started, count = self._batch_run
        self._batch_run = None
        # ru_maxrss is KiB on Linux (the production platform).
        peak_rss_mb = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024
        logger.info(
            "Digest batch processed batch_id=%s deliveries=%d duration_ms=%.0f "
            "peak_rss_mb=%.0f",
            batch_id,
            count,
            (self._monotonic() - started) * 1000,
            peak_rss_mb,
        )

    def _load_batch_comparison(self, batch):
        """Return the batch's snapshots and comparison, computed once per batch."""
        key = (
            batch.batch_id,
            batch.semester,
            batch.current_snapshot_id,
            batch.previous_snapshot_id,
        )
        if self._batch_cache is not None and self._batch_cache[0] == key:
            return self._batch_cache[1]
        reader = (
            self.reader_for_semester(batch.semester)
            if self.reader_for_semester
            else self.enrollment_db
        )
        if reader is None:
            raise RuntimeError("No enrollment reader configured")
        current = reader.get_snapshot_data(batch.current_snapshot_id)
        previous = reader.get_snapshot_data(batch.previous_snapshot_id)
        if current is None or previous is None:
            return None, None, None
        value = (
            current,
            previous,
            SnapshotComparator().compare_snapshots(current, previous),
        )
        self._batch_cache = (key, value)
        return value

    async def _respect_limits(self, chat_id: int) -> None:
        now = self._monotonic()
        waits = []
        last_send = self._last_send.get(chat_id)
        if last_send is not None:
            waits.append(self.per_chat_interval - (now - last_send))
        if self._last_global_send is not None:
            waits.append(self.global_interval - (now - self._last_global_send))
        wait = max(waits, default=0)
        if wait > 0:
            await self._sleep(wait)

    @staticmethod
    def _retry_after_seconds(error: RetryAfter) -> float:
        value = error.retry_after
        if isinstance(value, timedelta):
            return value.total_seconds()
        return float(value)
