"""Domain values persisted by the Telegram subscription service."""

from dataclasses import dataclass


@dataclass(frozen=True, order=True)
class SubscriptionTarget:
    """One semester-scoped course or section watch."""

    semester: str
    course_code: str
    section_code: str = ""

    @property
    def is_course(self) -> bool:
        return not self.section_code


@dataclass(frozen=True)
class BotUser:
    telegram_user_id: int
    private_chat_id: int
    active: bool


@dataclass(frozen=True)
class NotificationBatch:
    batch_id: int
    semester: str
    previous_snapshot_id: int
    current_snapshot_id: int
    status: str
    channel_succeeded: bool


@dataclass(frozen=True)
class Delivery:
    delivery_id: int
    batch_id: int
    telegram_user_id: int
    private_chat_id: int
    status: str
    attempt_count: int
    next_chunk_index: int
    error_category: str | None
