from __future__ import annotations

import asyncio
from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from memorybot.config import parse_window
from memorybot.scheduler import (
    CATCH_UP_DELAY,
    DailyReminder,
    moment_for_day,
    next_run_at,
)

MOSCOW = ZoneInfo("Europe/Moscow")
SALT = "test-salt"
WINDOW = parse_window("10:00-20:00")
FIXED = parse_window("09:30")


def test_moment_stays_inside_the_window():
    for offset in range(60):
        day = date(2026, 1, 1) + timedelta(days=offset)
        moment = moment_for_day(day, WINDOW, MOSCOW, SALT)
        assert moment.date() == day
        assert WINDOW.start <= moment.time() <= WINDOW.end


def test_moment_is_stable_within_a_day_but_moves_between_days():
    day = date(2026, 9, 2)
    assert moment_for_day(day, WINDOW, MOSCOW, SALT) == moment_for_day(day, WINDOW, MOSCOW, SALT)

    moments = {
        moment_for_day(day + timedelta(days=offset), WINDOW, MOSCOW, SALT).time()
        for offset in range(30)
    }
    assert len(moments) > 5


def test_fixed_window_means_the_same_time_every_day():
    assert moment_for_day(date(2026, 9, 2), FIXED, MOSCOW, SALT).time() == FIXED.start
    assert moment_for_day(date(2026, 9, 3), FIXED, MOSCOW, SALT).time() == FIXED.start


def test_waits_for_todays_moment_when_it_is_still_ahead():
    target = moment_for_day(date(2026, 9, 2), WINDOW, MOSCOW, SALT)
    now = target - timedelta(hours=1)

    assert next_run_at(now, WINDOW, MOSCOW, SALT, checked_today=False) == target


def test_catches_up_when_the_bot_was_offline_but_the_window_is_open():
    target = moment_for_day(date(2026, 9, 2), WINDOW, MOSCOW, SALT)
    now = target + timedelta(minutes=5)

    planned = next_run_at(now, WINDOW, MOSCOW, SALT, checked_today=False)

    assert planned == now + CATCH_UP_DELAY


def test_gives_up_on_today_once_the_window_has_closed():
    now = datetime(2026, 9, 2, 23, 0, tzinfo=MOSCOW)

    planned = next_run_at(now, WINDOW, MOSCOW, SALT, checked_today=False)

    assert planned.date() == date(2026, 9, 3)


class StubService:
    def __init__(self, *, paused: bool = False) -> None:
        self.paused = paused
        self.timezone = MOSCOW
        self.deliveries = 0

    async def deliver(self) -> None:
        self.deliveries += 1


async def wait_for(condition, timeout: float = 2.0) -> bool:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if condition():
            return True
        await asyncio.sleep(0.02)
    return False


async def test_loop_delivers_once_the_moment_has_come():
    service = StubService()
    reminder = DailyReminder(service)
    now = datetime.now(tz=MOSCOW)
    planned = iter([now - timedelta(seconds=1), now + timedelta(days=1)])
    reminder.next_run = lambda: next(planned, now + timedelta(days=1))

    reminder.start()
    delivered = await wait_for(lambda: service.deliveries == 1)
    await reminder.stop()

    assert delivered, "планировщик не разбудил сервис"
    assert service.deliveries == 1


async def test_loop_delivers_when_the_plan_is_relative_to_now():
    # Проснувшись к моменту, планировщик всегда опаздывает на доли секунды, и
    # next_run_at отвечает «догнать через CATCH_UP_DELAY от сейчас». Цель не должна
    # отодвигаться при каждом пересчёте, иначе отправка не наступит никогда.
    service = StubService()
    reminder = DailyReminder(service)
    reminder.next_run = lambda: datetime.now(tz=MOSCOW) + timedelta(seconds=0.05)

    reminder.start()
    delivered = await wait_for(lambda: service.deliveries > 0)
    await reminder.stop()

    assert delivered, "цель уезжает вперёд, отправка не наступает"


async def test_loop_keeps_quiet_while_paused():
    service = StubService(paused=True)
    reminder = DailyReminder(service)
    reminder.next_run = lambda: datetime.now(tz=MOSCOW) - timedelta(seconds=1)

    reminder.start()
    woke_up = await wait_for(lambda: service.deliveries > 0, timeout=0.4)
    await reminder.stop()

    assert woke_up is False


def test_one_reminder_per_day():
    now = datetime(2026, 9, 2, 11, 0, tzinfo=MOSCOW)

    planned = next_run_at(now, WINDOW, MOSCOW, SALT, checked_today=True)

    assert planned == moment_for_day(date(2026, 9, 3), WINDOW, MOSCOW, SALT)
