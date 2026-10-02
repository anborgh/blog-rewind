"""Тексты и форматирование сообщений бота."""

from __future__ import annotations

from datetime import date, datetime

from .config import TimeWindow
from .models import Memory

MONTHS_GENITIVE = (
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
)


def plural(count: int, one: str, few: str, many: str) -> str:
    if count % 100 in range(11, 15):
        return many
    last = count % 10
    if last == 1:
        return one
    if last in (2, 3, 4):
        return few
    return many


def years_phrase(years: int) -> str:
    return f"{years} {plural(years, 'год', 'года', 'лет')}"


def years_ago_phrase(years: int) -> str:
    return f"{years_phrase(years)} назад"


def entries_phrase(count: int) -> str:
    return f"{count} {plural(count, 'запись', 'записи', 'записей')}"


def human_date(day: date) -> str:
    return f"{day.day} {MONTHS_GENITIVE[day.month - 1]} {day.year}"


def memory_header(memory: Memory) -> str:
    when = human_date(memory.head.local_date)
    title = (
        f"🕰 <b>Ровно {years_ago_phrase(memory.years_ago)}</b> — {when}"
        if memory.years_ago >= 1
        else f"🕰 <b>{when}</b>"
    )
    parts = [title]
    if memory.siblings > 1:
        others = memory.siblings - 1
        parts.append(f"<i>В этот день было ещё {entries_phrase(others)}; эта выбрана случайно.</i>")
    return "\n".join(parts)


def memory_fallback(memory: Memory) -> str:
    """Текст на случай, если переслать оригинал не получилось."""
    head = memory.head
    lines = [f"<b>{human_date(head.local_date)}</b>", ""]
    if head.text:
        body = head.text if len(head.text) <= 3000 else head.text[:2999] + "…"
        lines.append(escape(body))
    else:
        lines.append(f"<i>{escape(head.preview)}</i>")
    link = head.link
    if link:
        lines += ["", f'<a href="{link}">Открыть в блоге</a>']
    return "\n".join(lines)


def escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


START = (
    "Привет! Я присылаю дайджест того, что вы писали в свой блог ровно год, два, три и больше лет назад.\n\n"
    "<b>Как меня включить</b>\n"
    "1. Добавьте меня администратором в канал или группу с блогом — новые записи "
    "я запоминаю сам. Без прав администратора Telegram не показывает мне сообщения.\n"
    "2. Чтобы поднять архив, сделайте экспорт канала в Telegram Desktop "
    "(Настройки канала → Экспорт истории, формат JSON) и загрузите <code>result.json</code>: "
    "просто пришлите файл сюда или запустите <code>memorybot import result.json</code>.\n"
    "3. Можно и вручную: перешлите мне любой пост из блога — я запишу его "
    "вместе с исходной датой.\n\n"
    "Раз в день в заданное время (по умолчанию 12:00) я пришлю по одной случайной записи "
    "этого дня из каждого прошлого года. Если в этот день ничего не было — промолчу.\n\n"
    "Команды: /today, /on, /random, /stats, /settings, /help"
)

HELP = (
    "<b>Что я умею</b>\n\n"
    "/today — проверить сегодняшний день прямо сейчас\n"
    "/on 5.09 или /on 2019-09-05 — воспоминания за конкретную дату\n"
    "/random — случайная запись из архива\n"
    "/stats — сколько записей проиндексировано и за какие годы\n"
    "/settings — текущие настройки\n"
    "/window 10:00-20:00 — окно, внутри которого приходит напоминание "
    "(одно значение = фиксированное время)\n"
    "/timezone Europe/Moscow — часовой пояс\n"
    "/pause и /resume — временно выключить или включить напоминания\n\n"
    "Пополнить архив: пришлите мне <code>result.json</code> из экспорта Telegram Desktop "
    "или перешлите отдельные посты."
)

NOTHING_TODAY = "Сегодня в прошлые годы блог молчал — и я тоже промолчу. 🤫"
PAUSED = "Напоминания на паузе. Включить обратно — /resume"
RESUMED = "Напоминания снова включены."
NOT_OWNER = "Этот бот личный: он присылает записи только своему владельцу."
EMPTY_ARCHIVE = (
    "Архив пока пуст. Добавьте меня администратором в канал блога "
    "или пришлите <code>result.json</code> из экспорта Telegram Desktop."
)


def settings_text(
    *,
    timezone_name: str,
    window: TimeWindow,
    paused: bool,
    min_years_ago: int,
    next_run: datetime | None,
    total: int,
) -> str:
    schedule = (
        f"каждый день в {window.start:%H:%M}"
        if window.is_fixed
        else f"каждый день в случайный момент между {window.start:%H:%M} и {window.end:%H:%M}"
    )
    lines = [
        "<b>Настройки</b>",
        f"Расписание: {schedule}",
        f"Часовой пояс: <code>{escape(timezone_name)}</code>",
        f"Записи старше: {years_phrase(min_years_ago)}",
        f"Записей в архиве: {total}",
        f"Статус: {'на паузе' if paused else 'работает'}",
    ]
    if next_run and not paused:
        lines.append(f"Следующая проверка: {next_run:%d.%m.%Y %H:%M}")
    return "\n".join(lines)
