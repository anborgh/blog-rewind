"""Сборка и запуск бота."""

from __future__ import annotations

import contextlib
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError, TelegramNetworkError, TelegramUnauthorizedError
from aiogram.types import BotCommand

from .config import Settings
from .handlers import (
    make_channel_router,
    make_owner_router,
    make_start_router,
    make_stranger_router,
)
from .scheduler import DailyReminder
from .service import STATE_BLOG_CHAT, MemoryService
from .storage import Storage

log = logging.getLogger(__name__)


class StartupAborted(RuntimeError):
    """Запуск невозможен. Код возврата подсказывает systemd, стоит ли перезапускать.

    1 — временная беда (нет сети), перезапуск поможет. 2 — ошибка в настройках,
    перезапускаться бессмысленно: юнит с RestartPreventExitStatus=2 остановится.
    """

    def __init__(self, message: str, code: int) -> None:
        super().__init__(message)
        self.code = code


BOT_COMMANDS = [
    BotCommand(command="today", description="Что было в этот день"),
    BotCommand(command="on", description="Воспоминания за дату"),
    BotCommand(command="random", description="Случайная запись"),
    BotCommand(command="stats", description="Что в архиве"),
    BotCommand(command="settings", description="Настройки"),
    BotCommand(command="pause", description="Выключить напоминания"),
    BotCommand(command="resume", description="Включить напоминания"),
    BotCommand(command="help", description="Помощь"),
]


def build_dispatcher(service: MemoryService) -> Dispatcher:
    dispatcher = Dispatcher()
    dispatcher["service"] = service
    dispatcher.include_router(make_channel_router())
    dispatcher.include_router(make_start_router())
    dispatcher.include_router(make_owner_router())
    dispatcher.include_router(make_stranger_router())
    return dispatcher


async def resolve_blog_chat(bot: Bot, service: MemoryService) -> None:
    """Превращает @username канала в числовой id и запоминает его."""
    configured = service.settings.blog_chat
    if not isinstance(configured, str):
        return
    if service.storage.get_state(STATE_BLOG_CHAT):
        return
    try:
        chat = await bot.get_chat(configured)
    except TelegramAPIError as exc:
        log.warning("Не удалось определить канал %s: %s", configured, exc)
        return
    service.storage.set_state(STATE_BLOG_CHAT, str(chat.id))
    log.info("Канал блога: %s (%s)", chat.title or configured, chat.id)


def build_bot(settings: Settings) -> Bot:
    session = None
    if settings.api_base:
        # Свой сервер Bot API снимает лимит в 20 МБ на скачивание — с ним можно
        # присылать боту большие result.json прямо в чат.
        session = AiohttpSession(api=TelegramAPIServer.from_base(settings.api_base))
    return Bot(
        settings.bot_token,
        session=session,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )


async def greet(bot: Bot) -> None:
    """Проверяет токен и связь до старта polling, чтобы ошибка была одной строкой."""
    try:
        me = await bot.get_me()
    except TelegramUnauthorizedError as exc:
        raise StartupAborted(
            "Telegram не принял токен. Проверьте BOT_TOKEN в .env — "
            "получить новый можно у @BotFather.",
            code=2,
        ) from exc
    except TelegramNetworkError as exc:
        raise StartupAborted(f"Telegram недоступен: {exc}", code=1) from exc
    log.info("Запускаюсь как @%s", me.username)


async def run_bot(settings: Settings) -> None:
    storage = Storage(settings.db_path)
    bot = build_bot(settings)
    service = MemoryService(bot, storage, settings)
    reminder = DailyReminder(service)
    service.next_run_provider = reminder.next_run
    dispatcher = build_dispatcher(service)

    try:
        await greet(bot)
        with contextlib.suppress(TelegramAPIError):
            await bot.set_my_commands(BOT_COMMANDS)
        await resolve_blog_chat(bot, service)
        if service.owner_id is None:
            log.warning("Владелец не задан: напишите боту /start, чтобы закрепить его за собой")
        reminder.start()
        await dispatcher.start_polling(bot, handle_signals=True)
    finally:
        await reminder.stop()
        await bot.session.close()
        storage.close()
        log.info("Остановлен")
