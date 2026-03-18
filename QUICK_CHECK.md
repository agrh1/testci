# ⚡ Быстрые команды для проверки

## Самый простой способ (Python)

```bash
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI

# Запустить все проверки одной командой
python check_syntax.py
```

---

## Альтернатива (Bash скрипт)

```bash
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI

# Сделать скрипт исполняемым и запустить
chmod +x check_syntax.sh
./check_syntax.sh
```

---

## Ручные команды (если хотите)

### 💻 Установить зависимости

```bash
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI

pip install -r requirements-dev.txt
```

### 🔍 Проверить синтаксис (Ruff)

```bash
# Все файлы в проекте
ruff check .

# Конкретные адаптеры
ruff check bot/adapters/
ruff check bot/adapters/base.py
ruff check bot/adapters/telegram.py
ruff check bot/adapters/mattermost.py

# Конфиг и БД
ruff check bot/config/settings.py
ruff check web/db.py
```

### 🔗 Проверить импорты

```bash
export PYTHONPATH="$PYTHONPATH:/Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI"

# По одному
python -c "from bot.adapters import UserIdentity; print('✓')"
python -c "from bot.adapters import TelegramMessageAdapter; print('✓')"
python -c "from bot.adapters import MattermostMessageAdapter; print('✓')"
python -c "from bot.config.settings import BotSettings; print('✓')"
python -c "from web.db import PlatformUser; print('✓')"

# Все вместе
python -c "from bot.adapters import *; from bot.config.settings import BotSettings; from web.db import *; print('✅ All imports OK')"
```

### 🧪 Запустить существующие тесты

```bash
# Все тесты
pytest tests/ -v

# Конкретный файл
pytest tests/test_app.py -v

# С показом вывода
pytest tests/ -v -s

# Остановиться на первой ошибке
pytest tests/ -x
```

### 📋 Форматировать код (auto-fix)

```bash
# Автоматически исправить стиль
ruff format .

# для конкретной папки
ruff format bot/adapters/
```

---

## 📊 Вывод проверки

Успешная проверка:
```
✓ All syntax checks passed
✓ All imports work
✓ All files compiled successfully
✅ READY FOR DEPLOYMENT
```

---

## 🎯 На один взгляд

| Команда | Используется | Для чего |
|---------|-------------|----------|
| `python check_syntax.py` | ⭐ ГЛАВНАЯ | Все проверки одной командой |
| `ruff check .` | Проверка синтаксиса | Поиск ошибок стиля |
| `ruff format .` | Автоисправление | Форматирование кода |
| `pytest tests/ -v` | Запуск тестов | Проверка логики |
| `python -c "from bot.adapters import *"` | Проверка импор тов | Убедиться что все импортируется |

