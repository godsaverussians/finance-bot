from __future__ import annotations

import asyncio
import json
import time
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Sequence

import gspread
from gspread.exceptions import APIError, SpreadsheetNotFound, WorksheetNotFound

from .base import Category, Repository, Transaction

TRANSACTIONS = "transactions"
CATEGORIES = "categories"
RECURRING = "recurring"

TRANSACTIONS_HEADER = [
    "id",
    "created_at",
    "date",
    "type",
    "user_id",
    "user_name",
    "amount",
    "category",
    "comment",
    "source",
    "deleted",
]
CATEGORIES_HEADER = ["name", "type", "emoji", "order", "active"]
RECURRING_HEADER = [
    "id",
    "name",
    "type",
    "amount",
    "category",
    "day_of_month",
    "user_id",
    "active",
    "last_posted_month",
]

_CATEGORY_CACHE_TTL = 300


def _is_true(value) -> bool:
    return str(value).strip().upper() in {"TRUE", "1", "ДА", "YES"}


def _to_kopecks(value) -> int:
    raw = str(value).replace("\u00a0", "").replace(" ", "").replace(",", ".").strip()
    if not raw:
        return 0
    return int(round(float(raw) * 100))


class NoAccessError(RuntimeError):
    """Сервисный аккаунт не видит таблицу."""


class SheetsRepository(Repository):
    def __init__(self, client: gspread.Client, spreadsheet_id: str) -> None:
        self._client = client
        self._spreadsheet_id = spreadsheet_id
        self._categories_cache: tuple[float, list[Category]] | None = None

    # gspread синхронный, поэтому все вызовы уходят в отдельный поток
    async def _run(self, fn, *args, **kwargs):
        return await asyncio.to_thread(fn, *args, **kwargs)

    def _open(self) -> gspread.Spreadsheet:
        return self._client.open_by_key(self._spreadsheet_id)

    async def title(self) -> str:
        return await self._run(lambda: self._open().title)

    # --- структура ----------------------------------------------------

    def _ensure_structure_sync(self) -> None:
        book = self._open()
        existing = {ws.title: ws for ws in book.worksheets()}

        for name, header, rows in (
            (TRANSACTIONS, TRANSACTIONS_HEADER, 2000),
            (CATEGORIES, CATEGORIES_HEADER, 100),
            (RECURRING, RECURRING_HEADER, 100),
        ):
            if name in existing:
                ws = existing[name]
                if ws.row_values(1) != header:
                    ws.update([header], "A1")
            else:
                ws = book.add_worksheet(title=name, rows=rows, cols=len(header))
                ws.update([header], "A1")
            ws.freeze(rows=1)

        # дефолтный пустой лист мешает, но удаляем только если он реально пустой
        for junk in ("Лист1", "Sheet1"):
            ws = existing.get(junk)
            if ws is not None and not any(ws.get_all_values()):
                book.del_worksheet(ws)

    async def ensure_structure(self) -> None:
        await self._run(self._ensure_structure_sync)

    # --- категории ----------------------------------------------------

    def _set_categories_sync(self, categories: Sequence[Category]) -> None:
        ws = self._open().worksheet(CATEGORIES)
        rows = [
            [c.name, c.type, c.emoji, c.order, c.active]
            for c in categories
        ]
        ws.batch_clear([f"A2:E{max(ws.row_count, len(rows) + 1)}"])
        if rows:
            ws.update(rows, "A2", value_input_option="USER_ENTERED")

    async def set_categories(self, categories: Sequence[Category]) -> None:
        await self._run(self._set_categories_sync, list(categories))
        self._categories_cache = None

    def _get_categories_sync(self) -> list[Category]:
        ws = self._open().worksheet(CATEGORIES)
        result: list[Category] = []
        for row in ws.get_all_values()[1:]:
            padded = row + [""] * (len(CATEGORIES_HEADER) - len(row))
            name = padded[0].strip()
            if not name:
                continue
            try:
                order = int(padded[3] or 0)
            except ValueError:
                order = 0
            result.append(
                Category(
                    name=name,
                    type=(padded[1].strip() or "expense"),
                    emoji=padded[2].strip(),
                    order=order,
                    active=_is_true(padded[4]) if padded[4] != "" else True,
                )
            )
        result.sort(key=lambda c: (c.order, c.name))
        return result

    async def get_categories(self, type_: str | None = None) -> list[Category]:
        now = time.monotonic()
        if self._categories_cache is None or now - self._categories_cache[0] > _CATEGORY_CACHE_TTL:
            fresh = await self._run(self._get_categories_sync)
            self._categories_cache = (now, fresh)
        cached = self._categories_cache[1]
        return [c for c in cached if c.active and (type_ is None or c.type == type_)]

    def invalidate_cache(self) -> None:
        self._categories_cache = None

    # --- операции -----------------------------------------------------

    def _add_transaction_sync(self, tx: Transaction) -> str:
        ws = self._open().worksheet(TRANSACTIONS)
        ws.append_row(
            [
                tx.id,
                tx.created_at.astimezone(timezone.utc).isoformat(timespec="seconds"),
                tx.date.isoformat(),
                tx.type,
                tx.user_id,
                tx.user_name,
                round(tx.amount / 100, 2),
                tx.category,
                tx.comment,
                tx.source,
                tx.deleted,
            ],
            value_input_option="USER_ENTERED",
        )
        return tx.id

    async def add_transaction(self, tx: Transaction) -> str:
        return await self._run(self._add_transaction_sync, tx)

    def _list_transactions_sync(self) -> list[Transaction]:
        ws = self._open().worksheet(TRANSACTIONS)
        out: list[Transaction] = []
        for row in ws.get_all_values()[1:]:
            padded = row + [""] * (len(TRANSACTIONS_HEADER) - len(row))
            if not padded[0].strip():
                continue
            try:
                op_date = date.fromisoformat(padded[2].strip())
            except ValueError:
                continue
            try:
                created = datetime.fromisoformat(padded[1].strip())
            except ValueError:
                created = datetime.now(timezone.utc)
            out.append(
                Transaction(
                    id=padded[0].strip(),
                    created_at=created,
                    date=op_date,
                    type=padded[3].strip() or "expense",
                    user_id=int(padded[4] or 0),
                    user_name=padded[5],
                    amount=_to_kopecks(padded[6]),
                    category=padded[7],
                    comment=padded[8],
                    source=padded[9] or "manual",
                    deleted=_is_true(padded[10]),
                )
            )
        return out

    async def list_transactions(
        self,
        date_from: date | None = None,
        date_to: date | None = None,
        user_id: int | None = None,
        type_: str | None = None,
        include_deleted: bool = False,
    ) -> list[Transaction]:
        rows = await self._run(self._list_transactions_sync)
        result = [
            tx
            for tx in rows
            if (include_deleted or not tx.deleted)
            and (date_from is None or tx.date >= date_from)
            and (date_to is None or tx.date <= date_to)
            and (user_id is None or tx.user_id == user_id)
            and (type_ is None or tx.type == type_)
        ]
        result.sort(key=lambda tx: (tx.date, tx.created_at))
        return result

    def _soft_delete_sync(self, tx_id: str) -> bool:
        ws = self._open().worksheet(TRANSACTIONS)
        cell = ws.find(tx_id, in_column=1)
        if cell is None:
            return False
        deleted_col = TRANSACTIONS_HEADER.index("deleted") + 1
        ws.update_cell(cell.row, deleted_col, True)
        return True

    async def soft_delete(self, tx_id: str) -> bool:
        return await self._run(self._soft_delete_sync, tx_id)

    def _set_comment_sync(self, tx_id: str, comment: str) -> bool:
        ws = self._open().worksheet(TRANSACTIONS)
        cell = ws.find(tx_id, in_column=1)
        if cell is None:
            return False
        comment_col = TRANSACTIONS_HEADER.index("comment") + 1
        ws.update_cell(cell.row, comment_col, comment)
        return True

    async def set_comment(self, tx_id: str, comment: str) -> bool:
        return await self._run(self._set_comment_sync, tx_id, comment)


class SheetsFactory:
    """Один клиент gspread на процесс, репозиторий на таблицу."""

    SCOPES = (
        "https://www.googleapis.com/auth/spreadsheets",
        "https://www.googleapis.com/auth/drive.file",
    )

    def __init__(
        self,
        credentials_file: Path | None = None,
        credentials_json: str | None = None,
    ) -> None:
        if credentials_json:
            info = json.loads(credentials_json)
            self._client = gspread.service_account_from_dict(info, scopes=self.SCOPES)
        elif credentials_file is not None:
            self._client = gspread.service_account(
                filename=str(credentials_file), scopes=self.SCOPES
            )
            info = json.loads(credentials_file.read_text(encoding="utf-8-sig"))
        else:
            raise RuntimeError("Не передан ни файл ключа, ни его содержимое")

        self._email = info["client_email"]
        self._cache: dict[str, SheetsRepository] = {}

    @property
    def service_account_email(self) -> str:
        return self._email

    def repository(self, spreadsheet_id: str) -> SheetsRepository:
        if spreadsheet_id not in self._cache:
            self._cache[spreadsheet_id] = SheetsRepository(self._client, spreadsheet_id)
        return self._cache[spreadsheet_id]

    async def check_access(self, spreadsheet_id: str) -> str:
        """Возвращает название таблицы или бросает NoAccessError."""
        try:
            return await self.repository(spreadsheet_id).title()
        except (SpreadsheetNotFound, WorksheetNotFound) as exc:
            raise NoAccessError("Таблица не найдена") from exc
        except APIError as exc:
            raise NoAccessError("Нет доступа к таблице") from exc
