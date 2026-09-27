"""Загрузка настроек из окружения."""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from dotenv import find_dotenv, load_dotenv

DEFAULT_TIMEZONE = "Europe/Moscow"
DEFAULT_WINDOW = "10:00-20:00"
DEFAULT_DB_PATH = "data/memorybot.sqlite3"


class ConfigError(RuntimeError):
    pass


@dataclass(frozen=True)
class TimeWindow:
    """Промежуток суток, внутри которого бот выбирает момент для напоминания."""

    start: time
    end: time

    @property
    def is_fixed(self) -> bool:
        return self.start >= self.end

    def __str__(self) -> str:
        if self.is_fixed:
            return self.start.strftime("%H:%M")
        return f"{self.start:%H:%M}-{self.end:%H:%M}"


def parse_window(raw: str) -> TimeWindow:
    """Разбирает `10:00-20:00` или `10:00` (фиксированное время)."""
    raw = raw.strip()
    if not raw:
        raise ConfigError("Пустое расписание")
    if "-" in raw:
        left, _, right = raw.partition("-")
        return TimeWindow(parse_clock(left), parse_clock(right))
    point = parse_clock(raw)
    return TimeWindow(point, point)


def parse_clock(raw: str) -> time:
    raw = raw.strip()
    parts = raw.split(":")
    if len(parts) != 2:
        raise ConfigError(f"Не похоже на время: {raw!r}. Ожидается ЧЧ:ММ")
    try:
        hour, minute = int(parts[0]), int(parts[1])
    except ValueError as exc:
        raise ConfigError(f"Не похоже на время: {raw!r}. Ожидается ЧЧ:ММ") from exc
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ConfigError(f"Время вне суток: {raw!r}")
    return time(hour=hour, minute=minute)


def parse_timezone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name.strip())
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise ConfigError(f"Неизвестный часовой пояс: {name!r}") from exc


def parse_chat_id(raw: str | None) -> int | str | None:
    """`-1001234567890` -> int, `@myblog` -> str, пусто -> None."""
    if raw is None:
        return None
    raw = raw.strip()
    if not raw:
        return None
    try:
        return int(raw)
    except ValueError:
        return raw if raw.startswith("@") else f"@{raw}"


@dataclass(frozen=True)
class Settings:
    bot_token: str
    db_path: Path
    owner_id: int | None
    blog_chat: int | str | None
    timezone_name: str
    window: TimeWindow
    min_years_ago: int
    api_base: str | None = None

    @property
    def timezone(self) -> ZoneInfo:
        return parse_timezone(self.timezone_name)


def find_env_file() -> Path | None:
    """Ищет .env рядом с текущим каталогом, а затем в корне установленного проекта.

    Второй вариант важен для сервиса: команды вида
    `sudo -u memorybot /opt/memorybot/.venv/bin/memorybot import ...` запускают
    из произвольного каталога, но настройки и архив должны находиться те же.
    """
    found = find_dotenv(usecwd=True)
    if found:
        return Path(found)
    fallback = Path(__file__).resolve().parents[2] / ".env"
    return fallback if fallback.is_file() else None


def load_settings(env: dict[str, str] | None = None, *, require_token: bool = True) -> Settings:
    base_dir: Path | None = None
    if env is None:
        env_file = find_env_file()
        if env_file is not None:
            load_dotenv(env_file)
            base_dir = env_file.parent
        env = dict(os.environ)

    token = (env.get("BOT_TOKEN") or "").strip()
    if not token and require_token:
        raise ConfigError(
            "Не задан BOT_TOKEN. Получите токен у @BotFather и положите его в .env "
            "(см. .env.example)."
        )

    owner_raw = (env.get("OWNER_ID") or "").strip()
    owner_id: int | None = None
    if owner_raw:
        try:
            owner_id = int(owner_raw)
        except ValueError as exc:
            raise ConfigError(f"OWNER_ID должен быть числом, получено {owner_raw!r}") from exc

    timezone_name = (env.get("TIMEZONE") or DEFAULT_TIMEZONE).strip()
    parse_timezone(timezone_name)

    min_years_raw = (env.get("MIN_YEARS_AGO") or "1").strip()
    try:
        min_years = int(min_years_raw)
    except ValueError as exc:
        raise ConfigError(f"MIN_YEARS_AGO должен быть числом, получено {min_years_raw!r}") from exc
    if min_years < 1:
        raise ConfigError("MIN_YEARS_AGO не может быть меньше 1")

    db_path = Path((env.get("DB_PATH") or DEFAULT_DB_PATH).strip())
    if base_dir is not None and not db_path.is_absolute():
        db_path = base_dir / db_path

    return Settings(
        bot_token=token,
        db_path=db_path,
        owner_id=owner_id,
        blog_chat=parse_chat_id(env.get("BLOG_CHAT_ID")),
        timezone_name=timezone_name,
        window=parse_window(env.get("DIGEST_WINDOW") or DEFAULT_WINDOW),
        min_years_ago=min_years,
        api_base=(env.get("TELEGRAM_API_BASE") or "").strip() or None,
    )
