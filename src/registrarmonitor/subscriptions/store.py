"""SQLite persistence for bot users, subscriptions, and notification delivery."""

import sqlite3
from collections.abc import Callable
from contextlib import closing, contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from .models import BotUser, Delivery, NotificationBatch, SubscriptionTarget


class SubscriptionStore:
    """Own the bot's separate SQLite database and its lifecycle transitions."""

    def __init__(
        self,
        path: str | Path,
        *,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA busy_timeout = 5000")
        return connection

    @contextmanager
    def _transaction(self):
        with closing(self._connect()) as connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()

    def _initialize(self) -> None:
        with closing(self._connect()) as connection:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS bot_user (
                    telegram_user_id INTEGER PRIMARY KEY,
                    private_chat_id INTEGER NOT NULL UNIQUE,
                    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0, 1)),
                    created_at TEXT NOT NULL,
                    last_interaction_at TEXT NOT NULL,
                    inactive_at TEXT
                );

                CREATE TABLE IF NOT EXISTS subscription (
                    subscription_id INTEGER PRIMARY KEY AUTOINCREMENT,
                    telegram_user_id INTEGER NOT NULL
                        REFERENCES bot_user(telegram_user_id) ON DELETE CASCADE,
                    semester TEXT NOT NULL,
                    course_code TEXT NOT NULL,
                    section_code TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE (telegram_user_id, semester, course_code, section_code)
                );

                CREATE TABLE IF NOT EXISTS notification_batch (
                    batch_id INTEGER PRIMARY KEY,
                    semester TEXT NOT NULL,
                    previous_snapshot_id INTEGER NOT NULL,
                    current_snapshot_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'deliverable', 'complete')),
                    channel_succeeded_at TEXT,
                    created_at TEXT NOT NULL,
                    deliverable_at TEXT,
                    completed_at TEXT,
                    UNIQUE (semester, previous_snapshot_id, current_snapshot_id)
                );

                CREATE TABLE IF NOT EXISTS batch_recipient (
                    batch_id INTEGER NOT NULL
                        REFERENCES notification_batch(batch_id) ON DELETE CASCADE,
                    telegram_user_id INTEGER NOT NULL
                        REFERENCES bot_user(telegram_user_id) ON DELETE CASCADE,
                    private_chat_id INTEGER NOT NULL,
                    PRIMARY KEY (batch_id, telegram_user_id)
                );

                CREATE TABLE IF NOT EXISTS batch_subscription (
                    batch_id INTEGER NOT NULL
                        REFERENCES notification_batch(batch_id) ON DELETE CASCADE,
                    subscription_id INTEGER NOT NULL,
                    telegram_user_id INTEGER NOT NULL
                        REFERENCES bot_user(telegram_user_id) ON DELETE CASCADE,
                    semester TEXT NOT NULL,
                    course_code TEXT NOT NULL,
                    section_code TEXT NOT NULL,
                    PRIMARY KEY (batch_id, subscription_id)
                );

                CREATE TABLE IF NOT EXISTS delivery (
                    delivery_id INTEGER PRIMARY KEY,
                    batch_id INTEGER NOT NULL
                        REFERENCES notification_batch(batch_id) ON DELETE CASCADE,
                    telegram_user_id INTEGER NOT NULL
                        REFERENCES bot_user(telegram_user_id) ON DELETE CASCADE,
                    private_chat_id INTEGER NOT NULL,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN (
                            'pending', 'retry', 'sending', 'sent', 'skipped',
                            'permanent_failed'
                        )),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    next_attempt_at TEXT NOT NULL,
                    lease_expires_at TEXT,
                    next_chunk_index INTEGER NOT NULL DEFAULT 0,
                    error_category TEXT,
                    updated_at TEXT NOT NULL,
                    UNIQUE (batch_id, telegram_user_id)
                );

                CREATE INDEX IF NOT EXISTS delivery_due
                    ON delivery(status, next_attempt_at);
                PRAGMA user_version = 1;
                """
            )

    def _now(self) -> str:
        return self._clock().astimezone(UTC).isoformat()

    def touch_user(self, *, telegram_user_id: int, private_chat_id: int) -> BotUser:
        now = self._now()
        with self._transaction() as connection:
            connection.execute(
                """
                INSERT INTO bot_user(
                    telegram_user_id, private_chat_id, active,
                    created_at, last_interaction_at
                ) VALUES (?, ?, 1, ?, ?)
                ON CONFLICT(telegram_user_id) DO UPDATE SET
                    private_chat_id = excluded.private_chat_id,
                    active = 1,
                    last_interaction_at = excluded.last_interaction_at,
                    inactive_at = NULL
                """,
                (telegram_user_id, private_chat_id, now, now),
            )
        user = self.get_user(telegram_user_id)
        if user is None:
            raise RuntimeError("Failed to persist Telegram user")
        return user

    def get_user(self, telegram_user_id: int) -> BotUser | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT telegram_user_id, private_chat_id, active
                FROM bot_user WHERE telegram_user_id = ?
                """,
                (telegram_user_id,),
            ).fetchone()
        return self._user(row) if row else None

    def deactivate_user(self, telegram_user_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE bot_user SET active = 0, inactive_at = ?
                WHERE telegram_user_id = ?
                """,
                (self._now(), telegram_user_id),
            )

    def delete_user(self, telegram_user_id: int) -> None:
        with self._transaction() as connection:
            batch_ids = self._user_batch_ids(connection, telegram_user_id)
            connection.execute(
                "DELETE FROM bot_user WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            self._complete_empty_batches(connection, batch_ids, self._now())

    def subscribe(self, telegram_user_id: int, target: SubscriptionTarget) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO subscription(
                    telegram_user_id, semester, course_code, section_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    target.semester,
                    target.course_code,
                    target.section_code,
                    self._now(),
                ),
            )
            return cursor.rowcount == 1

    def add_watch(self, telegram_user_id: int, target: SubscriptionTarget) -> bool:
        """Add a UI watch without narrowing an existing whole-course watch."""
        with self._transaction() as connection:
            current = self._course_section_codes(
                connection,
                telegram_user_id,
                target.semester,
                target.course_code,
            )
            if not target.is_course and "" in current:
                return False
            cursor = connection.execute(
                """
                INSERT OR IGNORE INTO subscription(
                    telegram_user_id, semester, course_code, section_code, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (
                    telegram_user_id,
                    target.semester,
                    target.course_code,
                    target.section_code,
                    self._now(),
                ),
            )
            return cursor.rowcount == 1

    def replace_section_watches(
        self,
        telegram_user_id: int,
        course: SubscriptionTarget,
        section_codes: set[str],
    ) -> bool:
        """Atomically replace every watch for one course with section watches."""
        if not course.is_course:
            raise ValueError("course target required")
        with self._transaction() as connection:
            current = self._course_section_codes(
                connection,
                telegram_user_id,
                course.semester,
                course.course_code,
            )
            return self._replace_course_targets(
                connection,
                telegram_user_id,
                course.semester,
                course.course_code,
                current,
                section_codes,
            )

    @staticmethod
    def _course_section_codes(
        connection: sqlite3.Connection,
        telegram_user_id: int,
        semester: str,
        course_code: str,
    ) -> set[str]:
        rows = connection.execute(
            """
            SELECT section_code FROM subscription
            WHERE telegram_user_id = ? AND semester = ? AND course_code = ?
            """,
            (telegram_user_id, semester, course_code),
        ).fetchall()
        return {row["section_code"] for row in rows}

    def _replace_course_targets(
        self,
        connection: sqlite3.Connection,
        telegram_user_id: int,
        semester: str,
        course_code: str,
        current: set[str],
        desired: set[str],
    ) -> bool:
        if current == desired:
            return False
        connection.execute(
            """
            DELETE FROM subscription
            WHERE telegram_user_id = ? AND semester = ? AND course_code = ?
            """,
            (telegram_user_id, semester, course_code),
        )
        self._insert_targets(
            connection,
            telegram_user_id,
            semester,
            course_code,
            desired,
        )
        return True

    def _insert_targets(
        self,
        connection: sqlite3.Connection,
        telegram_user_id: int,
        semester: str,
        course_code: str,
        section_codes: set[str],
    ) -> None:
        now = self._now()
        connection.executemany(
            """
            INSERT INTO subscription(
                telegram_user_id, semester, course_code, section_code, created_at
            ) VALUES (?, ?, ?, ?, ?)
            """,
            [
                (telegram_user_id, semester, course_code, section_code, now)
                for section_code in sorted(section_codes)
            ],
        )

    def unsubscribe(self, telegram_user_id: int, target: SubscriptionTarget) -> bool:
        with self._transaction() as connection:
            cursor = connection.execute(
                """
                DELETE FROM subscription
                WHERE telegram_user_id = ? AND semester = ?
                  AND course_code = ? AND section_code = ?
                """,
                (
                    telegram_user_id,
                    target.semester,
                    target.course_code,
                    target.section_code,
                ),
            )
            return cursor.rowcount == 1

    def clear_subscriptions(self, telegram_user_id: int) -> int:
        with self._transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM subscription WHERE telegram_user_id = ?",
                (telegram_user_id,),
            )
            return cursor.rowcount

    def list_subscriptions(self, telegram_user_id: int) -> list[SubscriptionTarget]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT semester, course_code, section_code
                FROM subscription WHERE telegram_user_id = ?
                ORDER BY semester, course_code, section_code
                """,
                (telegram_user_id,),
            ).fetchall()
        return [self._target(row) for row in rows]

    def operational_stats(self) -> dict[str, int]:
        """Return aggregate bot health counts without exposing user identifiers."""
        with closing(self._connect()) as connection:
            row = connection.execute(
                """
                SELECT
                    (SELECT COUNT(*) FROM bot_user) AS users,
                    (SELECT COUNT(*) FROM bot_user WHERE active = 1) AS active_users,
                    (SELECT COUNT(*) FROM delivery
                     WHERE status IN ('pending', 'retry', 'sending'))
                        AS pending_deliveries
                """
            ).fetchone()
        return {
            "users": row["users"],
            "active_users": row["active_users"],
            "pending_deliveries": row["pending_deliveries"],
        }

    def effective_subscriptions(
        self, telegram_user_id: int, semester: str
    ) -> list[SubscriptionTarget]:
        targets = [
            target
            for target in self.list_subscriptions(telegram_user_id)
            if target.semester == semester
        ]
        course_codes = {target.course_code for target in targets if target.is_course}
        return [
            target
            for target in targets
            if target.is_course or target.course_code not in course_codes
        ]

    def effective_batch_subscriptions(
        self, batch_id: int, telegram_user_id: int
    ) -> list[SubscriptionTarget]:
        """Return staged targets that the user still subscribes to."""
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT bs.semester, bs.course_code, bs.section_code
                FROM batch_subscription bs
                JOIN subscription current
                  ON current.subscription_id = bs.subscription_id
                 AND current.telegram_user_id = bs.telegram_user_id
                WHERE bs.batch_id = ? AND bs.telegram_user_id = ?
                ORDER BY bs.course_code, bs.section_code
                """,
                (batch_id, telegram_user_id),
            ).fetchall()
        targets = [self._target(row) for row in rows]
        course_codes = {target.course_code for target in targets if target.is_course}
        return [
            target
            for target in targets
            if target.is_course or target.course_code not in course_codes
        ]

    def stage_batch(
        self,
        semester: str,
        previous_snapshot_id: int,
        current_snapshot_id: int,
    ) -> NotificationBatch:
        now = self._now()
        with self._transaction() as connection:
            inserted = connection.execute(
                """
                INSERT OR IGNORE INTO notification_batch(
                    semester, previous_snapshot_id, current_snapshot_id, created_at
                ) VALUES (?, ?, ?, ?)
                """,
                (semester, previous_snapshot_id, current_snapshot_id, now),
            )
            row = connection.execute(
                """
                SELECT * FROM notification_batch
                WHERE semester = ? AND previous_snapshot_id = ?
                  AND current_snapshot_id = ?
                """,
                (semester, previous_snapshot_id, current_snapshot_id),
            ).fetchone()
            if inserted.rowcount != 1:
                return self._batch(row)
            connection.execute(
                """
                INSERT OR IGNORE INTO batch_recipient(
                    batch_id, telegram_user_id, private_chat_id
                )
                SELECT ?, u.telegram_user_id, u.private_chat_id
                FROM bot_user u
                WHERE u.active = 1 AND EXISTS (
                    SELECT 1 FROM subscription s
                    WHERE s.telegram_user_id = u.telegram_user_id
                      AND s.semester = ?
                )
                """,
                (row["batch_id"], semester),
            )
            connection.execute(
                """
                INSERT OR IGNORE INTO batch_subscription(
                    batch_id, subscription_id, telegram_user_id,
                    semester, course_code, section_code
                )
                SELECT ?, s.subscription_id, s.telegram_user_id, s.semester,
                       s.course_code, s.section_code
                FROM subscription s
                JOIN bot_user u ON u.telegram_user_id = s.telegram_user_id
                WHERE u.active = 1 AND s.semester = ?
                """,
                (row["batch_id"], semester),
            )
        return self._batch(row)

    def get_batch(self, batch_id: int) -> NotificationBatch | None:
        with closing(self._connect()) as connection:
            row = connection.execute(
                "SELECT * FROM notification_batch WHERE batch_id = ?", (batch_id,)
            ).fetchone()
        return self._batch(row) if row else None

    def mark_channel_succeeded(self, batch_id: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE notification_batch
                SET channel_succeeded_at = COALESCE(channel_succeeded_at, ?)
                WHERE batch_id = ?
                """,
                (self._now(), batch_id),
            )

    def activate_batch(self, batch_id: int) -> None:
        now = self._now()
        with self._transaction() as connection:
            batch = connection.execute(
                "SELECT * FROM notification_batch WHERE batch_id = ?", (batch_id,)
            ).fetchone()
            if batch is None:
                raise KeyError(f"Unknown notification batch {batch_id}")
            if batch["channel_succeeded_at"] is None:
                raise ValueError("Channel delivery must succeed before activation")
            connection.execute(
                """
                INSERT OR IGNORE INTO delivery(
                    batch_id, telegram_user_id, private_chat_id,
                    next_attempt_at, updated_at
                )
                SELECT batch_id, telegram_user_id, private_chat_id, ?, ?
                FROM batch_recipient WHERE batch_id = ?
                """,
                (now, now, batch_id),
            )
            connection.execute(
                """
                UPDATE notification_batch
                SET status = 'deliverable', deliverable_at = COALESCE(deliverable_at, ?)
                WHERE batch_id = ? AND status = 'pending'
                """,
                (now, batch_id),
            )
            has_delivery = connection.execute(
                "SELECT 1 FROM delivery WHERE batch_id = ? LIMIT 1", (batch_id,)
            ).fetchone()
            if has_delivery is None:
                connection.execute(
                    """
                    UPDATE notification_batch
                    SET status = 'complete', completed_at = ?
                    WHERE batch_id = ?
                    """,
                    (now, batch_id),
                )

    def list_pending_batches(self) -> list[NotificationBatch]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                """
                SELECT * FROM notification_batch
                WHERE status = 'pending' ORDER BY batch_id
                """
            ).fetchall()
        return [self._batch(row) for row in rows]

    def list_deliveries(self, batch_id: int) -> list[Delivery]:
        with closing(self._connect()) as connection:
            rows = connection.execute(
                "SELECT * FROM delivery WHERE batch_id = ? ORDER BY delivery_id",
                (batch_id,),
            ).fetchall()
        return [self._delivery(row) for row in rows]

    def claim_due_delivery(self, *, lease_seconds: int) -> Delivery | None:
        now = self._clock().astimezone(UTC)
        now_text = now.isoformat()
        lease_text = (now + timedelta(seconds=lease_seconds)).isoformat()
        with self._transaction() as connection:
            row = connection.execute(
                """
                SELECT * FROM delivery
                WHERE (
                    status IN ('pending', 'retry') AND next_attempt_at <= ?
                ) OR (
                    status = 'sending' AND lease_expires_at <= ?
                )
                ORDER BY next_attempt_at, delivery_id LIMIT 1
                """,
                (now_text, now_text),
            ).fetchone()
            if row is None:
                return None
            connection.execute(
                """
                UPDATE delivery SET status = 'sending', lease_expires_at = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (lease_text, now_text, row["delivery_id"]),
            )
            claimed = connection.execute(
                "SELECT * FROM delivery WHERE delivery_id = ?",
                (row["delivery_id"],),
            ).fetchone()
        return self._delivery(claimed)

    def record_chunk_sent(self, delivery_id: int, *, next_chunk_index: int) -> None:
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE delivery SET next_chunk_index = ?, updated_at = ?
                WHERE delivery_id = ? AND status = 'sending'
                """,
                (next_chunk_index, self._now(), delivery_id),
            )

    def retry_delivery(
        self, delivery_id: int, *, delay_seconds: float, category: str
    ) -> None:
        now = self._clock().astimezone(UTC)
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE delivery
                SET status = 'retry', attempt_count = attempt_count + 1,
                    next_attempt_at = ?, lease_expires_at = NULL,
                    error_category = ?, updated_at = ?
                WHERE delivery_id = ?
                """,
                (
                    (now + timedelta(seconds=delay_seconds)).isoformat(),
                    category,
                    now.isoformat(),
                    delivery_id,
                ),
            )

    def finish_delivery(
        self, delivery_id: int, *, status: str, category: str | None = None
    ) -> None:
        if status not in {"sent", "skipped", "permanent_failed"}:
            raise ValueError(f"Invalid terminal delivery status: {status}")
        with self._transaction() as connection:
            connection.execute(
                """
                UPDATE delivery
                SET status = ?, lease_expires_at = NULL,
                    error_category = COALESCE(?, error_category), updated_at = ?
                WHERE delivery_id = ?
                """,
                (status, category, self._now(), delivery_id),
            )
            batch_row = connection.execute(
                "SELECT batch_id FROM delivery WHERE delivery_id = ?",
                (delivery_id,),
            ).fetchone()
            if batch_row:
                remaining = connection.execute(
                    """
                    SELECT 1 FROM delivery
                    WHERE batch_id = ? AND status NOT IN ('sent', 'skipped', 'permanent_failed')
                    LIMIT 1
                    """,
                    (batch_row["batch_id"],),
                ).fetchone()
                if remaining is None:
                    connection.execute(
                        """
                        UPDATE notification_batch
                        SET status = 'complete', completed_at = ?
                        WHERE batch_id = ?
                        """,
                        (self._now(), batch_row["batch_id"]),
                    )

    def cleanup(
        self, *, completed_batch_days: int, inactive_user_days: int
    ) -> tuple[int, int]:
        """Delete expired completed delivery history and inactive bot accounts."""
        now = self._clock().astimezone(UTC)
        batch_cutoff = (now - timedelta(days=completed_batch_days)).isoformat()
        user_cutoff = (now - timedelta(days=inactive_user_days)).isoformat()
        with self._transaction() as connection:
            batches = connection.execute(
                """
                DELETE FROM notification_batch
                WHERE status = 'complete' AND completed_at < ?
                """,
                (batch_cutoff,),
            ).rowcount
            expired_users = connection.execute(
                """
                SELECT telegram_user_id FROM bot_user
                WHERE active = 0 AND inactive_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM subscription
                      WHERE subscription.telegram_user_id = bot_user.telegram_user_id
                  )
                """,
                (user_cutoff,),
            ).fetchall()
            batch_ids = {
                batch_id
                for row in expired_users
                for batch_id in self._user_batch_ids(
                    connection, row["telegram_user_id"]
                )
            }
            users = connection.execute(
                """
                DELETE FROM bot_user
                WHERE active = 0 AND inactive_at < ?
                  AND NOT EXISTS (
                      SELECT 1 FROM subscription
                      WHERE subscription.telegram_user_id = bot_user.telegram_user_id
                  )
                """,
                (user_cutoff,),
            ).rowcount
            self._complete_empty_batches(connection, batch_ids, now.isoformat())
        return batches, users

    @staticmethod
    def _user_batch_ids(
        connection: sqlite3.Connection, telegram_user_id: int
    ) -> set[int]:
        rows = connection.execute(
            """
            SELECT batch_id FROM batch_recipient WHERE telegram_user_id = ?
            UNION
            SELECT batch_id FROM delivery WHERE telegram_user_id = ?
            """,
            (telegram_user_id, telegram_user_id),
        ).fetchall()
        return {int(row["batch_id"]) for row in rows}

    @staticmethod
    def _complete_empty_batches(
        connection: sqlite3.Connection, batch_ids: set[int], now: str
    ) -> None:
        for batch_id in batch_ids:
            remaining = connection.execute(
                "SELECT 1 FROM delivery WHERE batch_id = ? LIMIT 1", (batch_id,)
            ).fetchone()
            if remaining is None:
                connection.execute(
                    """
                    UPDATE notification_batch
                    SET status = 'complete', completed_at = COALESCE(completed_at, ?)
                    WHERE batch_id = ? AND status = 'deliverable'
                    """,
                    (now, batch_id),
                )

    @staticmethod
    def _user(row: sqlite3.Row) -> BotUser:
        return BotUser(
            row["telegram_user_id"], row["private_chat_id"], bool(row["active"])
        )

    @staticmethod
    def _target(row: sqlite3.Row) -> SubscriptionTarget:
        return SubscriptionTarget(
            row["semester"], row["course_code"], row["section_code"]
        )

    @staticmethod
    def _batch(row: sqlite3.Row) -> NotificationBatch:
        return NotificationBatch(
            row["batch_id"],
            row["semester"],
            row["previous_snapshot_id"],
            row["current_snapshot_id"],
            row["status"],
            row["channel_succeeded_at"] is not None,
        )

    @staticmethod
    def _delivery(row: sqlite3.Row) -> Delivery:
        return Delivery(
            row["delivery_id"],
            row["batch_id"],
            row["telegram_user_id"],
            row["private_chat_id"],
            row["status"],
            row["attempt_count"],
            row["next_chunk_index"],
            row["error_category"],
        )
