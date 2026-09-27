"""Планировщик: один раз в сутки, в непредсказуемый момент внутри окна."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import random
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from .config import TimeWindow
from .service import MemoryService

log = logging.getLogger(__name__)

# Как часто пересматривать план: настройки и часовой пояс можно менять на ходу.
RECHECK_INTERVAL = timedelta(minutes=15)
# Задержка перед догоняющей отправкой, если бот был выключен в запланированный момент.
CATCH_UP_DELAY = timedelta(seconds=30)


def moment_for_day(day: date, window: TimeWindow, tz: ZoneInfo, salt: str) -> datetime:
    """Момент напоминания в конкретный день.

    Внутри суток время случайное, но детерминированное: перезапуск бота или
    пересчёт плана в течение дня не сдвигают уже выбранный момент.
    """
    if window.is_fixed:
        moment = window.start
    else:
        rng = random.Random(f"{salt}:{day.isoformat()}")
        start = window.start.hour * 60 + window.start.minute
        end = window.end.hour * 60 + window.end.minute
        minutes = rng.randint(start, end)
        moment = time(hour=minutes // 60, minute=minutes % 60)
    return datetime.combine(day, moment, tzinfo=tz)


def next_run_at(
    now: datetime,
    window: TimeWindow,
    tz: ZoneInfo,
    salt: str,
    *,
    checked_today: bool,
) -> datetime:
    """Когда бот должен проверить сегодняшний день в следующий раз."""
    if not checked_today:
        today = moment_for_day(now.date(), window, tz, salt)
        if now <= today:
            return today
        window_end = datetime.combine(now.date(), window.end, tzinfo=tz)
        if not window.is_fixed and now <= window_end:
            return now + CATCH_UP_DELAY
    return moment_for_day(now.date() + timedelta(days=1), window, tz, salt)


class DailyReminder:
    """Фоновая задача, которая будит сервис в нужный момент."""

    def __init__(self, service: MemoryService) -> None:
        self.service = service
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="memorybot-daily")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await self._task
        self._task = None

    def next_run(self) -> datetime:
        tz = self.service.timezone
        now = datetime.now(tz=tz)
        return next_run_at(
            now,
            self.service.window,
            tz,
            self.service.schedule_salt,
            checked_today=self.service.checked_on(now.date()),
        )

    async def _loop(self) -> None:
        log.info("Планировщик запущен")
        while True:
            try:
                if self.service.paused:
                    await asyncio.sleep(RECHECK_INTERVAL.total_seconds())
                    continue
                target = await asyncio.to_thread(self.next_run)
                now = datetime.now(tz=self.service.timezone)
                delay = target - now
                if delay > RECHECK_INTERVAL:
                    await asyncio.sleep(RECHECK_INTERVAL.total_seconds())
                    continue
                # Дождавшись цели, отправляем сразу, не пересчитывая план: после сна
                # мы уже позади момента, и next_run_at предложит «догнать через
                # CATCH_UP_DELAY от сейчас» — цель уезжала бы вперёд бесконечно.
                if delay.total_seconds() > 0:
                    await asyncio.sleep(delay.total_seconds())
                await self.service.deliver()
            except asyncio.CancelledError:
                log.info("Планировщик остановлен")
                raise
            except Exception:
                log.exception("Сбой в планировщике, повтор через минуту")
                await asyncio.sleep(60)
