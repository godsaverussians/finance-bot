from __future__ import annotations

import logging

from aiogram import F, Router
from aiogram.filters import Command, CommandObject, CommandStart
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from aiogram.utils.keyboard import InlineKeyboardBuilder

from ..config import Config
from ..constants import (
    default_categories,
    parse_category_list,
    parse_spreadsheet_id,
    spreadsheet_url,
)
from ..registry import INVITE_TTL_HOURS, Registry, UserContext
from ..repository.base import EXPENSE, INCOME
from ..repository.sheets import NoAccessError, SheetsFactory

logger = logging.getLogger(__name__)
router = Router(name="onboarding")


class Onboarding(StatesGroup):
    bootstrap_code = State()
    spreadsheet_link = State()
    categories_mode = State()
    expense_list = State()
    income_list = State()
    invite_code = State()


def _start_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="🆕 Создать учёт", callback_data="onb:create")
    builder.button(text="🔗 Присоединиться по коду", callback_data="onb:join")
    builder.adjust(1)
    return builder.as_markup()


def _categories_mode_keyboard():
    builder = InlineKeyboardBuilder()
    builder.button(text="📋 Взять базовый набор", callback_data="onb:cats:default")
    builder.button(text="✍️ Ввести свои", callback_data="onb:cats:custom")
    builder.adjust(1)
    return builder.as_markup()


async def _finish(message: Message, state: FSMContext, context_household_title: str) -> None:
    await state.clear()
    await message.answer(
        f"Готово. Учёт «{context_household_title}» подключён.\n\n"
        "Дальше: /status — что подключено, /categories — список категорий, "
        "/invite — код для второго участника.\n\n"
        "Внесение трат появится на следующем шаге разработки."
    )


# --- вход ------------------------------------------------------------


@router.message(CommandStart())
async def cmd_start(
    message: Message,
    state: FSMContext,
    user_context: UserContext | None,
) -> None:
    await state.clear()

    if user_context and user_context.household:
        await message.answer(
            f"Ты уже подключён к учёту «{user_context.household.title}».\n"
            "/status — подробности, /categories — категории."
        )
        return

    await message.answer(
        "Привет. Этот бот записывает траты и доходы в твою Google-таблицу.\n\n"
        "Если учёт уже есть у кого-то — присоединяйся по коду. "
        "Если нет — создай новый.",
        reply_markup=_start_keyboard(),
    )


@router.callback_query(F.data == "onb:create")
async def start_create(
    callback: CallbackQuery, state: FSMContext, config: Config
) -> None:
    await callback.answer()
    assert isinstance(callback.message, Message)

    if config.bootstrap_code:
        await state.set_state(Onboarding.bootstrap_code)
        await callback.message.answer("Пришли код доступа на создание учёта.")
    else:
        await state.set_state(Onboarding.spreadsheet_link)
        await callback.message.answer(_link_prompt())


def _link_prompt() -> str:
    return (
        "Пришли ссылку на Google-таблицу, в которую бот будет писать.\n\n"
        "Если таблицы ещё нет: создай пустую на sheets.new, нажми «Поделиться», "
        "выдай права Редактор на email сервисного аккаунта (пришлю его следующим "
        "сообщением, если нужно — команда /sa), потом кидай ссылку сюда."
    )


@router.message(Onboarding.bootstrap_code)
async def take_bootstrap_code(
    message: Message, state: FSMContext, config: Config
) -> None:
    if (message.text or "").strip() != config.bootstrap_code:
        await message.answer("Код неверный. Попробуй ещё раз или /cancel.")
        return
    await state.set_state(Onboarding.spreadsheet_link)
    await message.answer(_link_prompt())


@router.message(Onboarding.spreadsheet_link)
async def take_spreadsheet_link(
    message: Message,
    state: FSMContext,
    registry: Registry,
    factory: SheetsFactory,
    user_context: UserContext | None,
) -> None:
    spreadsheet_id = parse_spreadsheet_id(message.text or "")
    if spreadsheet_id is None:
        await message.answer(
            "Не похоже на ссылку Google-таблицы. Она выглядит так:\n"
            "docs.google.com/spreadsheets/d/…/edit"
        )
        return

    existing = registry.find_household_by_spreadsheet(spreadsheet_id)
    if existing is not None:
        await message.answer(
            "К этой таблице уже привязан учёт. Попроси у владельца код через /invite."
        )
        return

    await message.answer("Проверяю доступ…")
    try:
        title = await factory.check_access(spreadsheet_id)
    except NoAccessError:
        await message.answer(
            "Не могу открыть таблицу. Нажми «Поделиться» и выдай права Редактор:\n\n"
            f"`{factory.service_account_email}`\n\n"
            "Потом пришли ссылку снова.",
            parse_mode="Markdown",
        )
        return

    repo = factory.repository(spreadsheet_id)
    try:
        await repo.ensure_structure()
    except Exception:
        logger.exception("Не удалось создать структуру листов")
        await message.answer("Доступ есть, но не получилось создать листы. Попробуй ещё раз.")
        return

    assert user_context is not None
    household = registry.create_household(title, spreadsheet_id, user_context.telegram_id)

    await state.update_data(household_id=household.id)
    await state.set_state(Onboarding.categories_mode)
    await message.answer(
        f"Таблица «{title}» подключена, листы созданы.\n\n"
        "Теперь категории. Можно взять базовый набор и потом поправить руками "
        "в таблице, либо сразу ввести свои.",
        reply_markup=_categories_mode_keyboard(),
    )


# --- категории -------------------------------------------------------


@router.callback_query(Onboarding.categories_mode, F.data == "onb:cats:default")
async def use_default_categories(
    callback: CallbackQuery,
    state: FSMContext,
    registry: Registry,
    factory: SheetsFactory,
) -> None:
    await callback.answer()
    assert isinstance(callback.message, Message)

    data = await state.get_data()
    household = registry.get_household(int(data["household_id"]))
    assert household is not None

    repo = factory.repository(household.spreadsheet_id)
    await repo.set_categories(default_categories())
    await _finish(callback.message, state, household.title)


@router.callback_query(Onboarding.categories_mode, F.data == "onb:cats:custom")
async def ask_expense_categories(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    assert isinstance(callback.message, Message)
    await state.set_state(Onboarding.expense_list)
    await callback.message.answer(
        "Перечисли категории трат через запятую одним сообщением.\n\n"
        "Например: Продукты, Кафе, Транспорт, Жильё, Здоровье, Прочее"
    )


@router.message(Onboarding.expense_list)
async def take_expense_categories(message: Message, state: FSMContext) -> None:
    categories = parse_category_list(message.text or "", EXPENSE)
    if not categories:
        await message.answer("Не разобрал ни одной категории. Попробуй ещё раз.")
        return

    await state.update_data(
        expenses=[(c.name, c.order) for c in categories]
    )
    await state.set_state(Onboarding.income_list)
    await message.answer(
        f"Принял {len(categories)} категорий трат.\n\n"
        "Теперь категории доходов, тоже через запятую.\n"
        "Например: Зарплата, Фриланс, Прочее"
    )


@router.message(Onboarding.income_list)
async def take_income_categories(
    message: Message,
    state: FSMContext,
    registry: Registry,
    factory: SheetsFactory,
) -> None:
    incomes = parse_category_list(message.text or "", INCOME)
    if not incomes:
        await message.answer("Не разобрал ни одной категории. Попробуй ещё раз.")
        return

    data = await state.get_data()
    household = registry.get_household(int(data["household_id"]))
    assert household is not None

    expenses = parse_category_list(
        ",".join(name for name, _ in data.get("expenses", [])), EXPENSE
    )

    repo = factory.repository(household.spreadsheet_id)
    await repo.set_categories([*expenses, *incomes])
    await _finish(message, state, household.title)


# --- присоединение ---------------------------------------------------


@router.callback_query(F.data == "onb:join")
async def start_join(callback: CallbackQuery, state: FSMContext) -> None:
    await callback.answer()
    assert isinstance(callback.message, Message)
    await state.set_state(Onboarding.invite_code)
    await callback.message.answer("Пришли код приглашения (8 символов).")


@router.message(Command("join"))
async def cmd_join(
    message: Message,
    command: CommandObject,
    state: FSMContext,
    registry: Registry,
    user_context: UserContext | None,
) -> None:
    if not command.args:
        await state.set_state(Onboarding.invite_code)
        await message.answer("Пришли код приглашения (8 символов).")
        return
    await _redeem(message, state, registry, user_context, command.args)


@router.message(Onboarding.invite_code)
async def take_invite_code(
    message: Message,
    state: FSMContext,
    registry: Registry,
    user_context: UserContext | None,
) -> None:
    await _redeem(message, state, registry, user_context, message.text or "")


async def _redeem(
    message: Message,
    state: FSMContext,
    registry: Registry,
    user_context: UserContext | None,
    code: str,
) -> None:
    assert user_context is not None
    result = registry.redeem_invite(code, user_context.telegram_id)
    if isinstance(result, str):
        await message.answer(f"{result}\n\nМожно отменить: /cancel")
        return

    await state.clear()
    await message.answer(
        f"Готово, ты подключён к учёту «{result.title}».\n\n"
        "Категории уже настроены владельцем. "
        "Если хочешь смотреть данные глазами — попроси у него ссылку на таблицу."
    )


@router.message(Command("invite"))
async def cmd_invite(
    message: Message, registry: Registry, user_context: UserContext | None
) -> None:
    if not user_context or not user_context.household:
        await message.answer("Сначала подключи учёт: /start")
        return
    if not user_context.is_owner:
        await message.answer("Приглашать может только владелец учёта.")
        return

    code, expires = registry.create_invite(
        user_context.household.id, user_context.telegram_id
    )
    await message.answer(
        f"Код приглашения:\n\n`{code}`\n\n"
        f"Одноразовый, живёт {INVITE_TTL_HOURS} часа. "
        f"Второй участник вводит его командой /join {code}",
        parse_mode="Markdown",
    )


# --- служебное -------------------------------------------------------


@router.message(Command("status"))
async def cmd_status(
    message: Message, registry: Registry, user_context: UserContext | None, repo
) -> None:
    if not user_context or not user_context.household:
        await message.answer("Учёт не подключён. /start")
        return

    household = user_context.household
    members = registry.members(household.id)
    categories = await repo.get_categories()
    expenses = sum(1 for c in categories if c.type == EXPENSE)
    incomes = sum(1 for c in categories if c.type == INCOME)

    people = "\n".join(
        f"• {name}" + (" (владелец)" if role == "owner" else "") for _, name, role in members
    )
    await message.answer(
        f"Учёт: «{household.title}»\n"
        f"Таблица: {spreadsheet_url(household.spreadsheet_id)}\n"
        f"Категорий: {expenses} трат, {incomes} доходов\n\n"
        f"Участники:\n{people}"
    )


@router.message(Command("categories"))
async def cmd_categories(message: Message, user_context: UserContext | None, repo) -> None:
    if not user_context or not user_context.household:
        await message.answer("Учёт не подключён. /start")
        return

    repo.invalidate_cache()
    categories = await repo.get_categories()
    if not categories:
        await message.answer("Категорий нет. Добавь их прямо в лист categories в таблице.")
        return

    expenses = [c.label for c in categories if c.type == EXPENSE]
    incomes = [c.label for c in categories if c.type == INCOME]
    lines = ["*Траты*", ", ".join(expenses) or "—", "", "*Доходы*", ", ".join(incomes) or "—"]
    lines += ["", "Правятся в листе `categories` в таблице, бот подхватит после /categories."]
    await message.answer("\n".join(lines), parse_mode="Markdown")


@router.message(Command("sa"))
async def cmd_service_account(message: Message, factory: SheetsFactory) -> None:
    await message.answer(
        "Email сервисного аккаунта, ему нужны права Редактор на таблицу:\n\n"
        f"`{factory.service_account_email}`",
        parse_mode="Markdown",
    )


@router.message(Command("cancel"))
async def cmd_cancel(message: Message, state: FSMContext) -> None:
    if await state.get_state() is None:
        await message.answer("Нечего отменять.")
        return
    await state.clear()
    await message.answer("Отменил. /start чтобы начать заново.")
