from __future__ import annotations

from datetime import time

import pytest

from memorybot.config import ConfigError, load_settings, parse_chat_id, parse_window


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """load_dotenv не переопределяет уже заданные переменные — убираем их из окружения."""
    for name in ("BOT_TOKEN", "OWNER_ID", "BLOG_CHAT_ID", "TIMEZONE", "DIGEST_WINDOW", "DB_PATH"):
        monkeypatch.delenv(name, raising=False)


def test_window_range():
    window = parse_window("10:00-20:30")

    assert (window.start, window.end) == (time(10, 0), time(20, 30))
    assert window.is_fixed is False
    assert str(window) == "10:00-20:30"


def test_single_value_means_fixed_time():
    window = parse_window("09:05")

    assert window.is_fixed is True
    assert str(window) == "09:05"


@pytest.mark.parametrize("raw", ["", "утром", "25:00", "10-20", "10:60"])
def test_bad_windows_are_rejected(raw):
    with pytest.raises(ConfigError):
        parse_window(raw)


def test_chat_id_accepts_numbers_and_usernames():
    assert parse_chat_id("-1001234567890") == -1001234567890
    assert parse_chat_id("@myblog") == "@myblog"
    assert parse_chat_id("myblog") == "@myblog"
    assert parse_chat_id("  ") is None
    assert parse_chat_id(None) is None


def test_settings_have_workable_defaults():
    settings = load_settings({"BOT_TOKEN": "123:abc"})

    assert settings.timezone_name == "Europe/Moscow"
    assert str(settings.window) == "12:00"
    assert settings.min_years_ago == 1
    assert settings.owner_id is None


def test_missing_token_is_explained():
    with pytest.raises(ConfigError, match="BOT_TOKEN"):
        load_settings({})


def test_relative_db_path_is_anchored_to_the_env_file(tmp_path, monkeypatch):
    project = tmp_path / "opt" / "memorybot"
    project.mkdir(parents=True)
    (project / ".env").write_text("BOT_TOKEN=123:abc\nDB_PATH=data/memorybot.sqlite3\n")
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    monkeypatch.setattr("memorybot.config.find_env_file", lambda: project / ".env")

    settings = load_settings()

    assert settings.db_path == project / "data" / "memorybot.sqlite3"


def test_absolute_db_path_is_left_alone(tmp_path, monkeypatch):
    (tmp_path / ".env").write_text(f"BOT_TOKEN=123:abc\nDB_PATH={tmp_path}/archive.sqlite3\n")
    monkeypatch.setattr("memorybot.config.find_env_file", lambda: tmp_path / ".env")

    settings = load_settings()

    assert settings.db_path == tmp_path / "archive.sqlite3"


def test_unknown_timezone_is_rejected():
    with pytest.raises(ConfigError, match="часовой пояс"):
        load_settings({"BOT_TOKEN": "123:abc", "TIMEZONE": "Mars/Olympus"})
