from __future__ import annotations

import logging
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import F, Router
from aiogram.filters import Command, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message

from .. import keyboards as kb
from ..config import Config
from ..money import format_amount, parse_amount
from ..registry import UserContext
from ..repository.base import EXPENSE, INCOME, Repository, Transaction

logger = logging.getLogger(__name__)
router = Router(name="entry")

TYPE_LABELS = {EXPENSE: "Трата", INCOME: "Доход"}
TYPE_SIGNS = {EXPENSE: "➖", INCOME: "➕"}


class Entry(StatesGroup):
    category = State()
    amount = State()
    comment = State()


def _today(config: Config):
    return datetime.now(ZoneInfo(config.timezone)).date()


def _needs_setup(user_context: UserContext | None) -> bool:
    return not user_context or not user_context.household


def _card(tx: Transaction) -> str:
    line = (
        f"{TYPE_SIGNS[tx.type]} {format_amount(tx.amount)} · {tx.category}"
    )
    if tx.comment:
        line += f"\n💬 {tx.comment}"
    return line


# --- начало ввода ----------------------------------------------------


async def _start_entry(
    message: Message,
    state: FSMContext,
    repo: Repository,
    type_: str,
) -> None:
    items = await repo.get_categories(type_, manual_only=True)
    if not items:
        await message.answer(
            f"Нет категорий для «{TYPE_LABELS[type_].lower()}». "
            "Добавь их в лист categories в таблице, потом /categories."
        )
        return

    await state.set_state(Entry.category)
    await state.update_data(
        type=type_,
        category_names=[c.name for c in items],
    )
    await message.answer(
        f"{TYPE_LABELS[type_]}: выбери категорию",
        reply_markup=kb.categories(items),
    )


@router.message(F.text == kb.BUTTON_EXPENSE)
@router.message(Command("add"))
async def add_expense(
    message: Message, state: FSMContext, user_context: UserContext | None, repo
) -> None:
    if _needs_setup(user_context):
        await message.answer("Сначала подключи учёт: /start")
        return
    await state.clear()
    await _start_entry(message, state, repo, EXPENSE)


@router.message(F.text == kb.BUTTON_INCOME)
@router.message(Command("income"))
async def add_income(
    message: Message, state: FSMContext, user_context: UserContext | None, repo
) -> None:
    if _needs_setup(user_context):
        await message.answer("Сначала подключи учёт: /start")
        return
    await state.clear()
    await _start_entry(message, state, repo, INCOME)


@router.callback_query(Entry.category, F.data.startswith("cat:"))
async def pick_category(
    callback: CallbackQuery,
    state: FSMContext,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    await callback.answer()
    if callback.message is None:
        return

    data = await state.get_data()
    names: list[str] = data.get("category_names", [])
    index = int(callback.data.split(":", 1)[1])
    if index >= len(names):
        await callback.message.answer("Категория устарела, начни заново.")
        await state.clear()
        return

    category = names[index]
    type_ = data.get("type", EXPENSE)
    pending: int | None = data.get("pending_amount")

    if pending is not None:
        await state.clear()
        await _save(callback.message, config, user_context, repo, type_, category, pending)
        await callback.message.delete()
        return

    await state.set_state(Entry.amount)
    await state.update_data(category=category)
    await callback.message.edit_text(
        f"{TYPE_LABELS[type_]} · {category}\n\nСумма?",
    )


@router.callback_query(F.data == "entry:cancel")
async def cancel_entry(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer("Отменено")
    await state.clear()
    if isinstance(callback.message, Message):
        await callback.message.edit_text("Отменено.")


# --- сумма и сохранение ----------------------------------------------


async def _save(
    message: Message,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
    type_: str,
    category: str,
    amount: int,
) -> Transaction | None:
    assert user_context is not None
    tx = Transaction(
        type=type_,
        user_id=user_context.telegram_id,
        user_name=user_context.name,
        amount=amount,
        category=category,
        date=_today(config),
    )
    try:
        await repo.add_transaction(tx)
    except Exception:
        logger.exception("Не удалось записать операцию")
        await message.answer("Не смог записать в таблицу. Попробуй ещё раз.")
        return None

    await message.answer(_card(tx), reply_markup=kb.saved_actions(tx.id))
    return tx


@router.message(Entry.amount)
async def take_amount(
    message: Message,
    state: FSMContext,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    amount = parse_amount(message.text or "")
    if amount is None:
        await message.answer(
            "Не понял сумму. Можно так: 1500, 1 500,50, 1.5к, 300+450"
        )
        return

    data = await state.get_data()
    await state.clear()
    await _save(
        message,
        config,
        user_context,
        repo,
        data.get("type", EXPENSE),
        data.get("category", "Прочее"),
        amount,
    )


# --- быстрый ввод суммой ---------------------------------------------


@router.message(StateFilter(None), F.text.regexp(r"^[\d\s.,+\-*/()кk]+$"))
async def quick_amount(
    message: Message, state: FSMContext, user_context: UserContext | None, repo
) -> None:
    """Просто число в чате = трата, спрашиваем только категорию."""
    if _needs_setup(user_context):
        return
    amount = parse_amount(message.text or "")
    if amount is None:
        return

    items = await repo.get_categories(EXPENSE, manual_only=True)
    if not items:
        return

    await state.set_state(Entry.category)
    await state.update_data(
        type=EXPENSE,
        category_names=[c.name for c in items],
        pending_amount=amount,
    )
    await message.answer(
        f"Трата {format_amount(amount)} · категория?",
        reply_markup=kb.categories(items),
    )


# --- комментарий -----------------------------------------------------


@router.callback_query(F.data.startswith("note:"))
async def ask_comment(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    if callback.message is None:
        return

    tx_id = callback.data.split(":", 1)[1]
    await state.set_state(Entry.comment)
    await state.update_data(comment_tx=tx_id)
    await callback.message.answer("Напиши коротко, на что это было.")


@router.message(Entry.comment)
async def take_comment(
    message: Message, state: FSMContext, repo: Repository
) -> None:
    data = await state.get_data()
    tx_id = data.get("comment_tx")
    await state.clear()

    if not tx_id:
        return

    comment = " ".join((message.text or "").split())[:200]
    ok = await repo.set_comment(tx_id, comment)
    await message.answer("Записал комментарий." if ok else "Не нашёл эту операцию.")


# --- удаление и просмотр ---------------------------------------------


@router.callback_query(F.data.startswith("del:"))
async def delete_transaction(callback: CallbackQuery, repo: Repository) -> None:
    tx_id = callback.data.split(":", 1)[1]
    ok = await repo.soft_delete(tx_id)
    await callback.answer("Удалено" if ok else "Не нашёл операцию")
    if ok and isinstance(callback.message, Message):
        await callback.message.edit_text("🗑 Удалено")


@router.message(F.text == kb.BUTTON_LAST)
@router.message(Command("last"))
async def show_last(
    message: Message, user_context: UserContext | None, repo: Repository
) -> None:
    if _needs_setup(user_context):
        await message.answer("Сначала подключи учёт: /start")
        return

    rows = await repo.list_transactions()
    if not rows:
        await message.answer("Пока ничего не записано.")
        return

    lines = []
    for tx in rows[-10:][::-1]:
        parts = [
            tx.date.strftime("%d.%m"),
            TYPE_SIGNS[tx.type],
            format_amount(tx.amount),
            tx.category,
        ]
        line = " ".join(parts)
        if tx.comment:
            line += f" — {tx.comment}"
        lines.append(line)

    await message.answer("Последние операции:\n\n" + "\n".join(lines))


@router.message(Command("undo"))
async def undo_last(
    message: Message, user_context: UserContext | None, repo: Repository
) -> None:
    if _needs_setup(user_context):
        return
    assert user_context is not None

    rows = await repo.list_transactions(user_id=user_context.telegram_id)
    if not rows:
        await message.answer("Нечего отменять.")
        return

    last = rows[-1]
    if await repo.soft_delete(last.id):
        await message.answer(f"Удалил:\n{_card(last)}")
    else:
        await message.answer("Не получилось удалить.")


@router.message(Command("repeat"))
async def repeat_last(
    message: Message,
    config: Config,
    user_context: UserContext | None,
    repo: Repository,
) -> None:
    if _needs_setup(user_context):
        return
    assert user_context is not None

    rows = await repo.list_transactions(user_id=user_context.telegram_id)
    if not rows:
        await message.answer("Нет операции, которую можно повторить.")
        return

    source = rows[-1]
    tx = Transaction(
        type=source.type,
        user_id=user_context.telegram_id,
        user_name=user_context.name,
        amount=source.amount,
        category=source.category,
        comment=source.comment,
        date=_today(config),
    )
    await repo.add_transaction(tx)
    await message.answer(
        "Повторил:\n" + _card(tx), reply_markup=kb.saved_actions(tx.id)
    )
