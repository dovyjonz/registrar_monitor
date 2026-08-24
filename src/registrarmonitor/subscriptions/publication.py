"""Coordinate channel reporting with activation of personal notifications."""

from collections.abc import Awaitable, Callable
from typing import Protocol

from .store import SubscriptionStore


class ReportingLog(Protocol):
    def was_snapshot_reported(
        self, snapshot_id: int, *, changes_were_found: bool
    ) -> bool: ...

    def add_reporting_log(
        self, *, snapshot_id: int, changes_were_found: bool
    ) -> None: ...


class ReportPublication:
    """Publish one report pair once, then make its personal deliveries visible."""

    def __init__(self, store: SubscriptionStore, reporting_log: ReportingLog) -> None:
        self.store = store
        self.reporting_log = reporting_log

    async def publish(
        self,
        *,
        semester: str,
        previous_snapshot_id: int,
        current_snapshot_id: int,
        send_channel: Callable[[], Awaitable[None]],
    ) -> None:
        batch = self.store.stage_batch(
            semester, previous_snapshot_id, current_snapshot_id
        )
        if not batch.channel_succeeded:
            await send_channel()
            self.store.mark_channel_succeeded(batch.batch_id)

        if not self.reporting_log.was_snapshot_reported(
            current_snapshot_id, changes_were_found=True
        ):
            self.reporting_log.add_reporting_log(
                snapshot_id=current_snapshot_id, changes_were_found=True
            )
        self.store.activate_batch(batch.batch_id)

    def recover(self, *, semester: str | None = None) -> int:
        """Activate batches whose channel send and reporting log are both durable."""
        activated = 0
        for batch in self.store.list_pending_batches():
            if semester is not None and batch.semester != semester:
                continue
            if batch.channel_succeeded and self.reporting_log.was_snapshot_reported(
                batch.current_snapshot_id, changes_were_found=True
            ):
                self.store.activate_batch(batch.batch_id)
                activated += 1
        return activated
