from __future__ import annotations

from datetime import date

import pytest
from conftest import make_post

from memorybot.models import Memory, summarize
from memorybot.texts import entries_phrase, human_date, memory_fallback, memory_header, years_phrase


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 год"), (2, "2 года"), (5, "5 лет"), (11, "11 лет"), (21, "21 год"), (22, "22 года")],
)
def test_years_are_declined(count, expected):
    assert years_phrase(count) == expected


@pytest.mark.parametrize(
    ("count", "expected"),
    [(1, "1 запись"), (3, "3 записи"), (5, "5 записей"), (11, "11 записей")],
)
def test_entries_are_declined(count, expected):
    assert entries_phrase(count) == expected


def test_human_date_uses_russian_months():
    assert human_date(date(2019, 9, 5)) == "5 сентября 2019"


def test_header_names_the_anniversary():
    memory = Memory(posts=[make_post(year=2023)], years_ago=3)

    assert memory_header(memory) == "🕰 <b>Ровно 3 года назад</b> — 2 сентября 2023"


def test_header_of_a_this_year_post_does_not_claim_an_anniversary():
    memory = Memory(posts=[make_post(year=2026)], years_ago=0)

    header = memory_header(memory)

    assert "назад" not in header
    assert "2 сентября 2026" in header


def test_header_mentions_the_other_entries_of_that_day():
    memory = Memory(posts=[make_post(year=2023)], years_ago=3, siblings=4)

    assert "было ещё 3 записи" in memory_header(memory)


def test_fallback_keeps_the_text_and_adds_a_link():
    post = make_post(year=2023, chat_id=-1001234567890, message_id=17, text="было дело")
    memory = Memory(posts=[post], years_ago=3)

    body = memory_fallback(memory)

    assert "было дело" in body
    assert "https://t.me/c/1234567890/17" in body


def test_fallback_escapes_html_from_the_post():
    post = make_post(year=2023, text="<script>alert(1)</script> & прочее")
    memory = Memory(posts=[post], years_ago=3)

    assert "&lt;script&gt;" in memory_fallback(memory)


def test_public_channel_posts_link_by_username():
    post = make_post(year=2023, message_id=17)
    post = type(post)(**{**post.__dict__, "chat_username": "myblog"})

    assert post.link == "https://t.me/myblog/17"


def test_summary_describes_media_without_text():
    assert summarize("", "photo") == "[фото]"
    assert summarize("", None) == "[без текста]"
    assert summarize("а" * 200, None).endswith("…")
