"""Точка входа: запуск бота и обслуживание архива из терминала."""

from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import date, datetime

from .anniversaries import build_memories, choose_memory
from .config import ConfigError, load_settings
from .importer import ExportParseError, import_export_file
from .storage import Storage
from .texts import human_date, years_ago_phrase


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memorybot",
        description="Telegram-бот с напоминаниями о записях блога годовой давности",
    )
    parser.add_argument("-v", "--verbose", action="store_true", help="подробные логи")
    sub = parser.add_subparsers(dest="command")

    sub.add_parser("run", help="запустить бота (по умолчанию)")

    importer = sub.add_parser("import", help="загрузить архив из экспорта Telegram Desktop")
    importer.add_argument("path", help="путь к result.json")
    importer.add_argument(
        "--chat-id",
        type=int,
        default=None,
        help="id канала в формате Bot API, например -1001234567890",
    )

    sub.add_parser("stats", help="показать содержимое архива")

    check = sub.add_parser("check", help="показать, что бот прислал бы в указанный день")
    check.add_argument("--date", dest="day", default=None, help="дата в формате ГГГГ-ММ-ДД")

    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
        datefmt="%H:%M:%S",
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    command = args.command or "run"
    try:
        settings = load_settings(require_token=command == "run")
    except ConfigError as exc:
        print(f"Ошибка настроек: {exc}", file=sys.stderr)
        return 2

    if command == "import":
        return cmd_import(settings, args)
    if command == "stats":
        return cmd_stats(settings)
    if command == "check":
        return cmd_check(settings, args)
    return cmd_run(settings)


def cmd_run(settings) -> int:
    from .app import StartupAborted, run_bot

    try:
        asyncio.run(run_bot(settings))
    except KeyboardInterrupt:
        pass
    except StartupAborted as exc:
        print(f"Не удалось запуститься: {exc}", file=sys.stderr)
        return exc.code
    return 0


def cmd_import(settings, args) -> int:
    storage = Storage(settings.db_path)
    try:
        result = import_export_file(storage, args.path, settings.timezone, args.chat_id)
    except FileNotFoundError:
        print(f"Файл не найден: {args.path}", file=sys.stderr)
        return 1
    except ExportParseError as exc:
        print(f"Не получилось разобрать экспорт: {exc}", file=sys.stderr)
        return 1

    print(f"Разобрано записей: {result.parsed}")
    print(f"Новых: {result.added}, обновлено: {result.updated}")
    for chat_id, title in result.chats.items():
        print(f"  {title} ({chat_id})")
    storage.close()
    return 0


def cmd_stats(settings) -> int:
    storage = Storage(settings.db_path)
    stats = storage.stats()
    if not stats["total"]:
        print("Архив пуст. Загрузите экспорт: memorybot import result.json")
        storage.close()
        return 0
    tz = settings.timezone
    print(f"Записей: {stats['total']}")
    print(
        f"Период: {stats['first_at'].astimezone(tz):%d.%m.%Y} — "
        f"{stats['last_at'].astimezone(tz):%d.%m.%Y}"
    )
    print(f"Дней с напоминаниями: {stats['deliveries']}")
    print("По годам:")
    for year, count in sorted(stats["per_year"].items()):
        print(f"  {year}: {count}")
    print("Источники:")
    for chat in stats["chats"]:
        print(f"  {chat['title'] or chat['chat_id']} ({chat['chat_id']}): {chat['count']}")
    storage.close()
    return 0


def cmd_check(settings, args) -> int:
    from .anniversaries import anniversary_day_keys

    day = date.fromisoformat(args.day) if args.day else datetime.now(tz=settings.timezone).date()
    storage = Storage(settings.db_path)
    posts = storage.posts_on(anniversary_day_keys(day), max_year=day.year - settings.min_years_ago)
    counts = storage.delivery_counts([post.key for post in posts])
    memories = build_memories(
        posts, day, min_years_ago=settings.min_years_ago, delivery_counts=counts
    )
    if not memories:
        print(f"{human_date(day)}: в прошлые годы записей нет — бот промолчит.")
        storage.close()
        return 0

    print(f"{human_date(day)}: найдено воспоминаний — {len(memories)}")
    for memory in memories:
        mark = "  " if memory.seen_before else "* "
        print(
            f"{mark}{human_date(memory.head.local_date)} "
            f"({years_ago_phrase(memory.years_ago)}): {memory.head.preview}"
        )
    chosen = choose_memory(memories)
    print(f"\nБот пришлёт: {human_date(chosen.head.local_date)} — {chosen.head.preview}")
    storage.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
