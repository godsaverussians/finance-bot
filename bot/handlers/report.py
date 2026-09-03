from __future__ import annotations

import html
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, CommandObject
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from .. import keyboards as kb
from ..config import Config
from ..money import format_amount
from ..registry import Registry, UserContext
from ..repository.base import EXPENSE, INCOME, Repository, Transaction

logger = logging.getLogger(__name__)
router = Router(name="report")

MONTH_GENITIVE = [
    "января",
    "февраля",
    "марта",
    "апреля",
    "мая",
    "июня",
    "июля",
    "августа",
    "сентября",
    "октября",
    "ноября",
    "декабря",
]

MONTH_SHORT = [
    "янв",
    "фев",
    "мар",
    "апр",
    "мая",
    "июн",
    "июл",
    "авг",
    "сен",
    "окт",
    "ноя",
    "дек",
]

MONTH_NAMES = [
    "Январь",
    "Февраль",
    "Март",
    "Апрель",
    "Май",
    "Июнь",
    "Июль",
    "Август",
    "Сентябрь",
    "Октябрь",
    "Ноябрь",
    "Декабрь",
]

TOP_CATEGORIES = 12
SCOPE_ALL = "all"
SCOPE_MINE = "mine"


def shift_month(year: int, month: int, delta: int) -> tuple[int, int]:
    index = year * 12 + (month - 1) + delta
    return divmod(index, 12)[0], divmod(index, 12)[1] + 1


def period_bounds(year: int, month: int, start_day: int) -> tuple[date, date]:
    """Период, начинающийся start_day числа указанного месяца."""
    first = date(year, month, start_day)
    next_year, next_month = shift_month(year, month, 1)
    following = date(next_year, next_month, start_day)
    return first, date.fromordinal(following.toordinal() - 1)


def current_anchor(today: date, start_day: int) -> tuple[int, int]:
    """Месяц, к которому относится период, содержащий сегодняшний день."""
    if today.day >= start_day:
        return today.year, today.month
    return shift_month(today.year, today.month, -1)


def period_title(year: int, month: int, start_day: int) -> str:
    if start_day == 1:
        return f"{MONTH_NAMES[month - 1]} {year}"
    first, last = period_bounds(year, month, start_day)
    return (
        f"{first.day} {MONTH_GENITIVE[first.month - 1]} — "
        f"{last.day} {MONTH_GENITIVE[last.month - 1]} {last.year}"
    )


def period_short(year: int, month: int, start_day: int) -> str:
    if start_day == 1:
        return MONTH_NAMES[month - 1]
    return f"{start_day} {MONTH_SHORT[month - 1]}"


def _keyboard(year: int, month: int, scope: str, today: date, start_day: int):
    prev_year, prev_month = shift_month(year, month, -1)
    next_year, next_month = shift_month(year, month, 1)

    builder = InlineKeyboardBuilder()
    builder.button(
        text=f"◀️ {period_short(prev_year, prev_month, start_day)}",
        callback_data=f"rep:{prev_year}-{prev_month:02d}:{scope}",
    )
    if (next_year, next_month) <= current_anchor(today, start_day):
        builder.button(
            text=f"{period_short(next_year, next_month, start_day)} ▶️",
            callback_data=f"rep:{next_year}-{next_month:02d}:{scope}",
        )

    other = SCOPE_MINE if scope == SCOPE_ALL else SCOPE_ALL
    builder.button(
        text="👤 Только мои" if other == SCOPE_MINE else "👥 Все участники",
        callback_data=f"rep:{year}-{month:02d}:{other}",
    )
    builder.adjust(2, 1)
    return builder.as_markup()


def _render(
    rows: list[Transaction], year: int, month: int, scope: str, start_day: int = 1
) -> str:
    title = period_title(year, month, start_day)
    title += " · только мои" if scope == SCOPE_MINE else ""

    if not rows:
        return f"<b>{html.escape(title)}</b>\n\nЗа этот месяц операций нет."

    expenses = [t for t in rows if t.type == EXPENSE]
    incomes = [t for t in rows if t.type == INCOME]
    spent = sum(t.amount for t in expenses)
    earned = sum(t.amount for t in incomes)
    balance = earned - spent

    lines = [f"<b>{html.escape(title)}</b>", ""]
    lines.append(f"➖ Траты  {format_amount(spent)}")
    lines.append(f"➕ Доходы  {format_amount(earned)}")
    sign = "+" if balance >= 0 else "−"
    lines.append(f"{'📈' if balance >= 0 else '📉'} Разница  {sign}{format_amount(balance)}")

    if expenses:
        by_category: dict[str, int] = {}
        for tx in expenses:
            by_category[tx.category] = by_category.get(tx.category, 0) + tx.amount
        ranked = sorted(by_category.items(), key=lambda item: -item[1])

        lines += ["", "<b>Траты по категориям</b>"]
        for name, amount in ranked[:TOP_CATEGORIES]:
            share = round(amount * 100 / spent) if spent else 0
            lines.append(f"{html.escape(name)} — {format_amount(amount)} · {share}%")
        if len(ranked) > TOP_CATEGORIES:
            rest = sum(amount for _, amount in ranked[TOP_CATEGORIES:])
            lines.append(f"Остальное — {format_amount(rest)}")

    auto = sum(1 for t in rows if t.source == "recurring")
    footer = f"Операций: {len(rows)}"
    if auto:
        footer += f", из них автоматических: {auto}"
    lines += ["", footer]

    return "\n".join(lines)


async def _build(
    repo: Repository,
    user_context: UserContext,
    year: int,
    month: int,
    scope: str,
) -> str:
    start_day = user_context.household.period_start_day if user_context.household else 1
    first, last = period_bounds(year, month, start_day)
    rows = await repo.list_transactions(
        date_from=first,
        date_to=last,
        user_id=user_context.telegram_id if scope == SCOPE_MINE else None,
    )
    return _render(rows, year, month, scope, start_day)


def _today(config: Config) -> date:
    return datetime.now(ZoneInfo(config.timezone)).date()


@router.message(F.text == kb.BUTTON_REPORT)
@router.message(Command("report"))
async def cmd_report(
    message: Message,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    if not user_context or not user_context.household:
        await message.answer("Сначала подключи учёт: /start")
        return

    today = _today(config)
    start_day = user_context.household.period_start_day
    year, month = current_anchor(today, start_day)
    text = await _build(repo, user_context, year, month, SCOPE_ALL)
    await message.answer(
        text,
        parse_mode="HTML",
        reply_markup=_keyboard(year, month, SCOPE_ALL, today, start_day),
    )


@router.callback_query(F.data.startswith("rep:"))
async def navigate(
    callback: CallbackQuery,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    await callback.answer()
    if callback.message is None or user_context is None:
        return

    _, period, scope = callback.data.split(":", 2)
    year, month = (int(part) for part in period.split("-"))

    today = _today(config)
    start_day = (
        user_context.household.period_start_day if user_context.household else 1
    )
    text = await _build(repo, user_context, year, month, scope)
    await callback.message.edit_text(
        text,
        parse_mode="HTML",
        reply_markup=_keyboard(year, month, scope, today, start_day),
    )


@router.message(Command("period"))
async def cmd_period(
    message: Message,
    command: CommandObject,
    registry: Registry,
    user_context: UserContext | None,
) -> None:
    if not user_context or not user_context.household:
        await message.answer("Сначала подключи учёт: /start")
        return

    current = user_context.household.period_start_day
    raw = (command.args or "").strip()

    if not raw:
        await message.answer(
            f"Учётный период начинается {current}-го числа.\n\n"
            "Поменять: /period 5 — тогда месяц будет считаться "
            "с 5-го по 4-е число следующего.\n"
            "Допустимо от 1 до 28."
        )
        return

    if not raw.isdigit() or not 1 <= int(raw) <= 28:
        await message.answer("Нужно число от 1 до 28.")
        return

    if not user_context.is_owner:
        await message.answer("Менять период может только владелец учёта.")
        return

    registry.set_period_start_day(user_context.household.id, int(raw))
    await message.answer(
        f"Период теперь начинается {int(raw)}-го числа. Посмотреть: /report"
    )
