from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

# На bothost эта папка не стирается при обновлении из Git.
PLATFORM_DATA_DIR = Path("/app/data")

# Платформа подставляет токен сама, под одним из этих имён.
TOKEN_ENV_NAMES = ("BOT_TOKEN", "API_TOKEN", "TELEGRAM_BOT_TOKEN")


@dataclass(frozen=True)
class Config:
    bot_token: str
    data_dir: Path
    db_path: Path
    credentials_file: Path | None
    credentials_json: str | None
    bootstrap_code: str | None
    allowed_user_ids: frozenset[int]
    timezone: str

    @property
    def has_credentials(self) -> bool:
        return self.credentials_file is not None or self.credentials_json is not None


def _first_env(*names: str) -> str:
    for name in names:
        value = os.getenv(name, "").strip()
        if value:
            return value
    return ""


def _resolve_data_dir() -> Path:
    explicit = os.getenv("DATA_DIR", "").strip()
    if explicit:
        return Path(explicit).expanduser()
    if PLATFORM_DATA_DIR.is_dir():
        return PLATFORM_DATA_DIR
    return Path("data")


def _read_side_file(data_dir: Path, filename: str) -> str | None:
    """Значение из файла в data-папке — замена переменным окружения на бесплатном тарифе."""
    path = data_dir / filename
    if not path.is_file():
        return None
    value = path.read_text(encoding="utf-8-sig").strip()
    return value or None


def _parse_ids(raw: str) -> frozenset[int]:
    ids = set()
    for chunk in raw.replace(";", ",").replace("\n", ",").split(","):
        chunk = chunk.strip()
        if chunk.lstrip("-").isdigit():
            ids.add(int(chunk))
    return frozenset(ids)


def _resolve_credentials(data_dir: Path) -> tuple[Path | None, str | None]:
    raw_json = os.getenv("GOOGLE_CREDENTIALS_JSON", "").strip()
    if raw_json:
        json.loads(raw_json)  # падаем сразу, если это не JSON
        return None, raw_json

    candidates = []
    explicit = os.getenv("GOOGLE_CREDENTIALS_FILE", "").strip()
    if explicit:
        candidates.append(Path(explicit).expanduser())
    candidates += [data_dir / "credentials.json", Path("credentials.json")]

    for candidate in candidates:
        if candidate.is_file():
            return candidate, None
    return None, None


def load_config() -> Config:
    load_dotenv()

    token = _first_env(*TOKEN_ENV_NAMES)
    data_dir = _resolve_data_dir()
    data_dir.mkdir(parents=True, exist_ok=True)

    if not token:
        token = _read_side_file(data_dir, "bot_token.txt") or ""
    if not token:
        raise RuntimeError(
            "Токен не найден. Локально: BOT_TOKEN в .env. "
            f"На хостинге: переменная BOT_TOKEN или файл {data_dir / 'bot_token.txt'}."
        )

    db_path_raw = os.getenv("DB_PATH", "").strip()
    db_path = Path(db_path_raw).expanduser() if db_path_raw else data_dir / "registry.db"

    credentials_file, credentials_json = _resolve_credentials(data_dir)

    bootstrap = os.getenv("BOOTSTRAP_CODE", "").strip() or _read_side_file(
        data_dir, "bootstrap.txt"
    )

    allowed_raw = os.getenv("ALLOWED_USER_IDS", "").strip()
    if not allowed_raw:
        allowed_raw = _read_side_file(data_dir, "allowed_users.txt") or ""

    return Config(
        bot_token=token,
        data_dir=data_dir,
        db_path=db_path,
        credentials_file=credentials_file,
        credentials_json=credentials_json,
        bootstrap_code=bootstrap,
        allowed_user_ids=_parse_ids(allowed_raw),
        timezone=os.getenv("TIMEZONE", "").strip() or "Europe/Moscow",
    )
