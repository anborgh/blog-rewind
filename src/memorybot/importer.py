"""Импорт архива блога из JSON-экспорта Telegram Desktop.

Bot API не умеет читать историю канала, поэтому старые записи попадают в архив
через экспорт: Настройки канала → Экспорт истории → формат JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from .models import Post
from .storage import Storage

MEDIA_TYPE_MAP = {
    "video_file": "video",
    "animation": "animation",
    "voice_message": "voice",
    "video_message": "video_note",
    "audio_file": "audio",
    "sticker": "sticker",
}

CHANNEL_LIKE = {"public_channel", "private_channel", "public_supergroup", "private_supergroup"}


class ExportParseError(ValueError):
    """Файл не похож на экспорт Telegram."""


@dataclass
class ImportResult:
    parsed: int
    added: int
    skipped: int
    chats: dict[int, str]

    @property
    def updated(self) -> int:
        return self.parsed - self.added


def load_export(path: Path | str) -> Any:
    raw = Path(path).read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ExportParseError(f"Не удалось разобрать JSON: {exc}") from exc


def iter_chats(data: Any) -> list[dict]:
    """Поддерживает и экспорт одного чата, и полный экспорт аккаунта."""
    if isinstance(data, dict) and isinstance(data.get("messages"), list):
        return [data]
    chats = data.get("chats") if isinstance(data, dict) else None
    if isinstance(chats, dict) and isinstance(chats.get("list"), list):
        return [chat for chat in chats["list"] if isinstance(chat.get("messages"), list)]
    raise ExportParseError(
        "В файле нет сообщений. Нужен result.json из экспорта Telegram Desktop в формате JSON."
    )


def normalize_chat_id(raw_id: Any, chat_type: str | None) -> int | None:
    try:
        value = int(raw_id)
    except (TypeError, ValueError):
        return None
    if value < 0:
        return value
    if chat_type in CHANNEL_LIKE:
        return int(f"-100{value}")
    return value


def extract_text(node: Any) -> str:
    if isinstance(node, str):
        return node
    if isinstance(node, list):
        parts = []
        for item in node:
            if isinstance(item, str):
                parts.append(item)
            elif isinstance(item, dict):
                parts.append(str(item.get("text", "")))
        return "".join(parts)
    return ""


def extract_media_kind(message: dict) -> str | None:
    if message.get("photo"):
        return "photo"
    media_type = message.get("media_type")
    if media_type in MEDIA_TYPE_MAP:
        return MEDIA_TYPE_MAP[media_type]
    if message.get("poll"):
        return "poll"
    if message.get("file") or message.get("file_name"):
        return "document"
    return None


def message_timestamp(message: dict) -> datetime | None:
    unixtime = message.get("date_unixtime")
    if unixtime is not None:
        try:
            return datetime.fromtimestamp(int(unixtime), tz=UTC)
        except (TypeError, ValueError, OSError):
            pass
    raw = message.get("date")
    if isinstance(raw, str):
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def parse_chat(chat: dict, tz: ZoneInfo, chat_id_override: int | None = None) -> list[Post]:
    chat_id = chat_id_override or normalize_chat_id(chat.get("id"), chat.get("type"))
    if chat_id is None:
        raise ExportParseError(
            "В экспорте не нашёлся id чата. Укажите его вручную: --chat-id -1001234567890"
        )
    title = chat.get("name") or None

    posts: list[Post] = []
    for message in chat["messages"]:
        if not isinstance(message, dict) or message.get("type") != "message":
            continue
        message_id = message.get("id")
        posted_at = message_timestamp(message)
        if message_id is None or posted_at is None:
            continue

        text = extract_text(message.get("text"))
        media_kind = extract_media_kind(message)
        if not text and media_kind is None:
            continue

        posts.append(
            Post(
                chat_id=chat_id,
                message_id=int(message_id),
                posted_at=posted_at,
                local_date=posted_at.astimezone(tz).date(),
                text=text,
                media_kind=media_kind,
                chat_title=title,
                chat_username=None,
                group_id=None,
                source="import",
            )
        )

    return detect_albums(posts)


def detect_albums(posts: list[Post]) -> list[Post]:
    """Восстанавливает альбомы: в экспорте нет media_group_id.

    Альбом — это идущие подряд медиа с одинаковой секундой публикации, у которых
    подпись есть только у первого сообщения. Именно так Telegram Desktop
    раскладывает сгруппированные вложения.
    """
    result = list(posts)
    start = 0
    while start < len(result):
        end = start
        while end + 1 < len(result) and _continues_album(result[end], result[end + 1]):
            end += 1
        if end > start:
            group_id = f"album:{result[start].message_id}"
            for index in range(start, end + 1):
                result[index] = replace(result[index], group_id=group_id)
        start = end + 1
    return result


def _continues_album(previous: Post, current: Post) -> bool:
    return (
        previous.media_kind is not None
        and current.media_kind is not None
        and not current.text
        and previous.chat_id == current.chat_id
        and current.message_id == previous.message_id + 1
        and int(previous.posted_at.timestamp()) == int(current.posted_at.timestamp())
    )


def parse_export(data: Any, tz: ZoneInfo, chat_id_override: int | None = None) -> list[Post]:
    posts: list[Post] = []
    for chat in iter_chats(data):
        posts.extend(parse_chat(chat, tz, chat_id_override))
    return posts


def import_export_file(
    storage: Storage,
    path: Path | str,
    tz: ZoneInfo,
    chat_id_override: int | None = None,
) -> ImportResult:
    data = load_export(path)
    posts = parse_export(data, tz, chat_id_override)
    added = storage.upsert_posts(posts)
    chats = {post.chat_id: post.chat_title or str(post.chat_id) for post in posts}
    return ImportResult(parsed=len(posts), added=added, skipped=0, chats=chats)
