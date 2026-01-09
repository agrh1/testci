<!-- README.md — основная документация проекта. -->

# testCI

Проект состоит из web‑сервиса и Telegram‑бота для мониторинга очереди заявок ServiceDesk (IntraService).
Web отвечает за проксирование запросов к ServiceDesk и хранение runtime‑конфига, бот — за polling и отправку уведомлений в Telegram.

## Состав

- Web (Flask): /health, /ready, /status, /sd/open, /config*.
- Bot (aiogram): polling очереди, routing, escalation, admin‑алерты, eventlog.
- Postgres: хранение runtime‑конфига и истории, плюс база пользователей бота.
- Redis: state store для polling и эскалаций (fallback в память, если Redis нет).

## Ключевой функционал

- Получение открытых заявок ServiceDesk через /sd/open.
- Routing уведомлений по правилам и default‑destination.
- Эскалации при долгом ожидании.
- Обработка eventlog ServiceDesk с отдельной веткой маршрутизации.
- Хранение и версияция runtime‑конфига (/config).
- Админ‑алерты при деградации web/redis или проблемах routing.
- Автообработка заявок с категорией getlink_* (создание ссылок Seafile и скрытый комментарий).

## Быстрый старт (local)

1) Скопируйте шаблон окружения:

```bash
mkdir -p .envs
cp env_example .envs/.env.local
```

2) Заполните `.envs/.env.local` (см. раздел ниже).

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

- `ENVIRONMENT` — среда: `local|staging|prod`.
- `APP_ENV` — значение для `FLASK_ENV` в compose.
- `APP_VERSION` — версия образа для `docker-compose.prod.yml`.
- `GIT_SHA` — SHA коммита для /status.
- `LOG_LEVEL` — уровень логирования бота.
- `TZ` — таймзона контейнеров.
- `PORT` — порт web внутри контейнера (по умолчанию 8000).
- `APP_PORT` — порт публикации web на хосте (compose).

### Web + ServiceDesk

- `SERVICEDESK_BASE_URL` — корневой URL IntraService.
- `SERVICEDESK_LOGIN` / `SERVICEDESK_PASSWORD` — Basic Auth.
- `SERVICEDESK_TIMEOUT_S` — таймаут запросов к ServiceDesk.
- `TELEGRAM_BOT_TOKEN` — нужен web для readiness (проверка env).
- `STRICT_READINESS` — строгая проверка env в /ready (1/0).

### Bot ↔ Web

- `WEB_BASE_URL` — базовый URL web‑сервиса для бота.
- `WEB_TIMEOUT_S` — таймаут запросов к web (/health,/ready,/config).
- `WEB_CACHE_TTL_S` — TTL кэша проверок web.
- `SD_WEB_TIMEOUT_S` — таймаут запроса /sd/open.

### Runtime‑config (web /config)

- `CONFIG_URL` — полный URL до /config (по умолчанию `{WEB_BASE_URL}/config`).
- `CONFIG_TOKEN` — токен на чтение /config (X‑Config‑Token).
- `CONFIG_ADMIN_TOKEN` — токен админа для изменения /config (X‑Admin‑Token).
- `CONFIG_TTL_S` — TTL кэша конфига у бота.
- `CONFIG_TIMEOUT_S` — таймаут запроса /config.

### База данных (Postgres)

- `DATABASE_URL` — строка подключения (обязательна для бота).
- `POSTGRES_DB`, `POSTGRES_USER`, `POSTGRES_PASSWORD` — для compose‑контейнера.

### Redis

- `REDIS_URL` — если задан, state store будет в Redis.
- `REDIS_SOCKET_TIMEOUT_S`, `REDIS_CONNECT_TIMEOUT_S` — таймауты Redis.

### Polling и лимиты

- `POLL_INTERVAL_S` — интервал опроса очереди.
- `POLL_MAX_BACKOFF_S` — максимальный backoff при ошибках.
- `MIN_NOTIFY_INTERVAL_S` — минимальный интервал между уведомлениями.
- `MAX_ITEMS_IN_MESSAGE` — максимум заявок в одном сообщении.
- `GETLINK_POLL_INTERVAL_S` — интервал проверки заявок с getlink_*.
- `GETLINK_LOOKBACK_S` — окно поиска изменённых заявок (секунды).

### Eventlog

- `EVENTLOG_ENABLED` — включить обработку eventlog.
- `EVENTLOG_BASE_URL` — базовый URL (если отличается от ServiceDesk).
- `EVENTLOG_POLL_INTERVAL_S` — интервал опроса при отсутствии событий.
- `EVENTLOG_KEEPALIVE_EVERY` — через сколько циклов писать keep‑alive.
- `EVENTLOG_START_ID` — стартовый event_id (0 = последний существующий).

### Routing (fallback через env)

- `ROUTES_DEFAULT_CHAT_ID`, `ROUTES_DEFAULT_THREAD_ID` — destination по умолчанию.
- `ROUTES_SERVICE_ID_FIELD`, `ROUTES_CUSTOMER_ID_FIELD` — имена полей в заявке.
- `ROUTES_RULES` — JSON с правилами маршрутизации.

Пример `ROUTES_RULES`:

```json
[
  {
    "dest": {"chat_id": -100111, "thread_id": 10},
    "keywords": ["VIP", "P1"],
    "service_ids": [101, 102],
    "customer_ids": [5001]
  }
]
```

### Эскалации (fallback через env)

- `ESCALATION_ENABLED` — включить эскалацию (1/0).
- `ESCALATION_AFTER_S` — через сколько секунд эскалировать.
- `ESCALATION_DEST_CHAT_ID`, `ESCALATION_DEST_THREAD_ID` — destination эскалации.
- `ESCALATION_MENTION` — кого упомянуть.
- `ESCALATION_SERVICE_ID_FIELD`, `ESCALATION_CUSTOMER_ID_FIELD` — поля фильтра.
- `ESCALATION_FILTER` — JSON‑фильтр (keywords/service_ids/customer_ids).

Пример `ESCALATION_FILTER`:

```json
{
  "keywords": ["VIP", "P1"],
  "service_ids": [101, 102],
  "customer_ids": [5001]
}
```

### Eventlog routing (fallback через env)

- `EVENTLOG_DEFAULT_CHAT_ID`, `EVENTLOG_DEFAULT_THREAD_ID` — destination по умолчанию.
- `EVENTLOG_RULES` — JSON с правилами для eventlog (keywords).

Пример `EVENTLOG_RULES`:

```json
[
  {
    "dest": {"chat_id": -100222, "thread_id": 5},
    "keywords": ["Сбой", "Ошибка"]
  }
]
```

### Admin‑alerts и observability

- `ADMIN_ALERT_CHAT_ID`, `ADMIN_ALERT_THREAD_ID` — отдельный канал алертов.
- `ALERT_CHAT_ID`, `ALERT_THREAD_ID` — fallback, если `ADMIN_ALERT_*` не задан.
- `ADMIN_ALERT_MIN_INTERVAL_S` — rate‑limit алертов.
- `OBS_CHECK_INTERVAL_S` — интервал проверок деградации.
- `OBS_ROLLBACK_WINDOW_S`, `OBS_ROLLBACK_THRESHOLD` — параметры алертов rollback.
- `OBS_WEB_ALERT_MIN_INTERVAL_S`, `OBS_REDIS_ALERT_MIN_INTERVAL_S`, `OBS_ROLLBACK_ALERT_MIN_INTERVAL_S` — rate‑limit.

### Тесты

- `WEB_TEST_URL` — URL web для integration‑тестов.

## Работа с БД

### Web (runtime‑config)

Web хранит конфиг бота и историю версий в таблицах:

- `bot_config` — текущая версия (id=1).
- `bot_config_history` — история изменений и rollback.

Если `DATABASE_URL` не задан, web работает без БД и /config отдаёт fallback‑конфиг.

### Bot (user store)

Бот хранит пользователей в Postgres:

- `tg_users` — роли, профиль, последняя команда.
- `tg_command_history` — история команд.
- `tg_user_audit` — аудит админских действий.

`DATABASE_URL` обязателен для запуска бота.

Дополнительно:

- `seafile_services` — список Seafile сервисов для /get_link, /get_link_d и авто‑getlink (name/base_url/repo_id/auth_token/username/password/sd_category/enabled; sd_category в формате `id:name` или `id|name`).
- `eventlog_filters` — фильтры eventlog (enabled/match_type/field/pattern/hits).
- `service_icons` — значки сервисов по ServiceId (service_code/service_name/icon/enabled).

Пример значков сервисов (SQL):

```sql
INSERT INTO service_icons (service_id, service_code, service_name, icon, enabled)
VALUES
  (25, 'LENOVO', 'Lenovo Support', '❗', TRUE),
  (42, 'NET', 'Network Team', '🌐', TRUE);
```

Примеры команд (admin):

```
/service_icons
/service_icon_add 25 LENOVO ❗ Lenovo Support
```

### Бэкапы и перенос между БД

Ниже примеры для Postgres в контейнере (docker compose).

Полный бэкап БД:

```bash
docker compose -f prod/docker-compose.prod.yml exec -T postgres \
  pg_dump -U testci -d testci > /tmp/prod_full_dump.sql
```

Полный restore (осторожно, перезапишет данные):

```bash
docker compose -f prod/docker-compose.prod.yml exec -T postgres \
  psql -U testci -d testci < /tmp/prod_full_dump.sql
```

Перенос конфиг‑таблиц между БД (dump в формате INSERT, чтобы избежать COPY):

```bash
docker compose -f test/docker-compose.test.yml exec -T postgres \
  pg_dump -U testci -d testci --data-only --inserts --column-inserts \
  --table=bot_config --table=bot_config_history \
  --table=eventlog_filters --table=seafile_services \
  > /tmp/test_config_dump.sql
```

Создать таблицы в целевой БД (если ещё нет):

```bash
docker compose -f prod/docker-compose.prod.yml exec -T postgres psql -U testci -d testci <<'SQL'
CREATE TABLE IF NOT EXISTS eventlog_filters (
  id SERIAL PRIMARY KEY,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  match_type TEXT NOT NULL DEFAULT 'contains',
  field TEXT NOT NULL,
  pattern TEXT NOT NULL,
  comment TEXT,
  hits BIGINT NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE TABLE IF NOT EXISTS seafile_services (
  id SERIAL PRIMARY KEY,
  name TEXT NOT NULL,
  base_url TEXT NOT NULL,
  repo_id TEXT NOT NULL,
  auth_token TEXT,
  username TEXT,
  password TEXT,
  sd_category TEXT,
  enabled BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
SQL
```

Вариант A: перенос с очисткой (перезапись):

```bash
docker compose -f prod/docker-compose.prod.yml exec -T postgres \
  psql -U testci -d testci -c "TRUNCATE bot_config, bot_config_history, eventlog_filters, seafile_services RESTART IDENTITY;"

docker compose -f prod/docker-compose.prod.yml exec -T postgres \
  psql -U testci -d testci -v ON_ERROR_STOP=1 < /tmp/test_config_dump.sql
```

Вариант B: перенос без очистки (добавление):

```bash
docker compose -f prod/docker-compose.prod.yml exec -T postgres \
  psql -U testci -d testci -v ON_ERROR_STOP=1 < /tmp/test_config_dump.sql
```

Пример фильтров eventlog (SQL):

```sql
INSERT INTO eventlog_filters (enabled, match_type, field, pattern, comment)
VALUES
  (TRUE, 'contains', 'type',        'Информация. Сервисное обслуживание БД', 'legacy'),
  (TRUE, 'contains', 'description', 'Заявка не создана. Письмо распознано как служебное.', 'legacy'),
  (TRUE, 'contains', 'name',        'Пользователь Администратор удалил записи в таблицах: Task', 'legacy'),
  (TRUE, 'regex',    'name',        '^Профиль:.*', 'regex по названию');
```

Поддерживаемые поля `field`:
- `type` (Тип)
- `description` (Описание)
- `name` (Название)
- `date` (Дата)
- `any` или `*` (по всем полям)

Поддерживаемые типы `match_type`:
- `contains`
- `regex`

## Работа с Redis

Redis используется как state store. Ключи с префиксом `testci:`

- `bot:polling_state` — состояние polling.
- `bot:open_queue` — состояние очереди.
- `bot:escalation` — состояние эскалаций.
- `bot:eventlog` — last_event_id для eventlog.

Если `REDIS_URL` не задан, используется in‑memory хранилище (без сохранения между рестартами).

## Команды бота (user)

- `/get_link` — ссылка на загрузку логов (создаёт каталог при необходимости).
- `/get_link_d` — ссылка на скачивание логов из существующего каталога (срок 7 дней, пароль генерируется).

## /config: эндпоинты и примеры

### Команда бота /config

- `/config` — показать текущий конфиг.
- `/config ?` — справка по формату и полям.
- `/config <json>` — полностью заменить конфиг (bot делает PUT /config).

Важно: обновление полностью заменяет конфиг. Чтобы изменить одно поле —
сначала получите текущий `/config`, затем отредактируйте JSON.

### Схема конфига (полная форма)

Топ‑уровень:
- `routing` (обязательный)
- `escalation` (обязательный)
- `eventlog` (опционально, если нет — наследуется от routing)
- `version`, `source` (можно передавать, бот их удалит)

`routing`:
- `rules`: список правил (может быть `[]`)
  - `enabled` (bool, опционально)
  - `dest` (обязательный): `{chat_id, thread_id}`
  - `keywords` (list[str], опционально)
  - `service_ids` (list[int], опционально)
  - `customer_ids` (list[int], опционально)
- `default_dest`: `{chat_id, thread_id}` (опционально)
- `service_id_field` (string, опционально)
- `customer_id_field` (string, опционально)

`escalation`:
- `enabled` (bool)
- `after_s` (int, если enabled=true)
- `dest`: `{chat_id, thread_id}` (если enabled=true)
- `mention` (string, например `@duty_engineer`)
- `service_id_field` (string, опционально)
- `customer_id_field` (string, опционально)
- `filter` (опционально):
  - `keywords` (list[str])
  - `service_ids` (list[int])
  - `customer_ids` (list[int])

`eventlog`:
- `rules` (тот же формат, что и `routing.rules`)
- `default_dest`: `{chat_id, thread_id}` (опционально)
- `service_id_field` (string, опционально)
- `customer_id_field` (string, опционально)

### Получить конфиг

```bash
curl -s -H "X-Config-Token: <token>" http://localhost:8000/config
```

### Обновить конфиг

```bash
curl -s -X PUT \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <admin_token>" \
  -d '{"routing": {"rules": [], "default_dest": {"chat_id": -1001}}, "eventlog": {"rules": [], "default_dest": {"chat_id": -1001}}, "escalation": {"enabled": false}}' \
  http://localhost:8000/config
```

Пример расширенного конфига:

```json
{
  "version": 0,
  "routing": {
    "rules": [
      {
        "enabled": true,
        "dest": {"chat_id": -100111, "thread_id": 10},
        "keywords": ["VIP", "P1"],
        "service_ids": [101, 102],
        "customer_ids": [5001]
      }
    ],
    "default_dest": {"chat_id": -1001234567890, "thread_id": null},
    "service_id_field": "ServiceId",
    "customer_id_field": "CustomerId"
  },
  "eventlog": {
    "rules": [
      {
        "enabled": true,
        "dest": {"chat_id": -100222, "thread_id": 5},
        "keywords": ["Сбой", "Ошибка"],
        "service_ids": [101]
      }
    ],
    "default_dest": {"chat_id": -1001234567890, "thread_id": null},
    "service_id_field": "ServiceId",
    "customer_id_field": "CustomerId"
  },
  "escalation": {
    "enabled": true,
    "after_s": 900,
    "dest": {"chat_id": -100333, "thread_id": 2},
    "mention": "@duty_engineer",
    "filter": {
      "keywords": ["VIP", "P1"],
      "service_ids": [101]
    }
  }
}
```

### История и diff

```bash
curl -s -H "X-Admin-Token: <admin_token>" http://localhost:8000/config/history
curl -s -H "X-Admin-Token: <admin_token>" "http://localhost:8000/config/diff?from=1&to=2"
```

### Rollback

```bash
curl -s -X POST \
  -H "Content-Type: application/json" \
  -H "X-Admin-Token: <admin_token>" \
  -d '{"version": 2}' \
  http://localhost:8000/config/rollback
```

## Диагностика

- `GET /health` — быстрый health‑check.
- `GET /ready` — readiness с проверкой обязательных env.
- `GET /status` — ENVIRONMENT + GIT_SHA.
- Команда бота `/status` — состояние web/redis/config/polling.

## Тесты

```bash
pytest -q
```

Integration‑тесты запускаются только если задан `WEB_TEST_URL`:

```bash
WEB_TEST_URL=http://localhost:8000 pytest -q
```
