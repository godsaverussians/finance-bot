from __future__ import annotations

from typing import Sequence

from aiogram.types import (
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .repository.base import Category

BUTTON_EXPENSE = "➖ Трата"
BUTTON_INCOME = "➕ Доход"
BUTTON_LAST = "📋 Последние"
BUTTON_REPORT = "📊 Сводка"


def main_menu() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=BUTTON_EXPENSE), KeyboardButton(text=BUTTON_INCOME)],
            [KeyboardButton(text=BUTTON_LAST), KeyboardButton(text=BUTTON_REPORT)],
        ],
        resize_keyboard=True,
        is_persistent=True,
    )


def categories(items: Sequence[Category]) -> InlineKeyboardMarkup:
    """Индекс в callback_data — имена категорий не влезают в лимит 64 байта."""
    builder = InlineKeyboardBuilder()
    for index, category in enumerate(items):
        builder.button(text=category.label, callback_data=f"cat:{index}")
    builder.adjust(2)
    builder.row(
        *InlineKeyboardBuilder()
        .button(text="✖️ Отмена", callback_data="entry:cancel")
        .buttons
    )
    return builder.as_markup()


def saved_actions(tx_id: str) -> InlineKeyboardMarkup:
    builder = InlineKeyboardBuilder()
    builder.button(text="💬 Комментарий", callback_data=f"note:{tx_id}")
    builder.button(text="🗑 Удалить", callback_data=f"del:{tx_id}")
    builder.adjust(2)
    return builder.as_markup()
