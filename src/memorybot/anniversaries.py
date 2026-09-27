"""Поиск записей, опубликованных ровно год / два / три и более лет назад."""

from __future__ import annotations

import calendar
import random
from collections.abc import Sequence
from datetime import date

from .models import Memory, Post


def anniversary_day_keys(today: date) -> list[tuple[int, int]]:
    """Пары (месяц, день), которые считаются «этим же днём» в прошлые годы.

    В невисокосный год 29 февраля отмечается 28-го: иначе такие записи
    выпадали бы из ленты на три года из четырёх.
    """
    keys = [(today.month, today.day)]
    if today.month == 2 and today.day == 28 and not calendar.isleap(today.year):
        keys.append((2, 29))
    return keys


def build_memories(
    posts: Sequence[Post],
    today: date,
    *,
    min_years_ago: int = 1,
    delivery_counts: dict[tuple[int, int], int] | None = None,
) -> list[Memory]:
    """Собирает записи в воспоминания: альбом из нескольких сообщений — одно воспоминание."""
    counts = delivery_counts or {}
    groups: dict[tuple[int, str | int], list[Post]] = {}
    order: list[tuple[int, str | int]] = []
    for post in posts:
        years_ago = today.year - post.local_date.year
        if years_ago < min_years_ago:
            continue
        bucket = (post.chat_id, post.group_id or f"single:{post.message_id}")
        if bucket not in groups:
            groups[bucket] = []
            order.append(bucket)
        groups[bucket].append(post)

    memories: list[Memory] = []
    for bucket in order:
        chunk = sorted(groups[bucket], key=lambda p: p.message_id)
        head = chunk[0]
        memories.append(
            Memory(
                posts=chunk,
                years_ago=today.year - head.local_date.year,
                seen_before=max(counts.get(post.key, 0) for post in chunk),
            )
        )

    for memory in memories:
        memory.siblings = len(memories)
    return memories


def choose_memory(memories: Sequence[Memory], rng: random.Random | None = None) -> Memory | None:
    """Случайное воспоминание дня.

    Если записей за этот день несколько, выбирается случайная — но сначала из тех,
    которые бот ещё ни разу не присылал, чтобы за годы не крутились одни и те же.
    """
    if not memories:
        return None
    rng = rng or random.Random()
    fewest_seen = min(memory.seen_before for memory in memories)
    pool = [memory for memory in memories if memory.seen_before == fewest_seen]
    return rng.choice(pool)


def memories_for(
    posts: Sequence[Post],
    today: date,
    *,
    min_years_ago: int = 1,
    delivery_counts: dict[tuple[int, int], int] | None = None,
    rng: random.Random | None = None,
) -> tuple[Memory | None, list[Memory]]:
    """Удобная обёртка: возвращает выбранное воспоминание и все кандидаты дня."""
    memories = build_memories(
        posts, today, min_years_ago=min_years_ago, delivery_counts=delivery_counts
    )
    return choose_memory(memories, rng), memories
