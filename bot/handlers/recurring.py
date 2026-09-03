from __future__ import annotations

import logging
from datetime import date, datetime

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder
from zoneinfo import ZoneInfo

from ..money import format_amount, parse_amount
from ..registry import UserContext
from ..repository.base import (
    EXPENSE,
    INCOME,
    INTERVAL,
    MONTHLY,
    RecurringRule,
    Repository,
)

logger = logging.getLogger(__name__)
router = Router(name="recurring")

TYPE_LABELS = {EXPENSE: "Трата", INCOME: "Доход"}
TYPE_SIGNS = {EXPENSE: "➖", INCOME: "➕"}


class NewRule(StatesGroup):
    type = State()
    category = State()
    amount = State()
    schedule = State()
    day = State()
    interval = State()
    start = State()
    name = State()


def _list_keyboard(rules: list[RecurringRule]):
    builder = InlineKeyboardBuilder()
    for rule in rules:
        mark = "" if rule.active else "⏸ "
        builder.button(
            text=f"{mark}{TYPE_SIGNS[rule.type]} {format_amount(rule.amount)} · {rule.name}",
            callback_data=f"rec:open:{rule.id}",
        )
    builder.button(text="➕ Добавить", callback_data="rec:new")
    builder.adjust(1)
    return builder.as_markup()


def _rule_keyboard(rule: RecurringRule):
    builder = InlineKeyboardBuilder()
    builder.button(
        text="▶️ Включить" if not rule.active else "⏸ Выключить",
        callback_data=f"rec:toggle:{rule.id}",
    )
    builder.button(text="◀️ Назад", callback_data="rec:list")
    builder.adjust(1)
    return builder.as_markup()


def _describe(rule: RecurringRule) -> str:
    status = "активна" if rule.active else "выключена"
    if rule.is_interval:
        last = (
            rule.last_posted_date.strftime("%d.%m.%Y")
            if rule.last_posted_date
            else "ещё не записывалась"
        )
    else:
        last = rule.last_posted_month or "ещё не записывалась"
    return (
        f"{TYPE_SIGNS[rule.type]} {rule.name}\n"
        f"{format_amount(rule.amount)} · {rule.category}\n"
        f"{rule.schedule_label().capitalize()} · {status}\n"
        f"Последняя запись: {last}"
    )


async def _show_list(message: Message, repo: Repository, edit: bool = False) -> None:
    rules = await repo.list_recurring()
    if rules:
        text = "Постоянные операции:"
    else:
        text = (
            "Постоянных операций пока нет.\n\n"
            "Это аренда, подписки, зарплата — то, что записывается само "
            "раз в месяц в нужное число."
        )
    keyboard = _list_keyboard(rules)
    if edit:
        await message.edit_text(text, reply_markup=keyboard)
    else:
        await message.answer(text, reply_markup=keyboard)


@router.message(Command("recurring"))
async def cmd_recurring(
    message: Message, state: FSMContext, user_context: UserContext | None, repo
) -> None:
    if not user_context or not user_context.household:
        await message.answer("Сначала подключи учёт: /start")
        return
    await state.clear()
    await _show_list(message, repo)


@router.callback_query(F.data == "rec:list")
async def back_to_list(
    callback: CallbackQuery, state: FSMContext, repo: Repository
) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.clear()
    await _show_list(callback.message, repo, edit=True)


@router.callback_query(F.data.startswith("rec:open:"))
async def open_rule(callback: CallbackQuery, repo: Repository) -> None:
    await callback.answer()
    if callback.message is None:
        return

    rule_id = callback.data.rsplit(":", 1)[1]
    rules = await repo.list_recurring()
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        await callback.message.edit_text("Операция не найдена.")
        return

    await callback.message.edit_text(_describe(rule), reply_markup=_rule_keyboard(rule))


@router.callback_query(F.data.startswith("rec:toggle:"))
async def toggle_rule(callback: CallbackQuery, repo: Repository) -> None:
    rule_id = callback.data.rsplit(":", 1)[1]
    rules = await repo.list_recurring()
    rule = next((r for r in rules if r.id == rule_id), None)
    if rule is None:
        await callback.answer("Не найдено")
        return

    await repo.update_recurring(rule_id, active=not rule.active)
    await callback.answer("Включена" if not rule.active else "Выключена")

    updated = RecurringRule(**{**rule.__dict__, "active": not rule.active})
    if callback.message is not None:
        await callback.message.edit_text(
            _describe(updated), reply_markup=_rule_keyboard(updated)
        )


# --- добавление ------------------------------------------------------


@router.callback_query(F.data == "rec:new")
async def new_rule(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="➖ Трата", callback_data="rec:type:expense")
    builder.button(text="➕ Доход", callback_data="rec:type:income")
    builder.adjust(2)

    await state.set_state(NewRule.type)
    await callback.message.edit_text("Что записывать?", reply_markup=builder.as_markup())


@router.callback_query(NewRule.type, F.data.startswith("rec:type:"))
async def pick_type(callback: CallbackQuery, state: FSMContext, repo: Repository) -> None:
    await callback.answer()
    if callback.message is None:
        return

    type_ = callback.data.rsplit(":", 1)[1]
    items = await repo.get_categories(type_)
    if not items:
        await callback.message.edit_text("Нет подходящих категорий.")
        await state.clear()
        return

    builder = InlineKeyboardBuilder()
    for index, category in enumerate(items):
        builder.button(text=category.label, callback_data=f"rec:cat:{index}")
    builder.adjust(2)

    await state.set_state(NewRule.category)
    await state.update_data(type=type_, category_names=[c.name for c in items])
    await callback.message.edit_text("Категория?", reply_markup=builder.as_markup())


@router.callback_query(NewRule.category, F.data.startswith("rec:cat:"))
async def pick_category(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return

    data = await state.get_data()
    names = data.get("category_names", [])
    index = int(callback.data.rsplit(":", 1)[1])
    if index >= len(names):
        await state.clear()
        await callback.message.edit_text("Список устарел, начни заново: /recurring")
        return

    await state.set_state(NewRule.amount)
    await state.update_data(category=names[index])
    await callback.message.edit_text(f"{names[index]}\n\nСумма?")


@router.message(NewRule.amount)
async def take_amount(message: Message, state: FSMContext) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer("Не понял сумму. Например: 30000 или 30к")
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="📅 Каждое число месяца", callback_data="rec:sched:monthly")
    builder.button(text="🔄 Каждые N дней", callback_data="rec:sched:interval")
    builder.adjust(1)

    await state.set_state(NewRule.schedule)
    await state.update_data(amount=amount)
    await message.answer("Как часто?", reply_markup=builder.as_markup())


@router.callback_query(NewRule.schedule, F.data == "rec:sched:monthly")
async def pick_monthly(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(NewRule.day)
    await state.update_data(schedule_kind=MONTHLY)
    await callback.message.edit_text(
        "Какого числа записывать? Одно число от 1 до 31.\n\n"
        "Если в месяце столько дней нет, запишется последним днём."
    )


@router.callback_query(NewRule.schedule, F.data == "rec:sched:interval")
async def pick_interval(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return
    await state.set_state(NewRule.interval)
    await state.update_data(schedule_kind=INTERVAL)
    await callback.message.edit_text(
        "Раз в сколько дней? Число от 1 до 365.\n\nНапример: 14 — раз в две недели."
    )


@router.message(NewRule.interval)
async def take_interval(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 365:
        await message.answer("Нужно число от 1 до 365.")
        return

    builder = InlineKeyboardBuilder()
    builder.button(text="Сегодня", callback_data="rec:start:today")
    builder.adjust(1)

    await state.set_state(NewRule.start)
    await state.update_data(interval_days=int(raw))
    await message.answer(
        "С какой даты считать? Пришли дату в виде 05.09.2026 или нажми «Сегодня».",
        reply_markup=builder.as_markup(),
    )


async def _ask_name(message: Message, state: FSMContext) -> None:
    await state.set_state(NewRule.name)
    await message.answer("Название? Например: аренда, Яндекс Плюс, линзы.")


@router.callback_query(NewRule.start, F.data == "rec:start:today")
async def start_today(callback: CallbackQuery, state: FSMContext, config) -> None:
    await callback.answer()
    if callback.message is None:
        return
    today = datetime.now(ZoneInfo(config.timezone)).date()
    await state.update_data(start_date=today.isoformat())
    await callback.message.edit_text(f"Старт: {today.strftime('%d.%m.%Y')}")
    await _ask_name(callback.message, state)


@router.message(NewRule.start)
async def take_start(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    parsed = None
    for fmt in ("%d.%m.%Y", "%d.%m.%y", "%Y-%m-%d"):
        try:
            parsed = datetime.strptime(raw, fmt).date()
            break
        except ValueError:
            continue
    if parsed is None:
        await message.answer("Не разобрал дату. Формат: 05.09.2026")
        return
    await state.update_data(start_date=parsed.isoformat())
    await _ask_name(message, state)


@router.message(NewRule.day)
async def take_day(message: Message, state: FSMContext) -> None:
    raw = (message.text or "").strip()
    if not raw.isdigit() or not 1 <= int(raw) <= 31:
        await message.answer("Нужно число от 1 до 31.")
        return
    await state.update_data(day=int(raw))
    await _ask_name(message, state)


@router.message(NewRule.name)
async def take_name(
    message: Message,
    state: FSMContext,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    name = " ".join((message.text or "").split())[:60]
    if not name:
        await message.answer("Название не может быть пустым.")
        return

    data = await state.get_data()
    await state.clear()
    assert user_context is not None

    start_raw = data.get("start_date")
    rule = RecurringRule(
        name=name,
        type=data.get("type", EXPENSE),
        amount=int(data["amount"]),
        category=data.get("category", "Прочее"),
        user_id=user_context.telegram_id,
        schedule_kind=data.get("schedule_kind", MONTHLY),
        day_of_month=int(data.get("day", 1)),
        interval_days=int(data.get("interval_days", 0)),
        start_date=date.fromisoformat(start_raw) if start_raw else None,
    )

    try:
        await repo.add_recurring(rule)
    except Exception:
        logger.exception("Не удалось добавить постоянную операцию")
        await message.answer("Не смог записать в таблицу. Попробуй ещё раз.")
        return

    await message.answer(
        "Добавил:\n\n"
        + _describe(rule)
        + "\n\nЗапишется в ближайшую проверку, если нужное число уже прошло."
    )
