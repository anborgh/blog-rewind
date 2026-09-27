from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import pytest
from conftest import make_post

from memorybot.config import load_settings
from memorybot.service import MemoryService

OWNER = 4242


class FakeBot:
    """Достаточно правдоподобный Bot, чтобы проверить логику доставки."""

    def __init__(self, *, failing: set[str] | None = None) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.failing = failing or set()

    def _record(self, name: str, kwargs: dict) -> None:
        self.calls.append((name, kwargs))
        if name in self.failing:
            from aiogram.exceptions import TelegramBadRequest

            raise TelegramBadRequest(method=SimpleNamespace(), message="chat not found")

    async def send_message(self, chat_id, text, **kwargs):
        self._record("send_message", {"chat_id": chat_id, "text": text, **kwargs})

    async def forward_message(self, **kwargs):
        self._record("forward_message", kwargs)

    async def forward_messages(self, **kwargs):
        self._record("forward_messages", kwargs)

    async def copy_message(self, **kwargs):
        self._record("copy_message", kwargs)

    async def copy_messages(self, **kwargs):
        self._record("copy_messages", kwargs)

    @property
    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    @property
    def texts(self) -> list[str]:
        return [kwargs["text"] for name, kwargs in self.calls if name == "send_message"]


@pytest.fixture
def service(storage):
    def build(bot: FakeBot | None = None, **env) -> MemoryService:
        settings = load_settings({"BOT_TOKEN": "123:abc", "OWNER_ID": str(OWNER), **env})
        return MemoryService(bot or FakeBot(), storage, settings)

    return build


async def test_stays_silent_when_nothing_was_written(service):
    bot = FakeBot()
    memories = service(bot)

    outcome = await memories.deliver(day=date(2026, 9, 2))

    assert outcome.delivered is False
    assert bot.calls == []


async def test_sends_a_memory_from_a_year_ago(service, storage):
    storage.upsert_post(make_post(year=2025, month=9, day=2, message_id=11, text="год назад"))
    bot = FakeBot()
    memories = service(bot)

    outcome = await memories.deliver(day=date(2026, 9, 2))

    assert outcome.delivered is True
    assert outcome.memory.years_ago == 1
    assert bot.names == ["send_message", "forward_message"]
    assert "Ровно 1 год назад" in bot.texts[0]
    assert bot.calls[1][1] == {"chat_id": OWNER, "from_chat_id": -1001, "message_id": 11}


async def test_only_one_reminder_per_day(service, storage):
    storage.upsert_post(make_post(year=2025, message_id=11))
    bot = FakeBot()
    memories = service(bot)

    first = await memories.deliver(day=date(2026, 9, 2))
    second = await memories.deliver(day=date(2026, 9, 2))

    assert first.delivered is True
    assert second.delivered is False
    assert bot.names == ["send_message", "forward_message"]


async def test_quiet_day_is_remembered_so_the_search_is_not_repeated(service, storage):
    bot = FakeBot()
    memories = service(bot)

    await memories.deliver(day=date(2026, 9, 2))

    assert memories.checked_on(date(2026, 9, 2)) is True


async def test_paused_bot_says_nothing(service, storage):
    storage.upsert_post(make_post(year=2025, message_id=11))
    bot = FakeBot()
    memories = service(bot)
    memories.set_paused(True)

    outcome = await memories.deliver(day=date(2026, 9, 2))

    assert outcome.delivered is False
    assert bot.calls == []


async def test_album_is_forwarded_as_a_whole(service, storage):
    storage.upsert_posts(
        [
            make_post(year=2024, message_id=10, group_id="album:10"),
            make_post(year=2024, message_id=11, group_id="album:10"),
        ]
    )
    bot = FakeBot()
    memories = service(bot)

    await memories.deliver(day=date(2026, 9, 2))

    assert bot.names == ["send_message", "forward_messages"]
    assert bot.calls[1][1]["message_ids"] == [10, 11]


async def test_falls_back_to_a_copy_when_forwarding_is_blocked(service, storage):
    storage.upsert_post(make_post(year=2025, message_id=11))
    bot = FakeBot(failing={"forward_message"})
    memories = service(bot)

    outcome = await memories.deliver(day=date(2026, 9, 2))

    assert outcome.strategy == "copy"
    assert bot.names == ["send_message", "forward_message", "copy_message"]


async def test_falls_back_to_text_with_a_link_when_the_post_is_unreachable(service, storage):
    storage.upsert_post(
        make_post(year=2025, chat_id=-1001234567890, message_id=11, text="старая запись")
    )
    bot = FakeBot(failing={"forward_message", "copy_message"})
    memories = service(bot)

    outcome = await memories.deliver(day=date(2026, 9, 2))

    assert outcome.strategy == "fallback"
    assert "старая запись" in bot.texts[-1]
    assert "https://t.me/c/1234567890/11" in bot.texts[-1]


async def test_mentions_how_many_other_entries_that_day_had(service, storage):
    storage.upsert_posts([make_post(year=2025, message_id=index) for index in (1, 2, 3)])
    bot = FakeBot()
    memories = service(bot)

    await memories.deliver(day=date(2026, 9, 2))

    assert "было ещё 2 записи" in bot.texts[0]


async def test_first_visitor_becomes_the_owner(storage):
    settings = load_settings({"BOT_TOKEN": "123:abc"})
    memories = MemoryService(FakeBot(), storage, settings)

    assert memories.owner_id is None
    assert memories.claim_owner(777) is True
    assert memories.owner_id == 777
    assert memories.claim_owner(888) is False
    assert memories.owner_id == 777


async def test_timezone_change_moves_posts_between_days(service, storage):
    storage.upsert_post(make_post(year=2025, month=9, day=2, hour=1, message_id=11))
    memories = service()

    assert memories.collect(date(2026, 9, 2))

    memories.set_timezone("Europe/London")

    assert memories.collect(date(2026, 9, 2)) == []
    assert memories.collect(date(2026, 9, 1))


async def test_manual_request_does_not_cancel_the_daily_reminder(service, storage):
    storage.upsert_post(make_post(year=2025, message_id=11))
    storage.upsert_post(make_post(year=2024, message_id=12))
    bot = FakeBot()
    memories = service(bot)

    manual = await memories.deliver(day=date(2026, 9, 2), force=True)
    scheduled = await memories.deliver(day=date(2026, 9, 2))

    assert manual.delivered is True
    assert scheduled.delivered is True
    assert memories.checked_on(date(2026, 9, 2)) is True
    # Ротация учитывает ручную отправку: планировщик выбирает другую запись.
    assert manual.memory.head.message_id != scheduled.memory.head.message_id
