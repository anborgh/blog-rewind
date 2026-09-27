from __future__ import annotations

import random
from datetime import date

from conftest import make_post

from memorybot.anniversaries import anniversary_day_keys, build_memories, choose_memory


def test_only_whole_years_back_are_memories():
    today = date(2026, 9, 2)
    posts = [
        make_post(year=2026, message_id=1, text="сегодняшняя"),
        make_post(year=2025, message_id=2, text="год назад"),
        make_post(year=2019, message_id=3, text="семь лет назад"),
    ]

    memories = build_memories(posts, today, min_years_ago=1)

    assert [memory.years_ago for memory in memories] == [1, 7]
    assert {memory.head.text for memory in memories} == {"год назад", "семь лет назад"}


def test_min_years_ago_can_be_raised():
    today = date(2026, 9, 2)
    posts = [make_post(year=2025, message_id=1), make_post(year=2023, message_id=2)]

    memories = build_memories(posts, today, min_years_ago=3)

    assert [memory.years_ago for memory in memories] == [3]


def test_album_counts_as_one_memory():
    today = date(2026, 9, 2)
    posts = [
        make_post(year=2024, message_id=10, group_id="album:10", text="подпись"),
        make_post(year=2024, message_id=11, group_id="album:10", text=""),
        make_post(year=2024, message_id=12, group_id="album:10", text=""),
        make_post(year=2024, message_id=20, text="отдельная запись"),
    ]

    memories = build_memories(posts, today, min_years_ago=1)

    assert len(memories) == 2
    album = memories[0]
    assert album.is_album
    assert album.message_ids == [10, 11, 12]
    assert all(memory.siblings == 2 for memory in memories)


def test_nothing_written_means_nothing_to_send():
    assert build_memories([], date(2026, 9, 2)) == []
    assert choose_memory([]) is None


def test_february_29_is_remembered_on_february_28_in_common_years():
    assert anniversary_day_keys(date(2027, 2, 28)) == [(2, 28), (2, 29)]
    assert anniversary_day_keys(date(2028, 2, 28)) == [(2, 28)]
    assert anniversary_day_keys(date(2028, 2, 29)) == [(2, 29)]


def test_several_entries_in_one_day_are_picked_at_random():
    today = date(2026, 9, 2)
    posts = [make_post(year=2024, message_id=index) for index in range(1, 5)]
    memories = build_memories(posts, today, min_years_ago=1)

    picked = {choose_memory(memories, random.Random(seed)).head.message_id for seed in range(50)}

    assert picked == {1, 2, 3, 4}


def test_already_sent_memories_wait_their_turn():
    today = date(2026, 9, 2)
    posts = [make_post(year=2024, message_id=1), make_post(year=2023, message_id=2)]
    counts = {(-1001, 1): 3}

    memories = build_memories(posts, today, min_years_ago=1, delivery_counts=counts)
    picked = {choose_memory(memories, random.Random(seed)).head.message_id for seed in range(20)}

    assert picked == {2}


def test_album_seen_count_uses_the_whole_group():
    today = date(2026, 9, 2)
    posts = [
        make_post(year=2024, message_id=10, group_id="album:10"),
        make_post(year=2024, message_id=11, group_id="album:10"),
    ]

    memories = build_memories(posts, today, delivery_counts={(-1001, 11): 2})

    assert memories[0].seen_before == 2
