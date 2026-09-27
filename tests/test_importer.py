from __future__ import annotations

from zoneinfo import ZoneInfo

import pytest

from memorybot.importer import ExportParseError, parse_export

MOSCOW = ZoneInfo("Europe/Moscow")


def export(messages, **overrides):
    payload = {
        "name": "Мой блог",
        "type": "private_channel",
        "id": 1234567890,
        "messages": messages,
    }
    payload.update(overrides)
    return payload


def message(message_id, unixtime, **fields):
    base = {
        "id": message_id,
        "type": "message",
        "date": "2020-01-01T12:00:00",
        "date_unixtime": str(unixtime),
        "text": "",
    }
    base.update(fields)
    return base


def test_channel_id_gets_the_bot_api_prefix():
    posts = parse_export(export([message(1, 1577880000, text="привет")]), MOSCOW)

    assert len(posts) == 1
    assert posts[0].chat_id == -1001234567890
    assert posts[0].chat_title == "Мой блог"
    assert posts[0].source == "import"


def test_chat_id_can_be_overridden():
    posts = parse_export(export([message(1, 1577880000, text="привет")]), MOSCOW, -100999)

    assert posts[0].chat_id == -100999


def test_rich_text_is_flattened():
    rich = ["Смотри ", {"type": "link", "text": "сюда"}, " и всё"]
    posts = parse_export(export([message(1, 1577880000, text=rich)]), MOSCOW)

    assert posts[0].text == "Смотри сюда и всё"


def test_local_date_follows_the_configured_timezone():
    # 2020-01-01 22:30 UTC — это уже 2 января в Москве.
    posts = parse_export(export([message(1, 1577917800, text="ночная запись")]), MOSCOW)

    assert posts[0].local_date.isoformat() == "2020-01-02"


def test_media_types_are_recognised():
    messages = [
        message(1, 1577880000, photo="photos/photo_1.jpg"),
        message(2, 1577880100, file="video_files/v.mp4", media_type="video_file"),
        message(3, 1577880200, file="voice/v.ogg", media_type="voice_message"),
        message(4, 1577880300, file="files/doc.pdf"),
    ]

    posts = parse_export(export(messages), MOSCOW)

    assert [post.media_kind for post in posts] == ["photo", "video", "voice", "document"]


def test_service_messages_and_empty_messages_are_skipped():
    messages = [
        {"id": 1, "type": "service", "action": "pin_message", "date_unixtime": "1577880000"},
        message(2, 1577880100),
        message(3, 1577880200, text="настоящая запись"),
    ]

    posts = parse_export(export(messages), MOSCOW)

    assert [post.message_id for post in posts] == [3]


def test_consecutive_media_posted_in_one_second_form_an_album():
    messages = [
        message(10, 1577880000, photo="photos/1.jpg", text="подпись альбома"),
        message(11, 1577880000, photo="photos/2.jpg"),
        message(12, 1577880000, photo="photos/3.jpg"),
        message(20, 1577890000, photo="photos/4.jpg"),
    ]

    posts = parse_export(export(messages), MOSCOW)

    assert [post.group_id for post in posts] == ["album:10", "album:10", "album:10", None]


def test_full_account_export_is_supported():
    data = {
        "about": "экспорт",
        "chats": {
            "about": "чаты",
            "list": [export([message(1, 1577880000, text="из аккаунта")])],
        },
    }

    posts = parse_export(data, MOSCOW)

    assert [post.text for post in posts] == ["из аккаунта"]


def test_unrelated_json_is_rejected_with_a_hint():
    with pytest.raises(ExportParseError, match="result.json"):
        parse_export({"hello": "world"}, MOSCOW)
