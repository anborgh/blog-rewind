"""Поведение при запуске: неверный токен и недоступный Telegram."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError, TelegramUnauthorizedError

from memorybot.__main__ import main
from memorybot.app import StartupAborted, greet


class StubBot:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error

    async def get_me(self):
        if self.error:
            raise self.error
        return SimpleNamespace(username="memorybot")


async def test_greeting_succeeds_with_a_working_token():
    await greet(StubBot())


async def test_wrong_token_stops_the_service_for_good():
    error = TelegramUnauthorizedError(method=SimpleNamespace(), message="Unauthorized")

    with pytest.raises(StartupAborted) as raised:
        await greet(StubBot(error))

    # Код 2 совпадает с RestartPreventExitStatus в юните: перезапуск не поможет.
    assert raised.value.code == 2
    assert "BOT_TOKEN" in str(raised.value)


async def test_network_trouble_asks_for_a_restart():
    error = TelegramNetworkError(method=SimpleNamespace(), message="connection refused")

    with pytest.raises(StartupAborted) as raised:
        await greet(StubBot(error))

    assert raised.value.code == 1


def test_missing_token_exits_with_the_config_code(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("memorybot.config.find_env_file", lambda: None)
    monkeypatch.delenv("BOT_TOKEN", raising=False)

    assert main(["run"]) == 2
