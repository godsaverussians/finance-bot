from __future__ import annotations

import asyncio
import calendar
import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from aiogram import Bot

from .config import Config
from .money import format_amount
from .registry import Registry
from .repository.base import RecurringRule, Transaction
from .repository.sheets import SheetsFactory

logger = logging.getLogger(__name__)

CHECK_INTERVAL_SECONDS = 30 * 60


def month_key(day: date) -> str:
    return day.strftime("%Y-%m")


def effective_day(day_of_month: int, when: date) -> int:
    """31-е число в феврале превращается в 28-е (или 29-е)."""
    last_day = calendar.monthrange(when.year, when.month)[1]
    return min(day_of_month, last_day)


class RecurringPoster:
    """Раз в полчаса смотрит, какие постоянные операции пора записать.

    Пропущенные дни не теряются: условие «сегодня уже не раньше нужного числа»
    плюс отметка last_posted_month дают ровно одну запись в месяц, даже если
    бот лежал первого числа или перезапускался пять раз подряд.
    """

    def __init__(
        self,
        bot: Bot,
        config: Config,
        registry: Registry,
        factory: SheetsFactory,
    ) -> None:
        self._bot = bot
        self._config = config
        self._registry = registry
        self._factory = factory
        self._timezone = ZoneInfo(config.timezone)

    def today(self) -> date:
        return datetime.now(self._timezone).date()

    async def run_forever(self) -> None:
        while True:
            try:
                posted = await self.run_once()
                if posted:
                    logger.info("Записано постоянных операций: %s", posted)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("Ошибка в планировщике")
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)

    async def run_once(self) -> int:
        today = self.today()
        key = month_key(today)
        posted = 0

        for household in self._registry.all_households():
            repo = self._factory.repository(household.spreadsheet_id)
            try:
                rules = await repo.list_recurring()
            except Exception:
                logger.exception("Не прочитал recurring для %s", household.title)
                continue

            for rule in rules:
                op_date = self._due_date(rule, today, key)
                if op_date is None:
                    continue
                if await self._post(repo, rule, op_date, key):
                    posted += 1

        return posted

    def _due_date(self, rule: RecurringRule, today: date, key: str) -> date | None:
        """Дата операции, если пора записывать. Иначе None."""
        if not rule.active:
            return None

        if rule.is_interval:
            next_due = rule.next_due(today)
            if next_due is None or today < next_due:
                return None
            # Пропущенные периоды не досчитываем: записываем один раз и
            # отсчитываем следующий срок от сегодня.
            return today

        if rule.last_posted_month == key:
            return None
        if today.day < effective_day(rule.day_of_month, today):
            return None
        return today.replace(day=effective_day(rule.day_of_month, today))

    async def _post(
        self, repo, rule: RecurringRule, op_date: date, key: str
    ) -> bool:
        tx = Transaction(
            type=rule.type,
            user_id=rule.user_id,
            user_name="авто",
            amount=rule.amount,
            category=rule.category,
            comment=rule.name,
            date=op_date,
            source="recurring",
        )

        try:
            await repo.add_transaction(tx)
            # Отметку ставим только после успешной записи, иначе операция пропадёт.
            if rule.is_interval:
                await repo.update_recurring(rule.id, last_posted_date=op_date)
            else:
                await repo.update_recurring(rule.id, last_posted_month=key)
        except Exception:
            logger.exception("Не записал постоянную операцию %s", rule.name)
            return False

        await self._notify(rule, tx)
        return True

    async def _notify(self, rule: RecurringRule, tx: Transaction) -> None:
        if not rule.user_id:
            return
        sign = "➕" if tx.type == "income" else "➖"
        try:
            await self._bot.send_message(
                rule.user_id,
                f"Постоянная операция за {tx.date.strftime('%d.%m')}:\n"
                f"{sign} {format_amount(tx.amount)} · {tx.category} — {rule.name}",
            )
        except Exception:
            logger.warning("Не смог уведомить %s", rule.user_id)
