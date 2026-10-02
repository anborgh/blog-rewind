"""Сквозная проверка: настоящий цикл long polling против поддельного Bot API."""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from aiohttp import web
from conftest import make_post

from memorybot.app import run_bot
from memorybot.config import load_settings

TOKEN = "42:TESTTOKENTESTTOKENTESTTOKENTESTTOKEN"
OWNER = 4242
BLOG = -1001234567890


class FakeTelegram:
    """Минимальный сервер Bot API: раздаёт подготовленные апдейты и пишет вызовы."""

    def __init__(self) -> None:
        self.updates: asyncio.Queue[dict] = asyncio.Queue()
        self.calls: list[tuple[str, dict]] = []
        self._next_update_id = 1
        self._runner: web.AppRunner | None = None
        self.base_url = ""

    async def start(self) -> None:
        app = web.Application()
        app.router.add_post("/bot{token}/{method}", self._handle)
        self._runner = web.AppRunner(app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        port = site._server.sockets[0].getsockname()[1]
        self.base_url = f"http://127.0.0.1:{port}"

    async def stop(self) -> None:
        if self._runner:
            await self._runner.cleanup()

    def push(self, message: dict, kind: str = "message") -> None:
        self.updates.put_nowait({"update_id": self._next_update_id, kind: message})
        self._next_update_id += 1

    async def _handle(self, request: web.Request) -> web.Response:
        method = request.match_info["method"]
        payload = dict(await request.post())
        if method != "getUpdates":
            self.calls.append((method, payload))
        return web.json_response({"ok": True, "result": await self._result(method)})

    async def _result(self, method: str):
        if method == "getMe":
            return {"id": 42, "is_bot": True, "first_name": "memorybot", "username": "memorybot"}
        if method == "getUpdates":
            try:
                update = await asyncio.wait_for(self.updates.get(), timeout=0.2)
            except TimeoutError:
                return []
            return [update]
        if method in {"sendMessage", "forwardMessage", "copyMessage"}:
            return {
                "message_id": 1,
                "date": int(time.time()),
                "chat": {"id": OWNER, "type": "private"},
            }
        if method == "forwardMessages":
            return [{"message_id": 1}]
        return True

    def texts(self) -> list[str]:
        return [payload["text"] for method, payload in self.calls if method == "sendMessage"]

    def methods(self) -> list[str]:
        return [method for method, _ in self.calls]

    async def wait_for(self, method: str, count: int = 1, timeout: float = 5.0) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.methods().count(method) >= count:
                return
            await asyncio.sleep(0.05)
        raise AssertionError(f"Не дождались {method}; были вызваны: {self.methods()}")


def private_message(text: str) -> dict:
    return {
        "message_id": 100,
        "date": int(time.time()),
        "chat": {"id": OWNER, "type": "private"},
        "from": {"id": OWNER, "is_bot": False, "first_name": "Автор"},
        "text": text,
    }


def channel_post(text: str, message_id: int) -> dict:
    return {
        "message_id": message_id,
        "date": int(time.time()),
        "chat": {"id": BLOG, "type": "channel", "title": "Личный блог"},
        "text": text,
    }


@pytest.fixture
async def telegram():
    server = FakeTelegram()
    await server.start()
    yield server
    await server.stop()


@pytest.fixture
async def running_bot(telegram, tmp_path):
    # Фиксированное время в прошлом: планировщик не должен вмешиваться во время теста.
    past = (datetime.now(tz=UTC) - timedelta(hours=2)).strftime("%H:%M")
    settings = load_settings(
        {
            "BOT_TOKEN": TOKEN,
            "OWNER_ID": str(OWNER),
            "DB_PATH": str(tmp_path / "bot.sqlite3"),
            "TIMEZONE": "UTC",
            "DIGEST_WINDOW": past,
            "TELEGRAM_API_BASE": telegram.base_url,
        }
    )
    task = asyncio.create_task(run_bot(settings))
    await telegram.wait_for("setMyCommands")
    yield settings
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError, TimeoutError):
        await asyncio.wait_for(task, timeout=5)


async def test_bot_starts_polling_and_answers_start(telegram, running_bot):
    telegram.push(private_message("/start"))

    await telegram.wait_for("sendMessage")

    assert "Привет!" in telegram.texts()[0]


async def test_channel_post_gets_indexed_and_comes_back_a_year_later(
    telegram, running_bot, tmp_path
):
    from memorybot.storage import Storage

    telegram.push(channel_post("Сегодняшняя запись", message_id=7), kind="channel_post")
    telegram.push(private_message("/stats"))
    await telegram.wait_for("sendMessage")

    assert "Записей: 1" in telegram.texts()[-1]

    # Подкладываем запись годовой давности и просим бота проверить день.
    storage = Storage(running_bot.db_path)
    today = datetime.now(tz=UTC).date()
    storage.upsert_post(
        make_post(
            year=today.year - 1,
            month=today.month,
            day=today.day,
            message_id=11,
            chat_id=BLOG,
            text="то, что было год назад",
            tz=UTC,
        )
    )
    storage.close()

    telegram.push(private_message("/today"))
    await telegram.wait_for("forwardMessage")

    # Дайджест без комментариев бота: после /stats новых текстовых сообщений нет.
    assert "Записей: 1" in telegram.texts()[-1]
    forwarded = next(payload for method, payload in telegram.calls if method == "forwardMessage")
    assert json.loads(forwarded["from_chat_id"]) == BLOG
    assert json.loads(forwarded["message_id"]) == 11


async def test_quiet_day_produces_no_forward(telegram, running_bot):
    telegram.push(private_message("/today"))

    await telegram.wait_for("sendMessage")
    await asyncio.sleep(0.3)

    assert "промолчу" in telegram.texts()[-1]
    assert "forwardMessage" not in telegram.methods()
