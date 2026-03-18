# 🚀 ФАЗА 2: Рефакторинг сервисов (Service Refactoring)

## 📊 Статус: В НАЧАЛЕ

Синхронизация существующего кода с платформо-независимыми адаптерами.

---

## 🎯 Приоритет 1 (НЕДЕЛЯ 1): Фундамент

### ✅ Задача 1.1: Расширить notify_router.py

**Текущее состояние:**
```python
@dataclass
class Destination:
    chat_id: int
    thread_id: Optional[int]
```

**Требуется:**
- Добавить `platform: str` field
- Добавить `destination_id: str` для MM channel_id
- Обновить все функции routing'а

**Файлы:**
- `/bot/utils/notify_router.py` - основной файл (extend ~50 lines)
- `/bot/utils/runtime_config.py` - обновить config структуру

---

### ⏳ Задача 1.2: Создать command_executor.py

**Новый файл:**
```
/bot/services/command_executor.py (400-500 lines)
```

**Компоненты:**
- `CommandRequest` - унифицированный запрос команды
- `CommandResponse` - унифицированный ответ
- `CommandExecutor` - базовый класс исполнителя
- `TelegramCommandExecutor` - Telegram-специфичный
- `MattermostCommandExecutor` - Mattermost-специфичный

**Зависимости:**
- `bot/adapters/*.py` - адаптеры (используются протоколы)
- `bot/config/settings.py` - конфигурация

---

### ⏳ Задача 1.3: Обновить bot_app.py

**Требуется:**
- Инициализировать `adapters: dict[str, MessageAdapter]`
- Создать `CommandExecutor` экземпляр
- Передать в `NotificationService`
- Передать в handlers через workflow_data

**Место:** `/bot/bot_app.py` (main функция, +80-100 lines)

---

## 🔨 Приоритет 2 (НЕДЕЛЯ 1-2): Основной сервис

### ⏳ Задача 2.1: Рефакторить notifications.py

**Текущие зависимости:**
```python
def __init__(self, bot: Bot, ...):  # Плохо!
```

**Новые зависимости:**
```python
def __init__(self, adapters: dict[str, MessageAdapter], ...):  # Хорошо!
```

**Функции для рефакторинга:**
- `notify_main()` - использовать адаптеры
- `notify_eventlog()` - platform-aware
- `notify_escalation()` - dual-mode support
- `_send_message_safe()` - делегировать адаптерам

**Файл:** `/bot/services/notifications.py` (+100-150 lines)

---

## 🐛 Приоритет 3 (НЕДЕЛЯ 2-3): Команды (САМАЯ БОЛЬШАЯ)

### ⏳ Задача 3.1-3.4: Рефакторить commands.py

**Всего команд:** 39 (в 4 фазах)

**Фаза 3a:** Notification-testing commands (4 команды)
- routes_test, routes_debug, routes_send_test, escalation_send_test

**Фаза 3b:** User management (6 команд)
- user_add, user_remove, admin_add, user_list, user_audit, user_history

**Фаза 3c:** Admin config (6 команд)
- config, config_diff, last_eventlog_id, eventlog_poll, service_icons, service_icon_add

**Фаза 3d:** Основные команды (15+ команд)
- start, help, ping, status, my_id, share_phone, save_contact, reset_password, get_link, все callback'и

**Файл:** `/bot/handlers/commands.py` (MAJOR refactor, 1942 -> ~2400 lines)

---

## 🧪 Приоритет 4 (НЕДЕЛЯ 3): Тестирование

### ⏳ Задача 4.1: Integration tests

- Telegram команды должны работать как раньше (backward compatible)
- Mattermost команды должны работать через адаптер
- Dual-mode: уведомления в обе платформы

---

## 📝 Рекомендуемый порядок выполнения

1. **День 1-2:** Задача 1.1 (notify_router.py)
2. **День 2-3:** Задача 1.2 (command_executor.py)
3. **День 3:** Задача 1.3 (bot_app.py init)
4. **День 4:** Задача 2.1 (notifications.py refactor)
5. **День 5-6:** Задача 3.1 (первые 4 команды)
6. **День 7+:** Задачи 3.2-3.4 (остальные команды постепенно)
7. **День последний:** Отладка и тестирование

---

## ⚠️ Критические замечания

1. **Backward compatibility:** Все изменения должны поддерживать ФАЗУ 1 (только Telegram)
2. **Feature flags:** MATTERMOST_ENABLED управляет инициализацией MM адаптера
3. **Graceful degradation:** Если адаптер недоступен, система должна работать
4. **Testing:** Каждая команда должна работать и в Telegram, и (потом) в MM

---

## 📂 Файлы которые нужно создать/изменить

### НОВЫЕ файлы:
- `/bot/services/command_executor.py`

### ИЗМЕНЯЕМЫЕ файлы (в порядке приоритета):
1. `/bot/utils/notify_router.py` (+50 lines)
2. `/bot/services/command_executor.py` (NEW, 400-500 lines)
3. `/bot/bot_app.py` (+100 lines refactor)
4. `/bot/services/notifications.py` (refactor +150 lines)
5. `/bot/handlers/commands.py` (refactor ~500 lines)

---

## 🎯 Что нужно от вас

Хотите начать с Приоритета 1?

- **Start immediately:** Начнём с notify_router.py
- **Review first:** Сначала покажу plan, потом начнём реализацию

Какой вариант предпочитаете? 🚀
