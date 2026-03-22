<!-- README.md — основная документация проекта. -->

# testCI

Проект состоит из web-сервиса и бота Mattermost для мониторинга очереди заявок ServiceDesk (IntraService).
Web отвечает за проксирование запросов к ServiceDesk и хранение runtime-конфига, бот — за polling и отправку уведомлений в Mattermost.

## Архитектура

```
┌─────────────┐     ┌──────────────┐     ┌──────────────────┐
│  Mattermost │◄────│  Bot         │────►│  Web (Flask)     │
│  Server     │     │  (asyncio)   │     │  /health /config │
└─────────────┘     │              │     │  /sd/open        │
                    │  ┌──────────┐│     └────────┬─────────┘
                    │  │ Workers: ││              │
                    │  │ polling  ││     ┌────────▼─────────┐
                    │  │ eventlog ││     │  ServiceDesk     │
                    │  │ getlink  ││     │  (IntraService)  │
                    │  └──────────┘│     └──────────────────┘
                    └──────┬───────┘
                           │
              ┌────────────┼────────────┐
              │            │            │
        ┌─────▼────┐ ┌────▼─────┐ ┌────▼─────┐
        │ Postgres │ │  Redis   │ │ Seafile  │
        │          │ │ (state)  │ │ (files)  │
        └──────────┘ └──────────┘ └──────────┘
```

### Компоненты

- **Web (Flask)**: `/health`, `/ready`, `/status`, `/sd/open`, `/config*`, `/users*`.
- **Bot (asyncio + mattermostdriver)**: polling очереди, routing, escalation, admin-алерты, eventlog, Seafile-ссылки.
- **Postgres**: runtime-конфиг и история, пользователи бота, Seafile-сервисы, eventlog-фильтры, иконки сервисов.
- **Redis**: state store для polling и эскалаций (fallback в память, если Redis недоступен).
- **Seafile**: файловое хранилище — создание директорий, upload/download ссылок.

## Ключевой функционал

- Получение открытых заявок ServiceDesk через `/sd/open`.
- Routing уведомлений по правилам и default-destination.
- Эскалации при долгом ожидании.
- Обработка eventlog ServiceDesk с отдельной веткой маршрутизации.
- Хранение и версионирование runtime-конфига (`/config`).
- Админ-алерты при деградации web/redis или проблемах routing.
- Автообработка заявок с категорией `getlink_*` (создание ссылок Seafile и скрытый комментарий).
- Ручное создание upload/download ссылок Seafile через команды бота.
- Просмотр открытых заявок SD через команду бота.

## Быстрый старт (local)

1) Скопируйте шаблон окружения:

```bash
mkdir -p .envs
cp env_example .envs/.env.local
```

2) Заполните `.envs/.env.local` (см. раздел «Переменные окружения»).

3) Запустите контейнеры:

```bash
docker compose -f docker-compose.local.yml up --build -d
```

4) Проверьте доступность web:

```bash
curl -s http://localhost:8000/health
```

## Переменные окружения

Шаблон с комментариями: `env_example`.

### Общие

| Переменная | Описание | По умолчанию |
|---|---|---|
| `ENVIRONMENT` | Среда: `local\|staging\|prod` | `prod` |
| `APP_ENV` | Значение для `FLASK_ENV` в compose | `prod` |
| `APP_VERSION` | Версия образа для compose | `0.0.0` |
| `GIT_SHA` | SHA коммита для `/status` | `unknown` |
| `LOG_LEVEL` | Уровень логирования бота | `INFO` |
| `TZ` | Таймзона контейнеров | `Europe/Moscow` |
| `PORT` | Порт web внутри контейнера | `8000` |
| `APP_PORT` | Порт публикации web на хосте (compose) | `8000` |

### Mattermost (обязательно)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `MATTERMOST_API_URL` | Полный URL Mattermost сервера | — (обязательно) |
| `MATTERMOST_BOT_TOKEN` | Personal Access Token бота | — (обязательно) |
| `MATTERMOST_WEBHOOK_SECRET` | Webhook secret (опционально) | — |
| `MATTERMOST_STARTUP_CHANNEL_ID` | Channel ID для стартового сообщения | `town-square` |

### Admin-алерты

| Переменная | Описание | По умолчанию |
|---|---|---|
| `ADMIN_ALERT_CHANNEL_ID` | Channel ID для админских алертов | — |
| `ADMIN_ALERT_THREAD_ID` | Thread ID алертов | — |
| `ALERT_CHANNEL_ID` | Fallback channel ID | — |
| `ALERT_THREAD_ID` | Fallback thread ID | — |
| `ADMIN_ALERT_MIN_INTERVAL_S` | Rate-limit алертов (сек) | `300` |

### Web + ServiceDesk

| Переменная | Описание | По умолчанию |
|---|---|---|
| `SERVICEDESK_BASE_URL` | Корневой URL IntraService | — (обязательно) |
| `SERVICEDESK_LOGIN` | Логин (Basic Auth) | — (обязательно) |
| `SERVICEDESK_PASSWORD` | Пароль (Basic Auth) | — (обязательно) |
| `SERVICEDESK_TIMEOUT_S` | Таймаут запросов к ServiceDesk | `10` |
| `STRICT_READINESS` | Строгая проверка env в `/ready` | `1` |

### Bot <-> Web

| Переменная | Описание | По умолчанию |
|---|---|---|
| `WEB_BASE_URL` | Базовый URL web-сервиса для бота | — |
| `WEB_TIMEOUT_S` | Таймаут запросов к web | `1.5` |
| `WEB_CACHE_TTL_S` | TTL кэша проверок web | `3.0` |
| `SD_WEB_TIMEOUT_S` | Таймаут запроса `/sd/open` | `3` |

### Runtime-конфиг (web /config)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `CONFIG_URL` | Полный URL до `/config` | `{WEB_BASE_URL}/config` |
| `CONFIG_TOKEN` | Токен на чтение `/config` (X-Config-Token) | — |
| `CONFIG_ADMIN_TOKEN` | Токен админа для изменения `/config` (X-Admin-Token) | — |
| `CONFIG_TTL_S` | TTL кэша конфига у бота | `60` |
| `CONFIG_TIMEOUT_S` | Таймаут запроса `/config` | `2.5` |

### База данных (Postgres)

| Переменная | Описание | По умолчанию |
|---|---|---|
| `DATABASE_URL` | Строка подключения (обязательна для бота) | — |
| `POSTGRES_DB` | Имя БД для compose-контейнера | `testci` |
| `POSTGRES_USER` | Пользователь БД | `testci` |
| `POSTGRES_PASSWORD` | Пароль БД | — |

### Redis

| Переменная | Описание | По умолчанию |
|---|---|---|
| `REDIS_URL` | URL Redis (если задан, state store в Redis) | — |
| `REDIS_SOCKET_TIMEOUT_S` | Таймаут сокета Redis | `1.0` |
| `REDIS_CONNECT_TIMEOUT_S` | Таймаут подключения Redis | `1.0` |

### Polling и лимиты

| Переменная | Описание | По умолчанию |
|---|---|---|
| `POLL_INTERVAL_S` | Интервал опроса очереди | `30` |
| `POLL_MAX_BACKOFF_S` | Максимальный backoff при ошибках | `300` |
| `MIN_NOTIFY_INTERVAL_S` | Минимальный интервал между уведомлениями | `60` |
| `MAX_ITEMS_IN_MESSAGE` | Максимум заявок в одном сообщении | `10` |
| `GETLINK_POLL_INTERVAL_S` | Интервал проверки заявок с `getlink_*` | `60` |
| `GETLINK_LOOKBACK_S` | Окно поиска изменённых заявок (секунды) | `120` |

### Eventlog

| Переменная | Описание | По умолчанию |
|---|---|---|
| `EVENTLOG_ENABLED` | Включить обработку eventlog (1/0) | `1` |
| `EVENTLOG_BASE_URL` | Базовый URL (если отличается от ServiceDesk) | — |
| `EVENTLOG_POLL_INTERVAL_S` | Интервал опроса при отсутствии событий | `600` |
| `EVENTLOG_KEEPALIVE_EVERY` | Через сколько циклов писать keep-alive | `48` |
| `EVENTLOG_START_ID` | Стартовый event_id (0 = последний существующий) | `0` |

Поведение eventlog-воркера:
- `last_event_id` хранится в state store (`bot:eventlog`) и может быть изменён на лету через `/last_eventlog_id set <id>`.
- Воркер перечитывает `last_event_id` на каждом цикле и подхватывает новое значение (включая «откат» назад).
- Мягкий догон: если подряд 3 раза нет события для `next_id`, воркер проверяет `last_item` и прыгает к нему.

### Routing (Mattermost destinations)

| Переменная | Описание |
|---|---|
| `ROUTES_DEFAULT_DESTINATION_ID` | Channel ID назначения по умолчанию |
| `ROUTES_DEFAULT_THREAD_ID` | Thread ID по умолчанию |
| `ROUTES_SERVICE_ID_FIELD` | Имя поля ServiceId в заявке |
| `ROUTES_CUSTOMER_ID_FIELD` | Имя поля CustomerId в заявке |
| `ROUTES_CREATOR_ID_FIELD` | Имя поля CreatorId в заявке |
| `ROUTES_CREATOR_COMPANY_ID_FIELD` | Имя поля CreatorCompanyId |
| `ROUTES_RULES` | JSON-правила маршрутизации |

Пример `ROUTES_RULES`:

```json
[
  {
    "dest": {"destination_id": "channel_id_here", "thread_id": "root_post_id"},
    "keywords": ["VIP", "P1"],
    "service_ids": [101, 102],
    "customer_ids": [5001]
  }
]
```

### Эскалации

| Переменная | Описание |
|---|---|
| `ESCALATION_ENABLED` | Включить эскалацию (1/0) |
| `ESCALATION_AFTER_S` | Через сколько секунд эскалировать |
| `ESCALATION_DEST_DESTINATION_ID` | Channel ID назначения эскалации |
| `ESCALATION_DEST_THREAD_ID` | Thread ID эскалации |
| `ESCALATION_MENTION` | Базовый mention (`@duty_engineer`) |
| `ESCALATION_SERVICE_ID_FIELD` | Поле ServiceId |
| `ESCALATION_CUSTOMER_ID_FIELD` | Поле CustomerId |
| `ESCALATION_FILTER` | JSON-фильтр |

### Eventlog routing

| Переменная | Описание |
|---|---|
| `EVENTLOG_DEFAULT_DESTINATION_ID` | Channel ID по умолчанию |
| `EVENTLOG_DEFAULT_THREAD_ID` | Thread ID по умолчанию |
| `EVENTLOG_RULES` | JSON с правилами для eventlog |

### Observability

| Переменная | Описание | По умолчанию |
|---|---|---|
| `OBS_CHECK_INTERVAL_S` | Интервал проверок деградации | `60` |
| `OBS_ROLLBACK_WINDOW_S` | Окно для подсчёта rollback | `3600` |
| `OBS_ROLLBACK_THRESHOLD` | Порог алерта rollback | `3` |
| `OBS_WEB_ALERT_MIN_INTERVAL_S` | Rate-limit web-алерта | `300` |
| `OBS_REDIS_ALERT_MIN_INTERVAL_S` | Rate-limit Redis-алерта | `300` |
| `OBS_ROLLBACK_ALERT_MIN_INTERVAL_S` | Rate-limit rollback-алерта | `300` |

### Тесты

| Переменная | Описание |
|---|---|
| `WEB_TEST_URL` | URL web для integration-тестов |

## Миграция с Telegram на Mattermost

В этом релизе код Telegram полностью удалён. Все уведомления и команды работают только через Mattermost.

### Что изменилось

**Удалено:**
- `TELEGRAM_BOT_TOKEN` — больше не нужен
- `TG_ADMINS`, `TG_USERS` — управление пользователями теперь через БД
- `ROUTES_DEFAULT_CHAT_ID` -> `ROUTES_DEFAULT_DESTINATION_ID`
- `EVENTLOG_DEFAULT_CHAT_ID` -> `EVENTLOG_DEFAULT_DESTINATION_ID`
- `ESCALATION_DEST_CHAT_ID` -> `ESCALATION_DEST_DESTINATION_ID`
- `ADMIN_ALERT_CHAT_ID` -> `ADMIN_ALERT_CHANNEL_ID`
- Все ссылки на `chat_id` в JSON-правилах -> `destination_id`

**Добавлено:**
- `MATTERMOST_API_URL` — URL Mattermost сервера (обязательно)
- `MATTERMOST_BOT_TOKEN` — токен бота Mattermost (обязательно)
- `MATTERMOST_WEBHOOK_SECRET` — webhook secret (опционально)
- `MATTERMOST_STARTUP_CHANNEL_ID` — канал стартового сообщения

**Изменение формата правил routing/escalation:**

Было (Telegram):
```json
{"dest": {"chat_id": -1001234, "thread_id": 10}}
```

Стало (Mattermost):
```json
{"dest": {"destination_id": "abc123channelid", "thread_id": "root_post_id"}}
```

### Миграция БД

Старые таблицы `tg_users`, `tg_command_history`, `tg_user_audit` заменены на:
- `platform_users` — роли, профиль, последняя команда
- `mm_command_history` — история команд
- `mm_user_audit` — аудит админских действий

Таблицы создаются автоматически при старте бота (`UserStore.init_schema()`).

### Настройка бота Mattermost

1. Создайте бота в Mattermost: **System Console -> Integrations -> Bot Accounts**.
2. Получите Personal Access Token.
3. Установите `MATTERMOST_API_URL` и `MATTERMOST_BOT_TOKEN` в `.env`.
4. Добавьте первого админа:

```sql
INSERT INTO platform_users (mattermost_user_id, role, created_at, updated_at)
VALUES ('ваш_mattermost_user_id', 'admin', now(), now());
```

5. Узнать свой `user_id` можно командой `/whoami` (если уже добавлены как user) или в Mattermost: **Profile -> Advanced -> User ID**.

## Команды бота

### Пользовательские

| Команда | Описание |
|---|---|
| `/ping` | Проверка доступности бота |
| `/whoami` | Кто я: ID, username, имя, email, роль |
| `/whereami` | Где я: ID и название текущего канала и team |
| `/sd_open [limit]` | Список открытых заявок SD (до 50) |
| `/get_link <task_id> [service_id]` | Создать upload-ссылку Seafile |
| `/get_link_d <task_id> [service_id]` | Создать download-ссылку Seafile |
| `/user_list [admins\|users]` | Список пользователей |
| `/user_history <id> [limit]` | История команд пользователя |
| `/user_audit <id> [limit]` | Audit-история |
| `/help_mattermost` | Полная справка по командам |

При вызове `/get_link` или `/get_link_d` без `service_id`:
- Если один Seafile-сервис — используется автоматически.
- Если несколько — бот покажет список доступных ресурсов для выбора.

### Админские

| Команда | Описание |
|---|---|
| `/status` | Подробный статус бота: config, eventlog, state store |
| `/routes_test name="..." service_id=101` | Тест маршрутизации (без отправки) |
| `/routes_debug name="..."` | Подробный отладочный маршрутинг |
| `/routes_send_test name="..."` | Отправить тестовое уведомление по маршрутам |
| `/escalation_send_test name="..."` | Тест эскалации с реальной отправкой |
| `/user_add <id>` | Добавить пользователя |
| `/user_remove <id>` | Удалить пользователя |
| `/admin_add <id>` | Добавить админа |
| `/config [? \| check \| reload \| json]` | Управление конфигом |
| `/config_diff <from> <to>` | Diff между версиями конфига |
| `/last_eventlog_id [set <id>]` | Показать/установить последний eventlog ID |
| `/eventlog_poll` | Принудительный одиночный прогон eventlog |
| `/eventlog_filters` | Показать активные фильтры eventlog |
| `/service_icons` | Показать значки сервисов |
| `/service_icon_add <id> <code> <icon>` | Добавить значок сервиса |

## Работа с БД

### Web (runtime-config)

Web хранит конфиг бота и историю версий в таблицах:

- `bot_config` — текущая версия (id=1).
- `bot_config_history` — история изменений и rollback.

Если `DATABASE_URL` не задан, web работает без БД и `/config` отдаёт fallback-конфиг.

### Bot

Бот хранит данные в Postgres (таблицы создаются автоматически):

- `platform_users` — mattermost_user_id, роль (admin/user), профиль, последняя команда.
- `mm_command_history` — история команд.
- `mm_user_audit` — аудит админских действий.
- `seafile_services` — Seafile-сервисы (name/base_url/repo_id/auth_token/sd_category/enabled).
- `eventlog_filters` — фильтры eventlog (enabled/match_type/field/pattern/hits).
- `service_icons` — значки сервисов по ServiceId.

`DATABASE_URL` обязателен для запуска бота.

### Seafile-сервисы

Пример добавления Seafile-сервиса (SQL):

```sql
INSERT INTO seafile_services (name, base_url, repo_id, auth_token, sd_category, enabled)
VALUES
  ('sf.example.com', 'https://sf.example.com', 'repo-uuid-here', 'Token xxx', '110:getlink_uploads', TRUE);
```

Поле `sd_category` в формате `id:name` или `id|name` — используется для автоматической привязки заявок с getlink-категорией.

### Eventlog-фильтры

Пример (SQL):

```sql
INSERT INTO eventlog_filters (enabled, match_type, field, pattern, comment)
VALUES
  (TRUE, 'contains', 'type',        'Информация. Сервисное обслуживание БД', 'legacy'),
  (TRUE, 'regex',    'name',        '^Профиль:.*', 'regex по названию');
```

Поддерживаемые поля `field`: `type`, `description`, `name`, `date`, `any`/`*` (по всем полям).
Типы `match_type`: `contains`, `regex`.

### Значки сервисов

```sql
INSERT INTO service_icons (service_id, service_code, service_name, icon, enabled)
VALUES
  (25, 'LENOVO', 'Lenovo Support', '❗', TRUE),
  (42, 'NET', 'Network Team', '🌐', TRUE);
```

### Бэкапы и перенос между БД

Полный бэкап:

```bash
docker compose exec -T postgres pg_dump -U testci -d testci > /tmp/full_dump.sql
```

Выборочный дамп конфиг-таблиц:

```bash
docker compose exec -T postgres \
  pg_dump -U testci -d testci --data-only --inserts --column-inserts \
  --table=bot_config --table=bot_config_history \
  --table=eventlog_filters --table=seafile_services \
  --table=service_icons \
  > /tmp/config_dump.sql
```

Restore:

```bash
docker compose exec -T postgres \
  psql -U testci -d testci -v ON_ERROR_STOP=1 < /tmp/config_dump.sql
```

## Работа с Redis

Redis используется как state store. Ключи с префиксом `testci:`:

- `bot:open_queue` — состояние очереди.
- `bot:escalation` — состояние эскалаций.
- `bot:eventlog` — `last_event_id` для eventlog.

Если `REDIS_URL` не задан, используется in-memory хранилище (без сохранения между рестартами).

## /config: управление runtime-конфигом

### Команда бота

- `/config` — показать текущий конфиг.
- `/config ?` — справка.
- `/config check` — краткая сводка.
- `/config reload` — принудительно перезагрузить из web.
- `/config <json>` — полностью заменить конфиг (PUT /config).

Обновление полностью заменяет конфиг. Чтобы изменить одно поле — получите текущий `/config`, отредактируйте JSON, отправьте обратно.

### Схема конфига

Топ-уровень: `routing` (обязательный), `escalation` (обязательный), `eventlog` (опционально).

`routing`:
- `rules`: список правил
  - `name`, `enabled` (опционально)
  - `dest`: `{"destination_id": "...", "thread_id": "..."}` (обязательно)
  - `keywords`, `service_ids`, `customer_ids`, `creator_ids`, `creator_company_ids` (опционально)
- `default_dest`: `{"destination_id": "...", "thread_id": "..."}` (опционально)
- `service_id_field`, `customer_id_field`, `creator_id_field`, `creator_company_id_field` (опционально)

`escalation`:
- `enabled` (bool), `after_s` (int), `mention` (string)
- `rules` (опционально): список правил с переопределениями `dest`, `mention`, `after_s`
- `service_id_field`, `customer_id_field` и т.д. (опционально)

`eventlog`: тот же формат, что и `routing`.

### HTTP API

```bash
# Получить конфиг
curl -s -H "X-Config-Token: <token>" http://localhost:8000/config

# Обновить конфиг
curl -s -X PUT \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <admin_token>" \
  -d '{"routing": {"rules": [], "default_dest": {"destination_id": "ch_id"}}, "escalation": {"enabled": false}}' \
  http://localhost:8000/config

# История версий
curl -s -H "X-Admin-Token: <admin_token>" http://localhost:8000/config/history

# Diff между версиями
curl -s -H "X-Admin-Token: <admin_token>" "http://localhost:8000/config/diff?from=1&to=2"

# Rollback
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <admin_token>" \
  -d '{"version": 2}' \
  http://localhost:8000/config/rollback
```

## Структура проекта

```
├── bot/
│   ├── adapters/
│   │   ├── base.py                 # Базовые интерфейсы адаптеров
│   │   ├── mattermost.py           # REST API адаптер (отправка сообщений)
│   │   └── mattermost_bot.py       # WebSocket адаптер (приём команд)
│   ├── bot_app.py                  # Главная точка сборки бота
│   ├── config/
│   │   └── settings.py             # BotSettings из env
│   ├── handlers/
│   │   └── command_implementations.py  # Все команды бота
│   ├── services/
│   │   ├── command_executor.py     # Маршрутизация команд + DI
│   │   ├── config_sync.py          # Синхронизация runtime-конфига
│   │   ├── eventlog_filter_store.py # Фильтры eventlog (Postgres)
│   │   ├── eventlog_worker.py      # Фоновый воркер eventlog
│   │   ├── getlink_worker.py       # Автосоздание Seafile-ссылок
│   │   ├── notifications.py        # Отправка уведомлений
│   │   ├── observability.py        # Мониторинг и алерты
│   │   ├── seafile_store.py        # Seafile-сервисы (Postgres)
│   │   ├── service_icon_store.py   # Иконки сервисов (Postgres)
│   │   └── user_store.py           # Пользователи бота (Postgres)
│   └── utils/
│       ├── admin_alerts.py         # Форматирование алертов
│       ├── config_client.py        # HTTP-клиент /config
│       ├── env_helpers.py          # Парсеры env-переменных
│       ├── escalation.py           # Логика эскалаций
│       ├── eventlog.py             # Парсер eventlog (HTML)
│       ├── notify_router.py        # Маршрутизация уведомлений
│       ├── polling.py              # Polling очереди заявок
│       ├── runtime_config.py       # In-memory runtime-конфиг
│       ├── sd_api_client.py        # Прямой API ServiceDesk
│       ├── sd_state.py             # Состояние очереди
│       ├── sd_web_client.py        # Клиент /sd/open
│       ├── seafile_client.py       # API Seafile
│       ├── state_store.py          # Redis/Memory state store
│       ├── web_client.py           # HTTP-клиент web-сервиса
│       └── web_guard.py            # Проверка web-готовности
├── web/
│   ├── app.py                      # Flask-приложение
│   ├── config_validation.py        # Валидация runtime-конфига
│   ├── db.py                       # SQLAlchemy модели
│   ├── settings.py                 # Настройки web из env
│   ├── migrations/                 # SQL-миграции
│   └── routes/
│       ├── config.py               # /config endpoints
│       └── users.py                # /users endpoints
├── tests/                          # Pytest тесты
├── .github/workflows/              # CI/CD
│   ├── ci.yml                      # Линтинг + тесты
│   ├── deploy-staging.yml          # Деплой в staging
│   ├── deploy-prod.yml             # Деплой в prod
│   └── release.yml                 # Релиз
├── docker-compose.local.yml
├── docker-compose.staging.yml
├── docker-compose.prod.yml
├── Dockerfile.bot
├── Dockerfile.web
├── requirements.txt                # Web dependencies
├── requirements-bot.txt            # Bot dependencies
└── env_example                     # Шаблон переменных окружения
```

## Диагностика

- `GET /health` — быстрый health-check.
- `GET /ready` — readiness с проверкой обязательных env.
- `GET /status` — ENVIRONMENT + GIT_SHA.
- Команда бота `/ping` — проверка доступности бота.
- Команда бота `/status` — состояние config/eventlog/state store.

## Тесты

```bash
# Линтинг
ruff check .

# Юнит-тесты
pytest -q

# Integration-тесты (нужен запущенный web)
WEB_TEST_URL=http://localhost:8000 pytest -q
```
