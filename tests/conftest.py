from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from memorybot.models import Post
from memorybot.storage import Storage

MOSCOW = ZoneInfo("Europe/Moscow")


def make_post(
    *,
    year: int,
    month: int = 9,
    day: int = 2,
    hour: int = 12,
    message_id: int = 1,
    chat_id: int = -1001,
    text: str = "запись",
    media_kind: str | None = None,
    group_id: str | None = None,
    tz: ZoneInfo = MOSCOW,
) -> Post:
    local = datetime(year, month, day, hour, tzinfo=tz)
    return Post(
        chat_id=chat_id,
        message_id=message_id,
        posted_at=local.astimezone(UTC),
        local_date=local.date(),
        text=text,
        media_kind=media_kind,
        chat_title="Блог",
        group_id=group_id,
        source="test",
    )


@pytest.fixture
def storage(tmp_path):
    db = Storage(tmp_path / "test.sqlite3")
    yield db
    db.close()
