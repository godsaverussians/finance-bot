from __future__ import annotations

import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
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
    async def get_categories(self, type_: str | None = None) -> list[Category]: ...

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
