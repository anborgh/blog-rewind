"""Хранилище на SQLite: индекс записей блога и журнал отправок."""

from __future__ import annotations

import asyncio
import sqlite3
import threading
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Post, to_utc

SCHEMA = """
CREATE TABLE IF NOT EXISTS posts (
    chat_id       INTEGER NOT NULL,
    message_id    INTEGER NOT NULL,
    posted_at     INTEGER NOT NULL,
    local_year    INTEGER NOT NULL,
    local_month   INTEGER NOT NULL,
    local_day     INTEGER NOT NULL,
    text          TEXT NOT NULL DEFAULT '',
    media_kind    TEXT,
    chat_title    TEXT,
    chat_username TEXT,
    group_id      TEXT,
    source        TEXT NOT NULL DEFAULT 'live',
    PRIMARY KEY (chat_id, message_id)
);

CREATE INDEX IF NOT EXISTS posts_by_day ON posts (local_month, local_day, local_year);
CREATE INDEX IF NOT EXISTS posts_by_time ON posts (posted_at);

CREATE TABLE IF NOT EXISTS deliveries (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    sent_on    TEXT NOT NULL,
    chat_id    INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    sent_at    INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS deliveries_by_day ON deliveries (sent_on);
CREATE INDEX IF NOT EXISTS deliveries_by_post ON deliveries (chat_id, message_id);

CREATE TABLE IF NOT EXISTS state (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
"""

# SQLite ограничивает число параметров в одном запросе; ключ занимает два.
_MAX_KEYS_PER_QUERY = 400


def _chunked(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start : start + size]


class Storage:
    """Синхронное хранилище; из асинхронного кода вызывается через :func:`run`."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        if self.path.parent and str(self.path.parent) not in ("", "."):
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA foreign_keys=ON")
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    # --- записи -----------------------------------------------------------

    def upsert_post(self, post: Post) -> bool:
        """Добавляет или обновляет запись. Возвращает True, если запись новая."""
        return self.upsert_posts([post]) == 1

    def upsert_posts(self, posts: Iterable[Post]) -> int:
        """Массовая вставка. Возвращает количество именно новых записей."""
        rows = list(posts)
        if not rows:
            return 0
        with self._lock:
            known: set[tuple[int, int]] = set()
            for chunk in _chunked([post.key for post in rows], _MAX_KEYS_PER_QUERY):
                placeholders = ",".join(["(?,?)"] * len(chunk))
                known.update(
                    (row["chat_id"], row["message_id"])
                    for row in self._conn.execute(
                        "SELECT chat_id, message_id FROM posts WHERE (chat_id, message_id) IN "
                        f"(VALUES {placeholders})",
                        [value for key in chunk for value in key],
                    )
                )
            self._conn.executemany(
                """
                INSERT INTO posts (chat_id, message_id, posted_at, local_year, local_month,
                                   local_day, text, media_kind, chat_title, chat_username,
                                   group_id, source)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(chat_id, message_id) DO UPDATE SET
                    posted_at = excluded.posted_at,
                    local_year = excluded.local_year,
                    local_month = excluded.local_month,
                    local_day = excluded.local_day,
                    text = excluded.text,
                    media_kind = excluded.media_kind,
                    chat_title = COALESCE(excluded.chat_title, posts.chat_title),
                    chat_username = COALESCE(excluded.chat_username, posts.chat_username),
                    group_id = COALESCE(excluded.group_id, posts.group_id)
                """,
                [_to_row(post) for post in rows],
            )
            self._conn.commit()
        return sum(1 for post in rows if post.key not in known)

    def delete_post(self, chat_id: int, message_id: int) -> None:
        with self._lock:
            self._conn.execute(
                "DELETE FROM posts WHERE chat_id = ? AND message_id = ?", (chat_id, message_id)
            )
            self._conn.commit()

    def posts_on(
        self,
        day_keys: Sequence[tuple[int, int]],
        *,
        max_year: int,
        chat_id: int | None = None,
    ) -> list[Post]:
        """Записи за указанные пары (месяц, день) не позже `max_year` включительно."""
        if not day_keys:
            return []
        clauses = " OR ".join(["(local_month = ? AND local_day = ?)"] * len(day_keys))
        params: list[Any] = [value for key in day_keys for value in key]
        query = f"SELECT * FROM posts WHERE ({clauses}) AND local_year <= ?"
        params.append(max_year)
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY posted_at"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_from_row(row) for row in rows]

    def posts_on_date(self, day: date, chat_id: int | None = None) -> list[Post]:
        """Записи ровно за указанную календарную дату."""
        query = "SELECT * FROM posts WHERE local_year = ? AND local_month = ? AND local_day = ?"
        params: list[Any] = [day.year, day.month, day.day]
        if chat_id is not None:
            query += " AND chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY message_id"
        with self._lock:
            rows = self._conn.execute(query, params).fetchall()
        return [_from_row(row) for row in rows]

    def random_post(self, chat_id: int | None = None) -> Post | None:
        query = "SELECT * FROM posts"
        params: list[Any] = []
        if chat_id is not None:
            query += " WHERE chat_id = ?"
            params.append(chat_id)
        query += " ORDER BY RANDOM() LIMIT 1"
        with self._lock:
            row = self._conn.execute(query, params).fetchone()
        return _from_row(row) if row else None

    def group_posts(self, chat_id: int, group_id: str) -> list[Post]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM posts WHERE chat_id = ? AND group_id = ? ORDER BY message_id",
                (chat_id, group_id),
            ).fetchall()
        return [_from_row(row) for row in rows]

    # --- журнал отправок --------------------------------------------------

    def record_delivery(self, sent_on: date, chat_id: int, message_ids: Sequence[int]) -> None:
        now = int(datetime.now(tz=UTC).timestamp())
        with self._lock:
            self._conn.executemany(
                "INSERT INTO deliveries (sent_on, chat_id, message_id, sent_at) "
                "VALUES (?, ?, ?, ?)",
                [(sent_on.isoformat(), chat_id, message_id, now) for message_id in message_ids],
            )
            self._conn.commit()

    def delivered_on(self, day: date) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM deliveries WHERE sent_on = ? LIMIT 1", (day.isoformat(),)
            ).fetchone()
        return row is not None

    def delivery_counts(self, keys: Sequence[tuple[int, int]]) -> dict[tuple[int, int], int]:
        """Сколько раз каждая запись уже присылалась — чтобы не повторяться."""
        if not keys:
            return {}
        counts: dict[tuple[int, int], int] = {}
        with self._lock:
            for chunk in _chunked(list(keys), _MAX_KEYS_PER_QUERY):
                placeholders = ",".join(["(?,?)"] * len(chunk))
                rows = self._conn.execute(
                    "SELECT chat_id, message_id, COUNT(*) AS hits FROM deliveries "
                    f"WHERE (chat_id, message_id) IN (VALUES {placeholders}) "
                    "GROUP BY chat_id, message_id",
                    [value for key in chunk for value in key],
                ).fetchall()
                counts.update({(row["chat_id"], row["message_id"]): row["hits"] for row in rows})
        return counts

    # --- служебное состояние ---------------------------------------------

    def get_state(self, key: str) -> str | None:
        with self._lock:
            row = self._conn.execute("SELECT value FROM state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str | None) -> None:
        with self._lock:
            if value is None:
                self._conn.execute("DELETE FROM state WHERE key = ?", (key,))
            else:
                self._conn.execute(
                    "INSERT INTO state (key, value) VALUES (?, ?) "
                    "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                    (key, value),
                )
            self._conn.commit()

    # --- статистика и обслуживание ---------------------------------------

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total = self._conn.execute("SELECT COUNT(*) AS n FROM posts").fetchone()["n"]
            if not total:
                return {"total": 0, "per_year": {}, "chats": [], "deliveries": 0}
            per_year = {
                row["local_year"]: row["n"]
                for row in self._conn.execute(
                    "SELECT local_year, COUNT(*) AS n FROM posts GROUP BY local_year "
                    "ORDER BY local_year"
                )
            }
            chats = [
                {
                    "chat_id": row["chat_id"],
                    "title": row["chat_title"],
                    "username": row["chat_username"],
                    "count": row["n"],
                }
                for row in self._conn.execute(
                    "SELECT chat_id, MAX(chat_title) AS chat_title, "
                    "MAX(chat_username) AS chat_username, COUNT(*) AS n "
                    "FROM posts GROUP BY chat_id ORDER BY n DESC"
                )
            ]
            bounds = self._conn.execute(
                "SELECT MIN(posted_at) AS first, MAX(posted_at) AS last FROM posts"
            ).fetchone()
            deliveries = self._conn.execute(
                "SELECT COUNT(DISTINCT sent_on) AS n FROM deliveries"
            ).fetchone()["n"]
        return {
            "total": total,
            "per_year": per_year,
            "chats": chats,
            "first_at": datetime.fromtimestamp(bounds["first"], tz=UTC),
            "last_at": datetime.fromtimestamp(bounds["last"], tz=UTC),
            "deliveries": deliveries,
        }

    def recompute_local_dates(self, tz: ZoneInfo) -> int:
        """Пересчитывает локальные даты записей после смены часового пояса."""
        with self._lock:
            rows = self._conn.execute("SELECT chat_id, message_id, posted_at FROM posts").fetchall()
            updates = []
            for row in rows:
                local = datetime.fromtimestamp(row["posted_at"], tz=tz)
                updates.append(
                    (local.year, local.month, local.day, row["chat_id"], row["message_id"])
                )
            self._conn.executemany(
                "UPDATE posts SET local_year = ?, local_month = ?, local_day = ? "
                "WHERE chat_id = ? AND message_id = ?",
                updates,
            )
            self._conn.commit()
        return len(updates)


async def run(fn, *args, **kwargs):
    """Выполняет синхронный вызов хранилища в пуле потоков."""
    return await asyncio.to_thread(fn, *args, **kwargs)


def _to_row(post: Post) -> tuple:
    return (
        post.chat_id,
        post.message_id,
        int(to_utc(post.posted_at).timestamp()),
        post.local_date.year,
        post.local_date.month,
        post.local_date.day,
        post.text or "",
        post.media_kind,
        post.chat_title,
        post.chat_username,
        post.group_id,
        post.source,
    )


def _from_row(row: sqlite3.Row) -> Post:
    return Post(
        chat_id=row["chat_id"],
        message_id=row["message_id"],
        posted_at=datetime.fromtimestamp(row["posted_at"], tz=UTC),
        local_date=date(row["local_year"], row["local_month"], row["local_day"]),
        text=row["text"] or "",
        media_kind=row["media_kind"],
        chat_title=row["chat_title"],
        chat_username=row["chat_username"],
        group_id=row["group_id"],
        source=row["source"],
    )
