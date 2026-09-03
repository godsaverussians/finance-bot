from __future__ import annotations

from typing import Any, Awaitable, Callable

from aiogram import BaseMiddleware
from aiogram.types import CallbackQuery, Message, TelegramObject, User

from .config import Config
from .registry import Registry
from .repository.sheets import SheetsFactory


class ContextMiddleware(BaseMiddleware):
    """Кладёт в data зависимости и контекст пользователя."""

    def __init__(
        self, config: Config, registry: Registry, factory: SheetsFactory | None
    ) -> None:
        self._config = config
        self._registry = registry
        self._factory = factory

    async def __call__(
        self,
        handler: Callable[[TelegramObject, dict[str, Any]], Awaitable[Any]],
        event: TelegramObject,
        data: dict[str, Any],
    ) -> Any:
        user: User | None = data.get("event_from_user")

        data["config"] = self._config
        data["registry"] = self._registry
        data["factory"] = self._factory

        if user is None or user.is_bot:
            return await handler(event, data)

        if self._factory is None:
            text = (
                "Не найден ключ доступа к Google. Положи credentials.json "
                f"в папку {self._config.data_dir} и перезапусти бота."
            )
            if isinstance(event, Message):
                await event.answer(text)
            elif isinstance(event, CallbackQuery):
                await event.answer("Нет ключа Google, смотри логи", show_alert=True)
            return None

        allowed = self._config.allowed_user_ids
        if allowed and user.id not in allowed:
            text = "Бот приватный. Если тебе дали код приглашения — попроси владельца добавить твой ID."
            if isinstance(event, Message):
                await event.answer(f"{text}\n\nТвой ID: `{user.id}`", parse_mode="Markdown")
            elif isinstance(event, CallbackQuery):
                await event.answer(text, show_alert=True)
            return None

        display_name = user.full_name or user.username or str(user.id)
        self._registry.touch_user(user.id, display_name)
        context = self._registry.get_context(user.id)
        data["user_context"] = context
        data["repo"] = (
            self._factory.repository(context.household.spreadsheet_id)
            if context and context.household
            else None
        )

        return await handler(event, data)
