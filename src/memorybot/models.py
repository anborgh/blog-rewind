"""Модели записей блога."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime

MEDIA_LABELS = {
    "photo": "фото",
    "video": "видео",
    "animation": "гифка",
    "audio": "аудио",
    "voice": "голосовое",
    "video_note": "кружок",
    "document": "файл",
    "sticker": "стикер",
    "poll": "опрос",
    "album": "альбом",
}


@dataclass(frozen=True)
class Post:
    """Одно сообщение из блога, проиндексированное ботом."""

    chat_id: int
    message_id: int
    posted_at: datetime
    local_date: date
    text: str = ""
    media_kind: str | None = None
    chat_title: str | None = None
    chat_username: str | None = None
    group_id: str | None = None
    source: str = "live"

    @property
    def key(self) -> tuple[int, int]:
        return (self.chat_id, self.message_id)

    @property
    def preview(self) -> str:
        return summarize(self.text, self.media_kind)

    @property
    def link(self) -> str | None:
        """Публичная ссылка на пост, если её можно построить."""
        if self.chat_username:
            return f"https://t.me/{self.chat_username.lstrip('@')}/{self.message_id}"
        as_text = str(self.chat_id)
        if as_text.startswith("-100"):
            return f"https://t.me/c/{as_text[4:]}/{self.message_id}"
        return None


@dataclass
class Memory:
    """Воспоминание за конкретный день: одна запись или альбом из нескольких сообщений."""

    posts: list[Post]
    years_ago: int
    seen_before: int = 0
    siblings: int = 1
    extra: dict[str, str] = field(default_factory=dict)

    @property
    def head(self) -> Post:
        return self.posts[0]

    @property
    def message_ids(self) -> list[int]:
        return [post.message_id for post in self.posts]

    @property
    def is_album(self) -> bool:
        return len(self.posts) > 1


def summarize(text: str, media_kind: str | None, limit: int = 160) -> str:
    """Короткое описание записи для списков и служебных сообщений."""
    flat = " ".join((text or "").split())
    if flat:
        if len(flat) > limit:
            flat = flat[: limit - 1].rstrip() + "…"
        return flat
    label = MEDIA_LABELS.get(media_kind or "")
    return f"[{label}]" if label else "[без текста]"


def to_utc(moment: datetime) -> datetime:
    """Приводит момент времени к UTC, считая наивные значения уже UTC."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)
