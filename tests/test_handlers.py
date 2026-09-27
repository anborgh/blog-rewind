"""Проверка маршрутизации: настоящий Dispatcher aiogram на поддельной сессии."""

from __future__ import annotations

from collections.abc import AsyncGenerator
from datetime import UTC, date, datetime
from typing import Any

import pytest
from aiogram import Bot
from aiogram.client.session.base import BaseSession
from aiogram.methods import CopyMessage, ForwardMessage, ForwardMessages, TelegramMethod
from aiogram.types import (
    Chat,
    ChatMemberAdministrator,
    ChatMemberLeft,
    ChatMemberMember,
    ChatMemberUpdated,
    Message,
    MessageId,
    Update,
    User,
)
from conftest import make_post

from memorybot.app import build_dispatcher
from memorybot.config import load_settings
from memorybot.handlers import parse_day
from memorybot.service import MemoryService

OWNER = 4242
STRANGER = 777
BLOG = -1001234567890
GROUP_BLOG = -1009876543210
TOKEN = "42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"


class RecordingSession(BaseSession):
    """Записывает вызовы Bot API вместо похода в сеть."""

    def __init__(self) -> None:
        super().__init__()
        self.requests: list[TelegramMethod] = []

    async def close(self) -> None:
        pass

    async def make_request(self, bot: Bot, method: TelegramMethod, timeout: int | None = None):
        self.requests.append(method)
        if isinstance(method, CopyMessage):
            return MessageId(message_id=1)
        return _fake_message()

    async def stream_content(self, *args: Any, **kwargs: Any) -> AsyncGenerator[bytes, None]:
        yield b""

    @property
    def names(self) -> list[str]:
        return [type(request).__name__ for request in self.requests]

    @property
    def texts(self) -> list[str]:
        return [request.text for request in self.requests if hasattr(request, "text")]


def _fake_message() -> Message:
    return Message(
        message_id=1,
        date=datetime.now(tz=UTC),
        chat=Chat(id=OWNER, type="private"),
    )


@pytest.fixture
def bot() -> Bot:
    return Bot(TOKEN, session=RecordingSession())


@pytest.fixture
def app(storage, bot):
    settings = load_settings({"BOT_TOKEN": TOKEN})
    service = MemoryService(bot, storage, settings)
    return build_dispatcher(service), bot, bot.session, service


def private_message(text: str, *, user_id: int = OWNER) -> Update:
    return Update(
        update_id=1,
        message=Message(
            message_id=100,
            date=datetime.now(tz=UTC),
            chat=Chat(id=user_id, type="private"),
            from_user=User(id=user_id, is_bot=False, first_name="Автор"),
            text=text,
        ),
    )


def channel_post(text: str, *, message_id: int = 5, moment: datetime | None = None) -> Update:
    return Update(
        update_id=2,
        channel_post=Message(
            message_id=message_id,
            date=moment or datetime.now(tz=UTC),
            chat=Chat(id=BLOG, type="channel", title="Личный блог", username="myblog"),
            text=text,
        ),
    )


async def test_first_start_claims_the_bot(app):
    dispatcher, bot, session, service = app

    await dispatcher.feed_update(bot, private_message("/start"))

    assert service.owner_id == OWNER
    assert "Привет!" in session.texts[0]


async def test_strangers_are_turned_away(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, private_message("/stats", user_id=STRANGER))

    assert session.texts == ["Этот бот личный: он присылает записи только своему владельцу."]


async def test_channel_posts_are_indexed(app, storage):
    dispatcher, bot, session, service = app
    moment = datetime(2026, 9, 2, 12, 0, tzinfo=UTC)

    await dispatcher.feed_update(bot, channel_post("Свежая запись", moment=moment))

    stored = storage.posts_on_date(date(2026, 9, 2))
    assert [post.text for post in stored] == ["Свежая запись"]
    assert stored[0].chat_username == "myblog"
    assert session.requests == []


def group_message(text: str, *, user_id: int = OWNER, message_id: int = 8) -> Update:
    return Update(
        update_id=3,
        message=Message(
            message_id=message_id,
            date=datetime(2026, 9, 2, 12, 0, tzinfo=UTC),
            chat=Chat(id=GROUP_BLOG, type="supergroup", title="Мои заметки"),
            from_user=User(id=user_id, is_bot=False, first_name="Автор"),
            text=text,
        ),
    )


def added_to_chat(*, chat: Chat, status: str = "administrator") -> Update:
    bot_user = User(id=42, is_bot=True, first_name="memorybot")
    member: ChatMemberAdministrator | ChatMemberMember
    if status == "administrator":
        member = ChatMemberAdministrator(
            status="administrator",
            user=bot_user,
            can_be_edited=False,
            is_anonymous=False,
            can_manage_chat=True,
            can_delete_messages=False,
            can_manage_video_chats=False,
            can_restrict_members=False,
            can_promote_members=False,
            can_change_info=False,
            can_invite_users=False,
            can_post_stories=False,
            can_edit_stories=False,
            can_delete_stories=False,
            can_send_welcome_messages=False,
        )
    else:
        member = ChatMemberMember(status="member", user=bot_user)
    return Update(
        update_id=4,
        my_chat_member=ChatMemberUpdated(
            chat=chat,
            from_user=User(id=OWNER, is_bot=False, first_name="Автор"),
            date=datetime.now(tz=UTC),
            old_chat_member=ChatMemberLeft(status="left", user=bot_user),
            new_chat_member=member,
        ),
    )


async def test_notes_from_a_group_blog_are_indexed(app, storage):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, group_message("Заметка в группе"))

    stored = storage.posts_on_date(date(2026, 9, 2))
    assert [post.text for post in stored] == ["Заметка в группе"]


async def test_other_people_in_a_group_are_not_archived(app, storage):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, group_message("Чужая реплика", user_id=STRANGER))

    assert storage.posts_on_date(date(2026, 9, 2)) == []


async def test_a_configured_group_is_archived_whoever_wrote(storage, bot):
    settings = load_settings({"BOT_TOKEN": TOKEN, "BLOG_CHAT_ID": str(GROUP_BLOG)})
    service = MemoryService(bot, storage, settings)
    service.claim_owner(OWNER)
    dispatcher = build_dispatcher(service)

    await dispatcher.feed_update(bot, group_message("Запись из блога", user_id=STRANGER))

    assert len(storage.posts_on_date(date(2026, 9, 2))) == 1


async def test_posts_from_other_chats_are_ignored_when_a_blog_is_configured(storage, bot):
    settings = load_settings({"BOT_TOKEN": TOKEN, "BLOG_CHAT_ID": str(GROUP_BLOG)})
    service = MemoryService(bot, storage, settings)
    dispatcher = build_dispatcher(service)

    await dispatcher.feed_update(bot, channel_post("Из другого канала"))

    assert storage.stats()["total"] == 0


async def test_being_added_as_admin_is_confirmed_to_the_owner(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)
    chat = Chat(id=BLOG, type="channel", title="Личный блог")

    await dispatcher.feed_update(bot, added_to_chat(chat=chat))

    reply = session.texts[-1]
    assert "Личный блог" in reply
    assert str(BLOG) in reply
    assert "result.json" in reply


async def test_being_added_without_admin_rights_is_flagged(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)
    chat = Chat(id=BLOG, type="channel", title="Личный блог")

    await dispatcher.feed_update(bot, added_to_chat(chat=chat, status="member"))

    assert "права администратора" in session.texts[-1]


async def test_today_forwards_a_memory(app, storage):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)
    today = service.today()
    storage.upsert_post(
        make_post(
            year=today.year - 2,
            month=today.month,
            day=today.day,
            message_id=11,
            chat_id=BLOG,
            text="два года назад",
        )
    )

    await dispatcher.feed_update(bot, private_message("/today"))

    assert "SendMessage" in session.names
    assert isinstance(session.requests[-1], ForwardMessage | ForwardMessages)
    assert "Ровно 2 года назад" in session.texts[0]


async def test_today_on_a_quiet_day_says_so_only_when_asked(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, private_message("/today"))

    assert session.texts == ["Сегодня в прошлые годы блог молчал — и я тоже промолчу. 🤫"]


async def test_on_command_lists_the_other_entries_of_that_day(app, storage):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)
    for index in range(1, 4):
        storage.upsert_post(
            make_post(year=2024, month=9, day=5, message_id=index, chat_id=BLOG, text=f"№{index}")
        )

    await dispatcher.feed_update(bot, private_message("/on 5.09"))

    assert "Всё, что было в этот день (3)" in session.texts[-1]


async def test_window_and_timezone_are_persisted(app, storage):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, private_message("/window 08:30-09:15"))
    await dispatcher.feed_update(bot, private_message("/timezone Europe/Belgrade"))

    assert str(service.window) == "08:30-09:15"
    assert service.timezone_name == "Europe/Belgrade"
    assert storage.get_state("window") == "08:30-09:15"


async def test_bad_window_is_reported_without_changing_settings(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, private_message("/window завтра утром"))

    assert str(service.window) == "10:00-20:00"
    assert "время" in session.texts[-1]


async def test_pause_and_resume(app):
    dispatcher, bot, session, service = app
    service.claim_owner(OWNER)

    await dispatcher.feed_update(bot, private_message("/pause"))
    assert service.paused is True

    await dispatcher.feed_update(bot, private_message("/resume"))
    assert service.paused is False


@pytest.mark.parametrize(
    ("raw", "expected", "exact"),
    [
        ("5.09", (2026, 9, 5), False),
        ("05.09.2019", (2019, 9, 5), True),
        ("2019-09-05", (2019, 9, 5), True),
        ("5/9", (2026, 9, 5), False),
    ],
)
def test_date_formats(raw, expected, exact):
    day, has_year = parse_day(raw, date(2026, 1, 1))

    assert (day.year, day.month, day.day) == expected
    assert has_year is exact


@pytest.mark.parametrize("raw", ["вчера", "32.01", "2019-13-05", ""])
def test_bad_dates_are_rejected(raw):
    with pytest.raises(ValueError):
        parse_day(raw, date(2026, 1, 1))
