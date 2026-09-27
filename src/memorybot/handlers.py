"""Команды бота и индексация входящих сообщений."""

from __future__ import annotations

import contextlib
import json
import logging
import re
from datetime import date

from aiogram import Bot, F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.filters import BaseFilter, Command, CommandObject, CommandStart
from aiogram.types import ChatMemberUpdated, Message

from . import texts
from .config import ConfigError
from .importer import ExportParseError, parse_export
from .indexer import post_from_channel_message, post_from_forward
from .models import Memory, summarize
from .service import MemoryService
from .storage import run

log = logging.getLogger(__name__)

MAX_LISTED = 10


class IsOwner(BaseFilter):
    """Бот личный: отвечает только владельцу."""

    async def __call__(self, message: Message, service: MemoryService) -> bool:
        user = message.from_user
        return user is not None and user.id == service.owner_id


# --- индексация постов блога -------------------------------------------------


async def index_channel_post(message: Message, service: MemoryService) -> None:
    await _index(message, service)


async def index_group_message(message: Message, service: MemoryService) -> None:
    """Блог может быть не каналом, а группой — там записи приходят обычными сообщениями.

    Если канал блога задан явно, берём из него всё. Если нет — только сообщения
    владельца, чтобы не утащить в архив чужую переписку.
    """
    if service.blog_chat_id is None:
        author = message.from_user
        if author is None or author.id != service.owner_id:
            return
    await _index(message, service)


async def _index(message: Message, service: MemoryService) -> None:
    if not service.accepts_chat(message.chat.id):
        return
    post = post_from_channel_message(message, service.timezone)
    if not post.text and post.media_kind is None:
        return
    is_new = await run(service.storage.upsert_post, post)
    log.info(
        "%s запись %s из «%s»",
        "Записал" if is_new else "Обновил",
        post.message_id,
        post.chat_title or post.chat_id,
    )


async def on_added_to_chat(event: ChatMemberUpdated, service: MemoryService, bot: Bot) -> None:
    """Подсказывает владельцу, что теперь видно боту, а чего ещё не хватает."""
    owner = service.owner_id
    if owner is None or event.new_chat_member.status in {"left", "kicked"}:
        return

    chat = event.chat
    title = texts.escape(chat.title or str(chat.id))
    is_admin = event.new_chat_member.status in {"administrator", "creator"}
    lines = [
        f"Меня добавили в «{title}».",
        f"id чата: <code>{chat.id}</code>",
    ]

    if not service.accepts_chat(chat.id):
        lines.append(
            "Но записи отсюда я не беру: в настройках указан другой канал "
            f"(<code>{service.blog_chat_id}</code>)."
        )
    elif chat.type == "channel" and not is_admin:
        lines.append("Чтобы видеть посты канала, мне нужны права администратора.")
    elif chat.type != "channel" and not is_admin:
        lines.append(
            "В группе я вижу сообщения только как администратор — иначе Telegram "
            "их мне не показывает."
        )
    else:
        lines.append("Новые записи отсюда буду запоминать.")
        lines.append(
            "Прошлые годы Bot API читать не умеет — пришлите <code>result.json</code> "
            "из экспорта Telegram Desktop, и архив поднимется целиком."
        )

    with contextlib.suppress(TelegramAPIError):
        await bot.send_message(owner, "\n".join(lines), parse_mode=ParseMode.HTML)


# --- знакомство -------------------------------------------------------------


async def cmd_start(message: Message, service: MemoryService) -> None:
    user = message.from_user
    if user is None:
        return
    if not await run(service.claim_owner, user.id):
        await message.answer(texts.NOT_OWNER)
        return
    await message.answer(texts.START, parse_mode=ParseMode.HTML)


# --- команды владельца ------------------------------------------------------


async def cmd_help(message: Message) -> None:
    await message.answer(texts.HELP, parse_mode=ParseMode.HTML)


async def cmd_today(message: Message, service: MemoryService) -> None:
    outcome = await service.deliver(force=True)
    if not outcome.delivered:
        await message.answer(texts.NOTHING_TODAY)


async def cmd_on(message: Message, command: CommandObject, service: MemoryService) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            "Укажите дату: <code>/on 5.09</code> или <code>/on 2019-09-05</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        day, exact = parse_day(raw, service.today())
    except ValueError:
        await message.answer(
            "Не разобрал дату. Форматы: <code>5.09</code>, <code>05.09.2019</code>, "
            "<code>2019-09-05</code>",
            parse_mode=ParseMode.HTML,
        )
        return

    memories = await run(service.exact_day if exact else service.collect, day)
    if not memories:
        label = (
            texts.human_date(day) if exact else f"{day.day} {texts.MONTHS_GENITIVE[day.month - 1]}"
        )
        await message.answer(f"За {label} в архиве ничего нет.")
        return

    await service.send(service.pick(memories))
    if len(memories) > 1:
        await message.answer(list_memories(memories), parse_mode=ParseMode.HTML)


async def cmd_random(message: Message, service: MemoryService) -> None:
    post = await run(service.random_post)
    if post is None:
        await message.answer(texts.EMPTY_ARCHIVE, parse_mode=ParseMode.HTML)
        return
    await service.send(
        Memory(posts=[post], years_ago=max(service.today().year - post.local_date.year, 0))
    )


async def cmd_stats(message: Message, service: MemoryService) -> None:
    stats = await run(service.storage.stats)
    if not stats["total"]:
        await message.answer(texts.EMPTY_ARCHIVE, parse_mode=ParseMode.HTML)
        return
    tz = service.timezone
    lines = [
        "<b>Архив</b>",
        f"Записей: {stats['total']}",
        f"Период: {stats['first_at'].astimezone(tz):%d.%m.%Y} — "
        f"{stats['last_at'].astimezone(tz):%d.%m.%Y}",
        f"Дней с напоминаниями: {stats['deliveries']}",
        "",
        "<b>По годам</b>",
    ]
    lines += [f"{year}: {count}" for year, count in sorted(stats["per_year"].items())]
    if stats["chats"]:
        lines += ["", "<b>Источники</b>"]
        lines += [
            f"{texts.escape(chat['title'] or str(chat['chat_id']))} — {chat['count']}"
            for chat in stats["chats"]
        ]
    await message.answer("\n".join(lines), parse_mode=ParseMode.HTML)


async def cmd_settings(message: Message, service: MemoryService) -> None:
    stats = await run(service.storage.stats)
    await message.answer(
        texts.settings_text(
            timezone_name=service.timezone_name,
            window=service.window,
            paused=service.paused,
            min_years_ago=service.settings.min_years_ago,
            next_run=service.next_run_hint(),
            total=stats["total"],
        ),
        parse_mode=ParseMode.HTML,
    )


async def cmd_window(message: Message, command: CommandObject, service: MemoryService) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            f"Сейчас: <code>{service.window}</code>. "
            "Задать новое: <code>/window 10:00-20:00</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        window = await run(service.set_window, raw)
    except ConfigError as exc:
        await message.answer(str(exc))
        return
    if window.is_fixed:
        await message.answer(f"Буду присылать в {window.start:%H:%M}.")
    else:
        await message.answer(
            f"Буду присылать в случайный момент между {window.start:%H:%M} и {window.end:%H:%M}."
        )


async def cmd_timezone(message: Message, command: CommandObject, service: MemoryService) -> None:
    raw = (command.args or "").strip()
    if not raw:
        await message.answer(
            f"Сейчас: <code>{texts.escape(service.timezone_name)}</code>. "
            "Задать новый: <code>/timezone Europe/Berlin</code>",
            parse_mode=ParseMode.HTML,
        )
        return
    try:
        updated = await run(service.set_timezone, raw)
    except ConfigError as exc:
        await message.answer(str(exc))
        return
    await message.answer(
        f"Часовой пояс: <code>{texts.escape(raw)}</code>. Пересчитал даты у {updated} записей.",
        parse_mode=ParseMode.HTML,
    )


async def cmd_pause(message: Message, service: MemoryService) -> None:
    await run(service.set_paused, True)
    await message.answer(texts.PAUSED)


async def cmd_resume(message: Message, service: MemoryService) -> None:
    await run(service.set_paused, False)
    await message.answer(texts.RESUMED)


# --- пополнение архива ------------------------------------------------------


async def handle_export_file(message: Message, service: MemoryService, bot: Bot) -> None:
    document = message.document
    if not (document.file_name or "").lower().endswith(".json"):
        await message.answer(
            "Жду <code>result.json</code> из экспорта Telegram Desktop.",
            parse_mode=ParseMode.HTML,
        )
        return

    await message.answer("Разбираю экспорт…")
    try:
        buffer = await bot.download(document)
        data = json.loads(buffer.read().decode("utf-8"))
        posts = parse_export(data, service.timezone, service.blog_chat_id)
    except ExportParseError as exc:
        await message.answer(str(exc))
        return
    except (json.JSONDecodeError, UnicodeDecodeError):
        await message.answer("Не смог прочитать файл как JSON.")
        return
    except Exception as exc:  # среди прочего — лимит Bot API в 20 МБ на скачивание
        log.exception("Импорт не удался")
        await message.answer(
            f"Импорт не удался: {texts.escape(str(exc))}\n"
            "Если файл больше 20 МБ, загрузите его на сервере командой "
            "<code>memorybot import result.json</code>.",
            parse_mode=ParseMode.HTML,
        )
        return

    if not posts:
        await message.answer("В экспорте не нашлось ни одной записи.")
        return
    added = await run(service.storage.upsert_posts, posts)
    first = min(post.local_date for post in posts)
    last = max(post.local_date for post in posts)
    await message.answer(
        f"Готово: разобрано {len(posts)}, новых {added}.\n"
        f"Период: {first:%d.%m.%Y} — {last:%d.%m.%Y}."
    )


async def handle_forwarded(message: Message, service: MemoryService) -> None:
    post = post_from_forward(message, service.timezone)
    if post is None:
        await message.answer("Не вижу исходную дату у этого сообщения.")
        return
    is_new = await run(service.storage.upsert_post, post)
    await message.answer(
        ("Записал" if is_new else "Обновил")
        + f" запись от {texts.human_date(post.local_date)}: {texts.escape(post.preview)}",
        parse_mode=ParseMode.HTML,
    )


async def fallback(message: Message) -> None:
    await message.answer(texts.HELP, parse_mode=ParseMode.HTML)


# --- посторонние ------------------------------------------------------------


async def deny(message: Message) -> None:
    await message.answer(texts.NOT_OWNER)


# --- сборка роутеров ---------------------------------------------------------


def make_channel_router() -> Router:
    """Индексация блога: канал, где бот администратор, или группа с заметками."""
    router = Router(name="channel")
    router.channel_post.register(index_channel_post)
    router.edited_channel_post.register(index_channel_post)
    group_chats = F.chat.type.in_({ChatType.GROUP, ChatType.SUPERGROUP})
    router.message.register(index_group_message, group_chats)
    router.edited_message.register(index_group_message, group_chats)
    router.my_chat_member.register(on_added_to_chat)
    return router


def make_start_router() -> Router:
    """/start обрабатывается до проверки владельца: им владелец и назначается."""
    router = Router(name="start")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(cmd_start, CommandStart())
    return router


def make_owner_router() -> Router:
    router = Router(name="owner")
    router.message.filter(F.chat.type == ChatType.PRIVATE, IsOwner())
    for handler, command in (
        (cmd_help, "help"),
        (cmd_today, "today"),
        (cmd_on, "on"),
        (cmd_random, "random"),
        (cmd_stats, "stats"),
        (cmd_settings, "settings"),
        (cmd_window, "window"),
        (cmd_timezone, "timezone"),
        (cmd_pause, "pause"),
        (cmd_resume, "resume"),
    ):
        router.message.register(handler, Command(command))
    router.message.register(handle_export_file, F.document)
    router.message.register(handle_forwarded, F.forward_origin)
    router.message.register(fallback)
    return router


def make_stranger_router() -> Router:
    """Последний рубеж: всем остальным вежливо отказываем."""
    router = Router(name="stranger")
    router.message.filter(F.chat.type == ChatType.PRIVATE)
    router.message.register(deny)
    return router


# --- вспомогательное --------------------------------------------------------

DATE_PATTERNS = (
    (re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})$"), ("year", "month", "day")),
    (re.compile(r"^(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})$"), ("day", "month", "year")),
    (re.compile(r"^(\d{1,2})[.\-/](\d{1,2})$"), ("day", "month")),
)


def parse_day(raw: str, today: date) -> tuple[date, bool]:
    """Разбирает дату из команды. Второе значение — указан ли год явно."""
    raw = raw.strip()
    for pattern, order in DATE_PATTERNS:
        match = pattern.match(raw)
        if not match:
            continue
        values = dict(zip(order, (int(part) for part in match.groups()), strict=True))
        year = values.get("year", today.year)
        try:
            return date(year, values["month"], values["day"]), "year" in values
        except ValueError as exc:
            raise ValueError(str(exc)) from exc
    raise ValueError(f"Неизвестный формат даты: {raw!r}")


def list_memories(memories: list[Memory]) -> str:
    lines = [f"<b>Всё, что было в этот день ({len(memories)}):</b>"]
    for memory in memories[:MAX_LISTED]:
        head = memory.head
        preview = texts.escape(summarize(head.text, head.media_kind, limit=80))
        stamp = f"{head.local_date:%d.%m.%Y}"
        link = head.link
        lines.append(
            f'• <a href="{link}">{stamp}</a> — {preview}' if link else f"• {stamp} — {preview}"
        )
    if len(memories) > MAX_LISTED:
        lines.append(f"…и ещё {len(memories) - MAX_LISTED}")
    return "\n".join(lines)
