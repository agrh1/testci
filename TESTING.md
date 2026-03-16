# 🧪 Инструкция по локальной проверке кода

## 1️⃣ Установка зависимостей

```bash
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI

# Установить все зависимости для разработки
pip install -r requirements-dev.txt

# Или отдельно:
pip install -r requirements.txt
pip install -r requirements-bot.txt
pip install -r requirements-web.txt
pip install pytest ruff
```

---

## 2️⃣ Проверка синтаксиса (Ruff)

Ruff - это быстрый линтер и форматер для Python.

### Проверить синтаксис всех файлов

```bash
# Проверить стиль кода (без автоисправления)
ruff check .

# Или расширено с деталями
ruff check . --show-source --show-fixes
```

### Автоисправить синтаксис

```bash
# Автоматически исправить форматирование
ruff format .

# Проверить что изменилось
ruff check . --fix
```

### Проверить конкретные файлы

```bash
# Проверить только наши адаптеры
ruff check bot/adapters/

# Проверить только веб-приложение
ruff check web/

# Проверить только тесты
ruff check tests/
```

###  Конфигурация Ruff

Настройки находятся в `pyproject.toml`:

```bash
cat pyproject.toml
```

---

## 3️⃣ Запуск тестов (Pytest)

### Запустить все тесты

```bash
# Основной способ
pytest

# С детальным выводом
pytest -v

# С показом стдаут
pytest -s

# С покрытием (если установлен pytest-cov)
pytest --cov=bot --cov=web tests/
```

### Запустить конкретные тесты

```bash
# Только web тесты
pytest tests/test_app.py -v

# Только bot тесты
pytest tests/test_bot.py -v

# По названию функции
pytest tests/test_bot.py::test_start_handler -v

# По паттерну
pytest tests/ -k "ping" -v
```

### Параметры Pytest

```bash
# -v: verbose (подробный вывод)
# -s: показать print и логи
# -x: остановиться на первой ошибке
# -k EXPRESSION: запустить только тесты с именем соответствующим EXPRESSION
# --tb=short: короткий traceback
# -n auto: запустить параллельно (если установлен pytest-xdist)
```

### Пример запуска

```bash
# Запустить тесты web с детальным выводом, стдаут и стоп на первой ошибке
pytest tests/test_app.py -v -s -x

# Запустить все тесты по health check
pytest -k health -v
```

---

## 4️⃣ Проверка синтаксиса наших новых файлов

```bash
# Проверить адаптеры
ruff check bot/adapters/ -v

# Проверить конфиг
ruff check bot/config/settings.py -v

# Проверить БД модели
ruff check web/db.py -v
```

### Интерпретировать ошибки Ruff

```bash
# Типичные ошибки:
E501   # Line too long (строка слишком длинная)
F401   # Unused import (неиспользуемый импорт)
F841   # Local variable assigned but never used
W292   # No newline at end of file
```

---

## 5️⃣ Проверка синтаксиса Python напрямую

```bash
# Проверить что файлы валидны (без запуска)
python -m py_compile bot/adapters/base.py
python -m py_compile bot/adapters/telegram.py
python -m py_compile bot/adapters/mattermost.py

# Проверить все файлы
python -c "
import py_compile
import os
for root, dirs, files in os.walk('bot/adapters'):
    for f in files:
        if f.endswith('.py'):
            try:
                py_compile.compile(os.path.join(root, f), doraise=True)
                print(f'✓ {os.path.join(root, f)}')
            except py_compile.PyCompileError as e:
                print(f'✗ {os.path.join(root, f)}: {e}')
"
```

---

## 6️⃣ Полная проверка перед коммитом

```bash
#!/bin/bash
# Сохранить это как script: check_all.sh

echo "=== Checking Python syntax with Ruff ==="
ruff check . --show-source

if [ $? -ne 0 ]; then
    echo "❌ Ruff check failed"
    exit 1
fi

echo ""
echo "=== Formatting with Ruff ==="
ruff format .

echo ""
echo "=== Running Pytest ==="
pytest tests/ -v

if [ $? -eq 0 ]; then
    echo ""
    echo "✅ All checks passed!"
else
    echo ""
    echo "❌ Some tests failed"
    exit 1
fi
```

Запустить:
```bash
chmod +x check_all.sh
./check_all.sh
```

---

## 7️⃣ Проверка импортов в наших файлах

```bash
# Убедиться что все импорты работают
python -c "from bot.adapters import TelegramMessageAdapter, MattermostMessageAdapter, UserIdentity; print('✓ Imports OK')"

# Проверить settings
python -c "from bot.config.settings import BotSettings; print('✓ Settings OK')"

# Проверить БД модели
python -c "from web.db import PlatformUser, PlatformDestination, PlatformSyncLog; print('✓ DB models OK')"
```

---

## 8️⃣ Запуск в Docker (если нужно)

```bash
# Запустить проверку syntax  в контейнере
docker run --rm -v $(pwd):/app python:3.11 bash -c "
    cd /app
    pip install -r requirements-dev.txt >/dev/null 2>&1
    ruff check . --show-source
    python -m pytest tests/ -v
"
```

---

## 🐛 Частые проблемы и решения

### Ошибка: "ModuleNotFoundError"

```bash
# Добавить проект в PYTHONPATH
export PYTHONPATH="${PYTHONPATH}:/Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI"

# Или запустить pytest из корня проекта
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI
pytest tests/
```

### Ошибка: "No module named pytest"

```bash
# Установить pytest
pip install pytest>=8.0
```

### Тесты используют Redis/PostgreSQL

Если тесты требуют БД:
```bash
# Запустить тестовые контейнеры
docker-compose -f docker-compose.local.yml up -d postgres redis

# После тестирования остановить
docker-compose -f docker-compose.local.yml down
```

---

## 📊 Результаты проверок

Успешная проверка выглядит так:

```
✓ Ruff check: 0 errors
✓ Pytest: all tests passed
✓ Coverage: 85%
```

Неудачная проверка:
```
✗ E501 line too long in bot/adapters/mattermost.py:45
✗ F401 unused import 'json' in bot/adapters/base.py:10
✗ test_send_message FAILED
```

---

## 📋 Краткая команда для быстрой проверки

```bash
# Одной командой все проверить (скопировать в терминал)
cd /Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI && \
echo "🔍 Checking syntax..." && ruff check . --show-source && \
echo "" && \
echo "📝 Formatting code..." && ruff format . && \
echo "" && \
echo "🧪 Running tests..." && pytest tests/ -v && \
echo "" && \
echo "✅ All checks passed!"
```

---

## 🎯 Рекомендуемый порядок перед пушем

1. **Проверить синтаксис**: `ruff check . --show-source`
2. **Автоформатировать**: `ruff format .`
3. **Запустить тесты**: `pytest tests/ -v -x`
4. **Проверить импорты**: `python -c "from bot.adapters import *"`
5. **Коммитить**: `git commit -m "..."`

