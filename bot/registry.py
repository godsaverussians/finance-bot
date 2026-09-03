from __future__ import annotations

import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

# Без похожих друг на друга символов, чтобы код инвайта можно было продиктовать.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
INVITE_TTL_HOURS = 24

SCHEMA = """
CREATE TABLE IF NOT EXISTS households (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    title           TEXT    NOT NULL,
    spreadsheet_id  TEXT    NOT NULL,
    created_by      INTEGER NOT NULL,
    created_at      TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS users (
    telegram_id          INTEGER PRIMARY KEY,
    name                 TEXT    NOT NULL,
    active_household_id  INTEGER REFERENCES households(id),
    created_at           TEXT    NOT NULL
);

CREATE TABLE IF NOT EXISTS memberships (
    user_id      INTEGER NOT NULL,
    household_id INTEGER NOT NULL REFERENCES households(id),
    role         TEXT    NOT NULL,
    joined_at    TEXT    NOT NULL,
    PRIMARY KEY (user_id, household_id)
);

CREATE TABLE IF NOT EXISTS invites (
    code         TEXT    PRIMARY KEY,
    household_id INTEGER NOT NULL REFERENCES households(id),
    created_by   INTEGER NOT NULL,
    created_at   TEXT    NOT NULL,
    expires_at   TEXT    NOT NULL,
    used_by      INTEGER,
    used_at      TEXT
);
"""


@dataclass(frozen=True)
class Household:
    id: int
    title: str
    spreadsheet_id: str
    created_by: int


@dataclass(frozen=True)
class UserContext:
    telegram_id: int
    name: str
    household: Household | None
    role: str | None

    @property
    def is_owner(self) -> bool:
        return self.role == "owner"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Registry:
    """Техническая обвязка: кто к какой таблице подключён. Финансов здесь нет."""

    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(SCHEMA)
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    # --- пользователи -------------------------------------------------

    def touch_user(self, telegram_id: int, name: str) -> None:
        self._conn.execute(
            """
            INSERT INTO users (telegram_id, name, created_at) VALUES (?, ?, ?)
            ON CONFLICT(telegram_id) DO UPDATE SET name = excluded.name
            """,
            (telegram_id, name, _now()),
        )
        self._conn.commit()

    def get_context(self, telegram_id: int) -> UserContext | None:
        row = self._conn.execute(
            "SELECT telegram_id, name, active_household_id FROM users WHERE telegram_id = ?",
            (telegram_id,),
        ).fetchone()
        if row is None:
            return None

        household = None
        role = None
        if row["active_household_id"] is not None:
            household = self.get_household(row["active_household_id"])
            member = self._conn.execute(
                "SELECT role FROM memberships WHERE user_id = ? AND household_id = ?",
                (telegram_id, row["active_household_id"]),
            ).fetchone()
            role = member["role"] if member else None

        return UserContext(
            telegram_id=row["telegram_id"],
            name=row["name"],
            household=household,
            role=role,
        )

    # --- хозяйства ----------------------------------------------------

    def get_household(self, household_id: int) -> Household | None:
        row = self._conn.execute(
            "SELECT id, title, spreadsheet_id, created_by FROM households WHERE id = ?",
            (household_id,),
        ).fetchone()
        if row is None:
            return None
        return Household(
            id=row["id"],
            title=row["title"],
            spreadsheet_id=row["spreadsheet_id"],
            created_by=row["created_by"],
        )

    def find_household_by_spreadsheet(self, spreadsheet_id: str) -> Household | None:
        row = self._conn.execute(
            "SELECT id FROM households WHERE spreadsheet_id = ?", (spreadsheet_id,)
        ).fetchone()
        return self.get_household(row["id"]) if row else None

    def create_household(
        self, title: str, spreadsheet_id: str, owner_id: int
    ) -> Household:
        cursor = self._conn.execute(
            "INSERT INTO households (title, spreadsheet_id, created_by, created_at) VALUES (?, ?, ?, ?)",
            (title, spreadsheet_id, owner_id, _now()),
        )
        household_id = int(cursor.lastrowid)
        self._add_membership(owner_id, household_id, "owner")
        self._set_active(owner_id, household_id)
        self._conn.commit()
        household = self.get_household(household_id)
        assert household is not None
        return household

    def _add_membership(self, user_id: int, household_id: int, role: str) -> None:
        self._conn.execute(
            """
            INSERT INTO memberships (user_id, household_id, role, joined_at) VALUES (?, ?, ?, ?)
            ON CONFLICT(user_id, household_id) DO NOTHING
            """,
            (user_id, household_id, role, _now()),
        )

    def _set_active(self, user_id: int, household_id: int) -> None:
        self._conn.execute(
            "UPDATE users SET active_household_id = ? WHERE telegram_id = ?",
            (household_id, user_id),
        )

    def members(self, household_id: int) -> list[tuple[int, str, str]]:
        rows = self._conn.execute(
            """
            SELECT m.user_id, COALESCE(u.name, '?') AS name, m.role
            FROM memberships m LEFT JOIN users u ON u.telegram_id = m.user_id
            WHERE m.household_id = ?
            ORDER BY m.joined_at
            """,
            (household_id,),
        ).fetchall()
        return [(r["user_id"], r["name"], r["role"]) for r in rows]

    # --- инвайты ------------------------------------------------------

    def create_invite(self, household_id: int, created_by: int) -> tuple[str, datetime]:
        code = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
        expires = datetime.now(timezone.utc) + timedelta(hours=INVITE_TTL_HOURS)
        self._conn.execute(
            "INSERT INTO invites (code, household_id, created_by, created_at, expires_at) VALUES (?, ?, ?, ?, ?)",
            (code, household_id, created_by, _now(), expires.isoformat(timespec="seconds")),
        )
        self._conn.commit()
        return code, expires

    def redeem_invite(self, code: str, user_id: int) -> Household | str:
        """Возвращает Household при успехе или строку с причиной отказа."""
        code = code.strip().upper()
        row = self._conn.execute(
            "SELECT household_id, expires_at, used_by FROM invites WHERE code = ?", (code,)
        ).fetchone()
        if row is None:
            return "Код не найден. Проверь раскладку и пробелы."
        if row["used_by"] is not None:
            return "Этот код уже использован. Попроси новый."
        if datetime.fromisoformat(row["expires_at"]) < datetime.now(timezone.utc):
            return "Код просрочен, он живёт сутки. Попроси новый."

        household_id = int(row["household_id"])
        self._conn.execute(
            "UPDATE invites SET used_by = ?, used_at = ? WHERE code = ?",
            (user_id, _now(), code),
        )
        self._add_membership(user_id, household_id, "member")
        self._set_active(user_id, household_id)
        self._conn.commit()

        household = self.get_household(household_id)
        assert household is not None
        return household
