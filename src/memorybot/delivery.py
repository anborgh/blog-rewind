"""Доставка воспоминания владельцу."""

from __future__ import annotations

import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramAPIError

from .models import Memory
from .texts import memory_fallback, memory_header

log = logging.getLogger(__name__)


async def send_memory(bot: Bot, chat_id: int, memory: Memory) -> str:
    """Отправляет воспоминание и возвращает использованный способ доставки.

    Сначала пробуем переслать оригинал — так сохраняется подпись канала и дата.
    Если пост недоступен (например, поднят из экспорта, а бота в канале нет),
    отправляем копию, а в крайнем случае — текст со ссылкой.
    """
    await bot.send_message(chat_id, memory_header(memory), parse_mode=ParseMode.HTML)

    for strategy, action in (("forward", _forward), ("copy", _copy)):
        try:
            await action(bot, chat_id, memory)
        except TelegramAPIError as exc:
            log.info("Не удалось доставить способом %s: %s", strategy, exc)
            continue
        return strategy

    await bot.send_message(
        chat_id,
        memory_fallback(memory),
        parse_mode=ParseMode.HTML,
        link_preview_options=None,
    )
    return "fallback"


async def _forward(bot: Bot, chat_id: int, memory: Memory) -> None:
    source = memory.head.chat_id
    if memory.is_album:
        await bot.forward_messages(
            chat_id=chat_id, from_chat_id=source, message_ids=memory.message_ids
        )
    else:
        await bot.forward_message(
            chat_id=chat_id, from_chat_id=source, message_id=memory.head.message_id
        )


async def _copy(bot: Bot, chat_id: int, memory: Memory) -> None:
    source = memory.head.chat_id
    if memory.is_album:
        await bot.copy_messages(
            chat_id=chat_id, from_chat_id=source, message_ids=memory.message_ids
        )
    else:
        await bot.copy_message(
            chat_id=chat_id, from_chat_id=source, message_id=memory.head.message_id
        )
