from __future__ import annotations

import re

from .repository.base import EXPENSE, INCOME, Category

DEFAULT_EXPENSES = [
    ("Продукты", "🛒"),
    ("Кафе и доставка", "🍔"),
    ("Транспорт", "🚕"),
    ("Жильё", "🏠"),
    ("Связь и интернет", "📶"),
    ("Здоровье", "💊"),
    ("Одежда", "👕"),
    ("Развлечения", "🎬"),
    ("Подписки", "🔁"),
    ("Подарки", "🎁"),
    ("Дом и быт", "🧻"),
    ("Прочее", "❓"),
]

DEFAULT_INCOMES = [
    ("Зарплата", "💰"),
    ("Фриланс", "💻"),
    ("Возвраты", "↩️"),
    ("Подарки", "🎁"),
    ("Прочее", "❓"),
]

MAX_CATEGORIES_PER_TYPE = 40

_SPREADSHEET_ID_RE = re.compile(r"/spreadsheets/d/([a-zA-Z0-9-_]+)")
_BARE_ID_RE = re.compile(r"^[a-zA-Z0-9-_]{30,}$")


def default_categories() -> list[Category]:
    result: list[Category] = []
    for index, (name, emoji) in enumerate(DEFAULT_EXPENSES):
        result.append(Category(name=name, type=EXPENSE, emoji=emoji, order=index))
    for index, (name, emoji) in enumerate(DEFAULT_INCOMES):
        result.append(Category(name=name, type=INCOME, emoji=emoji, order=index))
    return result


def parse_spreadsheet_id(text: str) -> str | None:
    text = text.strip()
    match = _SPREADSHEET_ID_RE.search(text)
    if match:
        return match.group(1)
    if _BARE_ID_RE.match(text):
        return text
    return None


def parse_category_list(text: str, type_: str) -> list[Category]:
    """Принимает список через запятую или с новой строки."""
    raw_parts = re.split(r"[,\n;]+", text)
    seen: set[str] = set()
    result: list[Category] = []
    for part in raw_parts:
        name = " ".join(part.split())
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        result.append(Category(name=name[:40], type=type_, order=len(result)))
        if len(result) >= MAX_CATEGORIES_PER_TYPE:
            break
    return result


def spreadsheet_url(spreadsheet_id: str) -> str:
    return f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit"
