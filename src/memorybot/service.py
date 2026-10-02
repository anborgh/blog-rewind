"""Прикладная логика: что и когда присылать владельцу."""

from __future__ import annotations

import logging
import random
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from .anniversaries import anniversary_day_keys, build_memories, choose_memory, choose_per_year
from .config import Settings, TimeWindow, parse_timezone, parse_window
from .delivery import send_memory
from .models import Memory, Post
from .storage import Storage, run
from .texts import years_ago_phrase

log = logging.getLogger(__name__)

STATE_TIMEZONE = "timezone"
STATE_WINDOW = "window"
STATE_PAUSED = "paused"
STATE_OWNER = "owner_id"
STATE_BLOG_CHAT = "blog_chat_id"
STATE_SALT = "schedule_salt"
STATE_LAST_CHECK = "last_check"


@dataclass
class DeliveryOutcome:
    """Итог отправки дайджеста: что отправлено (по одному воспоминанию на год) и как."""

    memories: list[Memory]
    candidates: list[Memory]
    strategies: list[str]

    @property
    def delivered(self) -> bool:
        return bool(self.memories)


class MemoryService:
    """Единая точка входа для команд бота и планировщика."""

    def __init__(self, bot: Bot, storage: Storage, settings: Settings) -> None:
        self.bot = bot
        self.storage = storage
        self.settings = settings
        self._rng = random.Random()
        # Планировщик подставляет сюда свой расчёт, чтобы /settings показывал время.
        self.next_run_provider: Callable[[], datetime] | None = None

    # --- настройки, которые можно менять на лету ---------------------------

    @property
    def timezone_name(self) -> str:
        return self.storage.get_state(STATE_TIMEZONE) or self.settings.timezone_name

    @property
    def timezone(self) -> ZoneInfo:
        return parse_timezone(self.timezone_name)

    @property
    def window(self) -> TimeWindow:
        stored = self.storage.get_state(STATE_WINDOW)
        return parse_window(stored) if stored else self.settings.window

    @property
    def paused(self) -> bool:
        return self.storage.get_state(STATE_PAUSED) == "1"

    @property
    def owner_id(self) -> int | None:
        stored = self.storage.get_state(STATE_OWNER)
        if stored:
            return int(stored)
        return self.settings.owner_id

    @property
    def blog_chat_id(self) -> int | None:
        """Канал блога, если он задан явно; иначе индексируем всё, куда добавили бота."""
        if isinstance(self.settings.blog_chat, int):
            return self.settings.blog_chat
        stored = self.storage.get_state(STATE_BLOG_CHAT)
        return int(stored) if stored else None

    @property
    def schedule_salt(self) -> str:
        salt = self.storage.get_state(STATE_SALT)
        if not salt:
            salt = secrets.token_hex(8)
            self.storage.set_state(STATE_SALT, salt)
        return salt

    def set_timezone(self, name: str) -> int:
        tz = parse_timezone(name)
        self.storage.set_state(STATE_TIMEZONE, name.strip())
        return self.storage.recompute_local_dates(tz)

    def set_window(self, raw: str) -> TimeWindow:
        window = parse_window(raw)
        self.storage.set_state(STATE_WINDOW, str(window))
        return window

    def set_paused(self, flag: bool) -> None:
        self.storage.set_state(STATE_PAUSED, "1" if flag else None)

    def claim_owner(self, user_id: int) -> bool:
        """Первый, кто написал боту, становится владельцем. Возвращает True, если это он."""
        current = self.owner_id
        if current is None:
            self.storage.set_state(STATE_OWNER, str(user_id))
            return True
        return current == user_id

    def checked_on(self, day: date) -> bool:
        """Отмечаем каждый разобранный день, включая пустые: иначе бот будет
        снова и снова искать воспоминания в сутках, где записей нет."""
        return self.storage.get_state(STATE_LAST_CHECK) == day.isoformat()

    def mark_checked(self, day: date) -> None:
        self.storage.set_state(STATE_LAST_CHECK, day.isoformat())

    def accepts_chat(self, chat_id: int) -> bool:
        configured = self.blog_chat_id
        return configured is None or configured == chat_id

    # --- поиск воспоминаний ------------------------------------------------

    def today(self) -> date:
        return datetime.now(tz=self.timezone).date()

    def collect(self, day: date, *, min_years_ago: int | None = None) -> list[Memory]:
        """Все воспоминания за указанный день из прошлых лет."""
        min_years = self.settings.min_years_ago if min_years_ago is None else min_years_ago
        posts = self.storage.posts_on(
            anniversary_day_keys(day),
            max_year=day.year - min_years,
            chat_id=self.blog_chat_id,
        )
        counts = self.storage.delivery_counts([post.key for post in posts])
        return build_memories(posts, day, min_years_ago=min_years, delivery_counts=counts)

    def exact_day(self, day: date) -> list[Memory]:
        """Записи ровно за указанную дату, включая текущий год."""
        posts = self.storage.posts_on_date(day, chat_id=self.blog_chat_id)
        return self._to_memories(posts)

    def _to_memories(self, posts: Sequence[Post]) -> list[Memory]:
        counts = self.storage.delivery_counts([post.key for post in posts])
        return build_memories(posts, self.today(), min_years_ago=0, delivery_counts=counts)

    def pick(self, memories: list[Memory]) -> Memory | None:
        return choose_memory(memories, self._rng)

    def digest(self, memories: list[Memory]) -> list[Memory]:
        return choose_per_year(memories, self._rng)

    def random_post(self) -> Post | None:
        return self.storage.random_post(self.blog_chat_id)

    def next_run_hint(self) -> datetime | None:
        return self.next_run_provider() if self.next_run_provider else None

    # --- доставка ----------------------------------------------------------

    async def send(self, memory: Memory | None) -> str | None:
        """Отправляет конкретное воспоминание владельцу (используется командами)."""
        owner = self.owner_id
        if memory is None or owner is None:
            return None
        strategy = await send_memory(self.bot, owner, memory)
        await run(
            self.storage.record_delivery, self.today(), memory.head.chat_id, memory.message_ids
        )
        return strategy

    async def deliver(
        self,
        *,
        day: date | None = None,
        force: bool = False,
        record: bool = True,
    ) -> DeliveryOutcome:
        """Присылает дайджест: по одному случайному посту из каждого прошлого года.

        Молчит, если в этот день ни в одном из прошлых лет ничего не было.
        """
        owner = self.owner_id
        if owner is None:
            log.warning("Владелец неизвестен: пропускаю доставку")
            return DeliveryOutcome([], [], [])
        if self.paused and not force:
            return DeliveryOutcome([], [], [])

        target = day or self.today()
        if record and not force and await run(self.checked_on, target):
            log.info("За %s уже проверяли, пропускаю", target)
            return DeliveryOutcome([], [], [])

        candidates = await run(self.collect, target)
        digest = self.digest(candidates)
        if not digest:
            log.info("За %s в прошлые годы записей нет", target)
            if record and not force:
                await run(self.mark_checked, target)
            return DeliveryOutcome([], candidates, [])

        strategies: list[str] = []
        for memory in digest:
            strategy = await send_memory(self.bot, owner, memory)
            strategies.append(strategy)
            if record:
                await run(
                    self.storage.record_delivery, target, memory.head.chat_id, memory.message_ids
                )
            log.info(
                "Отправлено воспоминание за %s (%s, способ %s)",
                memory.head.local_date,
                years_ago_phrase(memory.years_ago),
                strategy,
            )
        # Ручной /today не отменяет ежедневный дайджест: день помечает только планировщик.
        if record and not force:
            await run(self.mark_checked, target)
        return DeliveryOutcome(digest, candidates, strategies)
