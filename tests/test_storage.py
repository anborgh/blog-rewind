from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo

from conftest import make_post


def test_upsert_is_idempotent(storage):
    post = make_post(year=2024, message_id=7)

    assert storage.upsert_post(post) is True
    assert storage.upsert_post(post) is False
    assert storage.stats()["total"] == 1


def test_upsert_updates_edited_text_but_keeps_the_row(storage):
    storage.upsert_post(make_post(year=2024, message_id=7, text="было"))
    storage.upsert_post(make_post(year=2024, message_id=7, text="стало"))

    posts = storage.posts_on([(9, 2)], max_year=2025)

    assert [post.text for post in posts] == ["стало"]


def test_posts_on_respects_day_and_year_bounds(storage):
    storage.upsert_posts(
        [
            make_post(year=2024, month=9, day=2, message_id=1),
            make_post(year=2023, month=9, day=2, message_id=2),
            make_post(year=2026, month=9, day=2, message_id=3),
            make_post(year=2024, month=9, day=3, message_id=4),
        ]
    )

    found = storage.posts_on([(9, 2)], max_year=2025)

    assert sorted(post.message_id for post in found) == [1, 2]


def test_posts_on_can_be_limited_to_one_chat(storage):
    storage.upsert_posts(
        [
            make_post(year=2024, message_id=1, chat_id=-1001),
            make_post(year=2024, message_id=1, chat_id=-1002),
        ]
    )

    assert len(storage.posts_on([(9, 2)], max_year=2025)) == 2
    assert len(storage.posts_on([(9, 2)], max_year=2025, chat_id=-1002)) == 1


def test_posts_on_date_returns_exact_day(storage):
    storage.upsert_posts(
        [
            make_post(year=2019, month=9, day=5, message_id=1),
            make_post(year=2020, month=9, day=5, message_id=2),
        ]
    )

    found = storage.posts_on_date(date(2019, 9, 5))

    assert [post.message_id for post in found] == [1]


def test_delivery_counts_track_repeats(storage):
    storage.record_delivery(date(2026, 9, 2), -1001, [5])
    storage.record_delivery(date(2027, 9, 2), -1001, [5])
    storage.record_delivery(date(2026, 9, 3), -1001, [6])

    counts = storage.delivery_counts([(-1001, 5), (-1001, 6), (-1001, 7)])

    assert counts == {(-1001, 5): 2, (-1001, 6): 1}
    assert storage.delivered_on(date(2026, 9, 2)) is True
    assert storage.delivered_on(date(2026, 9, 4)) is False


def test_changing_timezone_recomputes_local_dates(storage):
    # 01:30 по Москве — это ещё предыдущий день в Лондоне.
    storage.upsert_post(make_post(year=2024, month=9, day=2, hour=1, message_id=1))
    assert storage.posts_on([(9, 2)], max_year=2025)

    storage.recompute_local_dates(ZoneInfo("Europe/London"))

    assert storage.posts_on([(9, 2)], max_year=2025) == []
    assert len(storage.posts_on([(9, 1)], max_year=2025)) == 1


def test_state_roundtrip(storage):
    assert storage.get_state("window") is None
    storage.set_state("window", "10:00-20:00")
    assert storage.get_state("window") == "10:00-20:00"
    storage.set_state("window", None)
    assert storage.get_state("window") is None


def test_stats_summarises_the_archive(storage):
    storage.upsert_posts(
        [
            make_post(year=2023, message_id=1),
            make_post(year=2024, message_id=2),
            make_post(year=2024, message_id=3),
        ]
    )

    stats = storage.stats()

    assert stats["total"] == 3
    assert stats["per_year"] == {2023: 1, 2024: 2}
    assert stats["chats"][0]["count"] == 3


def test_bulk_upsert_handles_more_rows_than_sqlite_parameter_limit(storage):
    posts = [make_post(year=2024, message_id=index) for index in range(1500)]

    assert storage.upsert_posts(posts) == 1500
    assert storage.upsert_posts(posts) == 0
