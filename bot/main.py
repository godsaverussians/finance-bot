from __future__ import annotations

import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.types import BotCommand

from .config import Config, load_config
from .handlers import routers
from .middleware import ContextMiddleware
from .registry import Registry
from .repository.sheets import SheetsFactory

logger = logging.getLogger(__name__)

COMMANDS = [
    BotCommand(command="start", description="Начало и подключение учёта"),
    BotCommand(command="add", description="Записать трату"),
    BotCommand(command="income", description="Записать доход"),
    BotCommand(command="last", description="Последние операции"),
    BotCommand(command="undo", description="Удалить последнюю"),
    BotCommand(command="repeat", description="Повторить последнюю"),
    BotCommand(command="status", description="Что подключено"),
    BotCommand(command="categories", description="Список категорий"),
    BotCommand(command="invite", description="Код для второго участника"),
    BotCommand(command="join", description="Присоединиться по коду"),
    BotCommand(command="cancel", description="Отменить текущее действие"),
]


def _build_factory(config: Config) -> SheetsFactory | None:
    """Без ключа бот всё равно поднимается и объясняет, чего не хватает."""
    if not config.has_credentials:
        logger.warning(
            "Ключ сервисного аккаунта не найден. Положи credentials.json в %s",
            config.data_dir,
        )
        return None
    try:
        factory = SheetsFactory(
            credentials_file=config.credentials_file,
            credentials_json=config.credentials_json,
        )
    except Exception:
        logger.exception("Ключ найден, но не читается")
        return None

    logger.info("Сервисный аккаунт: %s", factory.service_account_email)
    return factory


async def run() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
    )

    config = load_config()
    logger.info("Папка данных: %s", config.data_dir)
    logger.info("Реестр: %s", config.db_path)
    if config.bootstrap_code is None:
        logger.warning("BOOTSTRAP_CODE не задан — создать учёт может любой пользователь")

    registry = Registry(config.db_path)
    factory = _build_factory(config)

    bot = Bot(config.bot_token, default=DefaultBotProperties(parse_mode=None))
    dispatcher = Dispatcher()

    middleware = ContextMiddleware(config, registry, factory)
    dispatcher.message.middleware(middleware)
    dispatcher.callback_query.middleware(middleware)

    for router in routers:
        dispatcher.include_router(router)

    try:
        await bot.set_my_commands(COMMANDS)
        await bot.delete_webhook(drop_pending_updates=True)
        logger.info("Запускаю polling")
        await dispatcher.start_polling(bot)
    finally:
        registry.close()
        await bot.session.close()


def main() -> None:
    try:
        asyncio.run(run())
    except (KeyboardInterrupt, SystemExit):
        logging.info("Остановлен")


if __name__ == "__main__":
    main()
