# Finance bot — этап 1a

Реестр, подключение таблицы, категории, приглашения. Внесение операций — следующий этап.

## Локальный запуск

```bash
cd finance-bot
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# в .env вписать BOT_TOKEN и BOOTSTRAP_CODE
# credentials.json положить рядом с .env

python -m bot.main
```

## Проверка

1. `/start` → «Создать учёт»
2. Ввести код из `BOOTSTRAP_CODE`
3. Прислать ссылку на таблицу
4. Выбрать базовый набор категорий
5. `/status` — должно показать таблицу и участников
6. Открыть таблицу: появились листы `transactions`, `categories`, `recurring`
7. `/invite` → код → девушка вводит `/join КОД`

## Что где

| Файл | Зачем |
|---|---|
| `bot/config.py` | настройки из `.env` |
| `bot/registry.py` | SQLite: кто к какой таблице подключён |
| `bot/repository/base.py` | интерфейс хранилища — точка замены Sheets на Postgres |
| `bot/repository/sheets.py` | реализация на Google Sheets |
| `bot/handlers/onboarding.py` | все команды этапа 1a |
| `bot/constants.py` | базовые категории, парсинг ссылок и списков |

## Деплой

См. `DEPLOY.md` — инструкция под bothost.ru.

## Полезное

- `/sa` — покажет email сервисного аккаунта, если забыл, кому давать доступ
- Категории правятся руками в листе `categories`, бот подхватит после `/categories`
- Суммы внутри кода — в копейках (`int`), в таблице — в рублях
- `credentials.json` и `.env` в git не попадают (см. `.gitignore`)
- На хостинге вместо `.env` используются файлы в `/app/data`: `bootstrap.txt`, при необходимости `bot_token.txt` и `allowed_users.txt`
