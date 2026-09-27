"""Превращение сообщений Telegram в записи архива."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

from .models import Post, to_utc

MEDIA_FIELDS = (
    "photo",
    "video",
    "animation",
    "audio",
    "voice",
    "video_note",
    "document",
    "sticker",
    "poll",
)


def detect_media_kind(message: Any) -> str | None:
    for field in MEDIA_FIELDS:
        if getattr(message, field, None):
            return field
    return None


def message_text(message: Any) -> str:
    text = getattr(message, "text", None) or getattr(message, "caption", None) or ""
    poll = getattr(message, "poll", None)
    if not text and poll is not None:
        return poll.question or ""
    return text


def post_from_channel_message(message: Any, tz: ZoneInfo, *, source: str = "live") -> Post:
    """Пост, полученный напрямую из канала, где бот стоит администратором."""
    posted_at = to_utc(message.date)
    return Post(
        chat_id=message.chat.id,
        message_id=message.message_id,
        posted_at=posted_at,
        local_date=posted_at.astimezone(tz).date(),
        text=message_text(message),
        media_kind=detect_media_kind(message),
        chat_title=getattr(message.chat, "title", None),
        chat_username=getattr(message.chat, "username", None),
        group_id=getattr(message, "media_group_id", None),
        source=source,
    )


def post_from_forward(message: Any, tz: ZoneInfo) -> Post | None:
    """Пост, восстановленный из сообщения, пересланного владельцем в личку боту.

    Если оригинал — пост канала, запоминаем настоящие chat_id и message_id, чтобы потом
    пересылать сам первоисточник. Иначе опираемся на копию в переписке с ботом, но
    с исходной датой публикации.
    """
    origin = getattr(message, "forward_origin", None)
    if origin is None:
        return None

    origin_date = getattr(origin, "date", None)
    if origin_date is None:
        return None
    posted_at = to_utc(origin_date)
    local_date = posted_at.astimezone(tz).date()

    origin_chat = getattr(origin, "chat", None)
    origin_message_id = getattr(origin, "message_id", None)
    if origin_chat is not None and origin_message_id is not None:
        chat_id = origin_chat.id
        message_id = origin_message_id
        chat_title = getattr(origin_chat, "title", None)
        chat_username = getattr(origin_chat, "username", None)
    else:
        chat_id = message.chat.id
        message_id = message.message_id
        chat_title = getattr(origin, "sender_user_name", None) or "Пересланное"
        chat_username = None

    return Post(
        chat_id=chat_id,
        message_id=message_id,
        posted_at=posted_at,
        local_date=local_date,
        text=message_text(message),
        media_kind=detect_media_kind(message),
        chat_title=chat_title,
        chat_username=chat_username,
        group_id=getattr(message, "media_group_id", None),
        source="forward",
    )


def local_date_for(posted_at: datetime, tz: ZoneInfo):
    return to_utc(posted_at).astimezone(tz).date()
