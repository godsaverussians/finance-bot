from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from typing import Sequence

EXPENSE = "expense"
INCOME = "income"


@dataclass(frozen=True)
class Category:
    name: str
    type: str
    emoji: str = ""
    order: int = 0
    active: bool = True
    # False — категория не показывается на кнопках ручного ввода,
    # но остаётся доступной для постоянных операций.
    manual: bool = True

    @property
    def label(self) -> str:
        return f"{self.emoji} {self.name}".strip()


@dataclass(frozen=True)
class Transaction:
    """Суммы всегда в копейках (int). Никаких float в деньгах."""

    type: str
    user_id: int
    user_name: str
    amount: int
    category: str
    date: date
    comment: str = ""
    source: str = "manual"
    deleted: bool = False
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def rubles(self) -> str:
        return f"{self.amount / 100:,.2f}".replace(",", " ")


MONTHLY = "monthly"
INTERVAL = "interval"


@dataclass(frozen=True)
class RecurringRule:
    """Постоянная операция: аренда, подписка, зарплата.

    Два расписания: раз в месяц в фиксированное число (monthly) или
    каждые N дней от даты старта (interval).
    """

    name: str
    type: str
    amount: int
    category: str
    user_id: int
    schedule_kind: str = MONTHLY
    day_of_month: int = 1
    interval_days: int = 0
    start_date: date | None = None
    active: bool = True
    last_posted_month: str = ""  # 'YYYY-MM' для monthly
    last_posted_date: date | None = None  # для interval
    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])

    @property
    def is_interval(self) -> bool:
        return self.schedule_kind == INTERVAL and self.interval_days > 0

    def next_due(self, today: date) -> date | None:
        """Ближайшая дата, начиная с которой можно записывать. Только для interval."""
        if not self.is_interval:
            return None
        if self.last_posted_date is None:
            return self.start_date or today
        return self.last_posted_date + timedelta(days=self.interval_days)

    def schedule_label(self) -> str:
        if self.is_interval:
            start = self.start_date.strftime("%d.%m.%Y") if self.start_date else "—"
            return f"каждые {self.interval_days} дн. с {start}"
        return f"каждое {self.day_of_month}-е число"


class Repository(ABC):
    """Хранилище финансов одного хозяйства.

    Бот работает только через этот интерфейс и ничего не знает про Google Sheets.
    Замена хранилища = новая реализация этих методов.
    """

    @abstractmethod
    async def title(self) -> str: ...

    @abstractmethod
    async def ensure_structure(self) -> None:
        """Создать нужные листы с заголовками, если их ещё нет."""

    @abstractmethod
    async def set_categories(self, categories: Sequence[Category]) -> None: ...

    @abstractmethod
    async def get_categories(
        self, type_: str | None = None, manual_only: bool = False
    ) -> list[Category]: ...

    @abstractmethod
    async def add_transaction(self, tx: Transaction) -> str: ...

    @abstractmethod
    async def list_transactions(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        user_id: int | None = None,
        type_: str | None = None,
        include_deleted: bool = False,
    ) -> list[Transaction]: ...

    @abstractmethod
    async def soft_delete(self, tx_id: str) -> bool: ...

    @abstractmethod
    async def set_comment(self, tx_id: str, comment: str) -> bool: ...

    @abstractmethod
    async def list_recurring(self) -> list[RecurringRule]: ...

    @abstractmethod
    async def add_recurring(self, rule: RecurringRule) -> str: ...

    @abstractmethod
    async def update_recurring(
        self,
        rule_id: str,
        *,
        active: bool | None = None,
        last_posted_month: str | None = None,
        last_posted_date: date | None = None,
    ) -> bool: ...
