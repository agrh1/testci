# 🚀 Инструкция по развертыванию Mattermost интеграции

## Обзор
Этот документ описывает необходимые шаги для развертывания интеграции с Mattermost наряду с существующей поддержкой Telegram.

Миграция проходит в 4 фазы:
1. **Фаза 1** (текущий момент): Система работает только в Telegram режиме
2. **Фаза 2**: Обе платформы работают параллельно (DUAL_MODE)
3. **Фаза 3**: Telegram отключен, работает только Mattermost
4. **Фаза 4**: Telegram код удален из проекта

---

## ✅ Этап 1: Подготовка базы данных

### Шаг 1.1: Применить миграцию БД

На **production/staging сервере** выполните:

```bash
# Подключиться к PostgreSQL контейнеру
docker exec testci-postgres psql -U testci -d testci -f /migrations/002_platform_users.sql

# Или если у вас есть прямой доступ к БД:
psql postgresql://testci:PASSWORD@localhost:5432/testci \
  -f /path/to/web/migrations/002_platform_users.sql
```

**Что создается:**
- `platform_users` - Унифицированная таблица пользователей (Telegram + Mattermost)
- `platform_destinations` - Маршруты отправки уведомлений
- `platform_sync_log` - Аудит лог синхронизации

### Шаг 1.2: Проверить создание таблиц

```bash
psql postgresql://testci:PASSWORD@localhost:5432/testci << 'EOF'
SELECT table_name FROM information_schema.tables
WHERE table_schema = 'public'
AND table_name LIKE 'platform_%';
EOF
```

Вывод должен содержать:
```
        table_name
---------------------------
 platform_users
 platform_destinations
 platform_sync_log
```

---

## 🔧 Этап 2: Переменные окружения сервера

### ФАЗА 1: Текущая (только Telegram)

Эти значения **оставляйте как есть** (уже установлены в вашей системе):

```bash
# Telegram (существующее)
TELEGRAM_BOT_TOKEN=<ваш_текущий_токен>
TG_ENABLED=1

# Mattermost выключен
MATTERMOST_ENABLED=0
MATTERMOST_API_URL=https://mattermost.example.com
MATTERMOST_BOT_TOKEN=
MATTERMOST_WEBHOOK_SECRET=
```

### Добавить новые переменные окружения

В файл `.envs/.env.prod` (или `.env.staging`) добавьте:

```bash
# ============================================================================
# MATTERMOST: Feature flags (новое)
# ============================================================================

# Включить Telegram бота на этом сервере (1/0)
TG_ENABLED=1

# Включить Mattermost бота на этом сервере (1/0)
# На ФАЗЕ 1: оставить 0
# На ФАЗЕ 2: установить 1
# На ФАЗЕ 3: установить 1
MATTERMOST_ENABLED=0

# Режим параллельной отправки в обе платформы (1/0)
# На ФАЗЕ 1: оставить 0
# На ФАЗЕ 2: установить 1 (оба адаптера работают)
# На ФАЗЕ 3: оставить 0 (только Mattermost)
DUAL_MODE_ENABLED=0

# Какая платформа по умолчанию для команд
# На ФАЗЕ 1-2: оставить "telegram"
# На ФАЗЕ 3: установить "mattermost"
DEFAULT_PLATFORM=telegram

# ============================================================================
# MATTERMOST: API конфигурация (новое)
# ============================================================================

# Полный URL вашего Mattermost сервера
# Пример: https://mattermost.yourcompany.com или http://localhost:8065
MATTERMOST_API_URL=https://mattermost.yourcompany.com

# Personal Access Token (PAT) для бота Mattermost
# ВАЖНО: тип должен быть Bot PAT, а не User PAT
#
# Как получить:
# 1. Войдите в Mattermost как администратор
# 2. Перейдите: System Console → Integrations → Bot Accounts
# 3. Создайте нового бота с именем "testci-bot"
# 4. Выдайте ему разрешения: post:channels, create_post
# 5. Нажмите кнопку "Create Personal Access Token"
# 6. Скопируйте токен (выглядит как: x4dsf8y4nzx8dfhjsdhf8sdhf8sdhf8sdhf)
MATTERMOST_BOT_TOKEN=<скопировать_токен_из_системной_консоли>

# Webhook secret для входящих webhooks от ServiceDesk
# Это значение используется для валидации запросов от Mattermost
# Опционально (если не используются incoming webhooks)
MATTERMOST_WEBHOOK_SECRET=my-secret-webhook-key-12345
```

### Проверить переменные окружения

После добавления переменных, перезапустите бот контейнер:

```bash
docker-compose -f docker-compose.prod.yml down web bot
docker-compose -f docker-compose.prod.yml up -d web bot

# Проверить логи
docker logs testci-bot

# Должны увидеть:
# "Bot initialized with platforms: ['telegram']" (на ФАЗЕ 1)
# или
# "Bot initialized with platforms: ['telegram', 'mattermost']" (на ФАЗЕ 2)
```

---

## 🎯 Этап 3: Настройка Mattermost сервера

### Шаг 3.1: Создать бота в Mattermost

1. Войдите в Mattermost как администратор
2. Перейдите в **System Console** → **Integrations** → **Bot Accounts**
3. Нажмите кнопку **Create New Bot Account**
4. Заполните:
   - **Username**: `testci-bot` (или как угодно)
   - **Display Name**: `TestCI Notifications`
   - **Description**: `Bot for ServiceDesk notifications`

5. Нажмите **Create Bot Account**

### Шаг 3.2: Создать Personal Access Token

1. На странице бота нажмите **Create Personal Access Token**
2. Выберите **"Bot Account"** в выпадающем списке
3. Нажмите **Select a user** и выберите только что созданного бота
4. Нажмите **Create Token**
5. **Скопируйте токен** и сохраните его (он больше не будет видим!)

### Шаг 3.3: Выдать разрешения боту

В System Console → Roles:
- Найдите **System Manager** роль
- Убедитесь, что она содержит разрешения:
  - `create_post:channels` (создавать посты в каналах)
  - `create_post:private_channels` (создавать посты в приватных каналах)

Или используйте **Bot Role** если доступна.

### Шаг 3.4: Создать каналы для уведомлений

В вашем Mattermost сервере создайте каналы:

1. **#servicedesk-notifications** - основной канал уведомлений
2. **#servicedesk-escalations** - канал эскалаций
3. **#servicedesk-eventlog** - канал логов событий (опционально)

Пригласьте бота во все эти каналы:
- Откройте канал
- Нажмите на имя канала вверху
- **Members** → **Add Members** → выберите вашего бота

---

## 📊 Этап 4: Конфигурация маршрутизации

### Вариант A: Через web интерфейс (рекомендуется)

1. Откройте **GET /config** endpoint:
   ```
   http://your-server:8000/config?token=YOUR_CONFIG_TOKEN
   ```

2. Посмотрите текущий конфиг routing, например:
   ```json
   {
     "routing": {
       "rules": [
         {
           "service_id": 123,
           "dest": {
             "chat_id": -1001234567890,
             "thread_id": 456
           }
         }
       ],
       "default_dest": {
         "chat_id": -1001234567890,
         "thread_id": null
       }
     }
   }
   ```

3. На ФАЗЕ 2 (dual mode) обновите конфиг через **PUT /config**:
   ```json
   {
     "routing": {
       "rules": [
         {
           "service_id": 123,
           "destinations": [
             {
               "platform": "telegram",
               "chat_id": -1001234567890,
               "thread_id": 456
             },
             {
               "platform": "mattermost",
               "channel_id": "channelid123",
               "thread_id": null
             }
           ]
         }
       ]
     }
   }
   ```

### Вариант B: Через переменные окружения

На ФАЗЕ 1 используйте существующие env переменные:
```bash
ROUTES_RULES='[{"service_id": 123, "dest": {"chat_id": -1001234567890}}]'
ESCALATION_ENABLED=1
ESCALATION_DEST_CHAT_ID=-1001234567890
```

---

## 🔄 Этап 5: Миграция пользователей

### На ФАЗЕ 2: Link пользователей

После успешного запуска ФАЗЫ 2 (dual mode), нужно связать пользователей Telegram с Mattermost.

**Вариант 1: Интерактивное связывание через команду /link-mattermost**

Пользователь в Telegram выполняет:
```
/link-mattermost @mattermost_username
```

Система:
1. Проверяет что @mattermost_username существует
2. Записывает связь в `platform_users`
3. Логирует в `platform_sync_log`

**Вариант 2: Массовое связывание по email**

Создайте скрипт:
```python
import asyncio
from bot.services.user_store import UserStore
from web.db import PlatformUser, init_db, create_db_engine

async def migrate_users_by_email():
    """
    Связать пользователей Telegram с Mattermost по email.
    Требует что email есть в обоих системах.
    """
    engine = create_db_engine()
    init_db(engine)

    # Получить всех пользователей Telegram
    # Найти их Mattermost ID по email
    # Обновить platform_users
    pass

# Запустить: asyncio.run(migrate_users_by_email())
```

---

## 🧪 Этап 6: Тестирование

### На ФАЗЕ 1 (текущий момент)

Никаких действий не требуется. Система работает как обычно.

### На ФАЗЕ 2 (одновременная работа)

Когда переходите на ФАЗЕ 2:

```bash
# 1. Обновить env переменные:
MATTERMOST_ENABLED=1
DUAL_MODE_ENABLED=1

# 2. Перезапустить бот
docker-compose restart bot

# 3. Проверить логи
docker logs bot | grep -i mattermost

# 4. Проверить подключение к Mattermost API
# Должно быть сообщение: "Mattermost adapter initialized"

# 5. Отправить тестовое уведомление
# Оно должно появиться И в Telegram, И в Mattermost
```

### На ФАЗЕ 3 (только Mattermost)

Когда отключаете Telegram:

```bash
# 1. Обновить env переменные:
TG_ENABLED=0
MATTERMOST_ENABLED=1
DEFAULT_PLATFORM=mattermost

# 2. Перезапустить бот
docker-compose restart bot

# 3. Система должна работать только с Mattermost
```

---

## 🆘 Поиск неполадок

### Ошибка: "Invalid Mattermost token"

```
Error: 401 Unauthorized - Invalid token
```

**Решение:**
1. Проверьте что скопировали токен полностью (без пробелов)
2. Убедитесь что это Bot PAT, а не User PAT
3. Проверьте что боту выданы нужные разрешения в System Console
4. Перегенерируйте токен если нужно

### Ошибка: "Channel not found"

```
Error: 404 Not Found - Channel/team not found
```

**Решение:**
1. Проверьте что канал существует в Mattermost
2. Убедитесь что он правильно назван (channel_id, а не channel_name)
3. Приглаcите бота в канал (добавьте его как member)

### Ошибка: "Failed to migrate database"

```
Error: Table 'platform_users' already exists
```

**Решение:**
1. Таблицы уже созданы (это ОК)
2. Проверьте что структура совпадает с миграцией
3. Если нужно пересоздать: `DROP TABLE platform_users CASCADE; DROP TABLE platform_destinations CASCADE; DROP TABLE platform_sync_log;`

### Логи: "Adapter not initialized"

**Решение:**
1. Проверьте что MATTERMOST_ENABLED=1 установлен
2. Проверьте что MATTERMOST_API_URL и MATTERMOST_BOT_TOKEN заполнены
3. Проверьте доступ к Mattermost API

---

## 📋 Чеклист перед развертыванием

### ФАЗА 1 (текущий момент)
- [x] Миграция БД выполнена (`platform_users` таблицы созданы)
- [x] Адаптеры созданы (base.py, telegram.py, mattermost.py)
- [x] Settings.py обновлен
- [x] env_example обновлен

### ФАЗА 2 (после тестирования)
- [ ] Mattermost сервер настроен и доступен
- [ ] Bot аккаунт создан в Mattermost
- [ ] Personal Access Token получен и надежно хранится
- [ ] Каналы созданы: #servicedesk-notifications, etc.
- [ ] MATTERMOST_API_URL добавлен в env
- [ ] MATTERMOST_BOT_TOKEN добавлен в env (SECURELY!)
- [ ] MATTERMOST_ENABLED=1 установлен
- [ ] DUAL_MODE_ENABLED=1 установлен
- [ ] Бот перезапущен
- [ ] Тестовое уведомление отправлено в обе платформы

### ФАЗА 3 (после долгого тестирования)
- [ ] Все пользователи перелинкованы (TG ↔ MM)
- [ ] TG_ENABLED=0 установлен
- [ ] DEFAULT_PLATFORM=mattermost установлен
- [ ] Система протестирована только с Mattermost в течение 1 недели

### ФАЗА 4 (месяц спустя)
- [ ] Telegram код удален из опубликованного кода
- [ ] Старые migrations перемещены в archive

---

## 📞 Поддержка

Для вопросов:
1. Посмотрите логи: `docker logs testci-bot | grep -i error`
2. Проверьте конфигурацию: `echo $MATTERMOST_ENABLED && echo $MATTERMOST_API_URL`
3. Убедитесь что Mattermost доступен: `curl https://mattermost.yourcompany.com/api/v4/system/ping`
4. Проверьте firewall/NAT правила между сервером и Mattermost

---

## Дополнительные ресурсы

- Документация Mattermost API: https://developers.mattermost.com/apis/rest/
- Bot Accounts: https://developers.mattermost.com/integrate/admin-guide/admin-bot-accounts/
- Personal Access Tokens: https://developers.mattermost.com/integrate/reference/personal-access-token/
