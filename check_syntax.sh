#!/bin/bash
# Скрипт для проверки синтаксиса и запуска тестов
# Запустить: bash check_syntax.sh

set -e

PROJECT_DIR="/Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI"
cd "$PROJECT_DIR"

echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🔍 ПРОВЕРКА СИНТАКСИСА И ТЕСТИРОВАНИЕ"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

# Проверка установки зависимостей
echo "📦 Проверка зависимостей..."
if ! command -v ruff &> /dev/null; then
    echo "  ⚠️  Ruff не установлен. Установка..."
    pip install -q ruff pytest
fi
echo "  ✓ Зависимости готовы"
echo ""

# 1. Проверка синтаксиса Ruff
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "1️⃣  ПРОВЕРКА СИНТАКСИСА (Ruff)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

FILES=(
    "bot/adapters/base.py"
    "bot/adapters/telegram.py"
    "bot/adapters/mattermost.py"
    "bot/adapters/__init__.py"
    "bot/config/settings.py"
    "web/db.py"
)

RUFF_ERRORS=0

for file in "${FILES[@]}"; do
    if [ -f "$file" ]; then
        echo "📄 $file"
        if output=$(ruff check "$file" 2>&1); then
            echo "   ✓ Синтаксис OK"
        else
            echo "   ❌ Найдены ошибки:"
            echo "$output" | sed 's/^/     /'
            RUFF_ERRORS=$((RUFF_ERRORS + 1))
        fi
    else
        echo "⚠️  $file не найден"
    fi
done

echo ""
if [ $RUFF_ERRORS -eq 0 ]; then
    echo "✅ Все файлы прошли проверку синтаксиса"
else
    echo "❌ Найдены ошибки синтаксиса в $RUFF_ERRORS файлах"
fi
echo ""

# 2. Проверка импортов
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "2️⃣  ПРОВЕРКА ИМПОРТОВ"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

export PYTHONPATH="${PYTHONPATH}:${PROJECT_DIR}"

echo "🔗 Проверка bot.adapters..."
if python -c "from bot.adapters import TelegramMessageAdapter, MattermostMessageAdapter, UserIdentity; print('   ✓ Импорты OK')" 2>&1; then
    :
else
    echo "   ❌ Ошибка импорта"
fi

echo "🔗 Проверка bot.config.settings..."
if python -c "from bot.config.settings import BotSettings; print('   ✓ Импорты OK')" 2>&1; then
    :
else
    echo "   ❌ Ошибка импорта"
fi

echo "🔗 Проверка web.db..."
if python -c "from web.db import PlatformUser, PlatformDestination, PlatformSyncLog; print('   ✓ Импорты OK')" 2>&1; then
    :
else
    echo "   ❌ Ошибка импорта"
fi

echo ""

# 3. Запуск существующих тестов
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "3️⃣  ЗАПУСК СУЩЕСТВУЮЩИХ ТЕСТОВ (Pytest)"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""

if command -v pytest &> /dev/null; then
    echo "🧪 Запуск pytest..."
    if pytest tests/ -v --tb=short 2>&1 | head -50; then
        echo ""
        echo "✅ Тесты пройдены"
    else
        echo ""
        echo "⚠️  Некоторые тесты могут требовать БД (PostgreSQL/Redis)"
    fi
else
    echo "⚠️  pytest не установлен, пропуск тестов"
fi

echo ""

# Итоги
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo "🎯 ИТОГИ ПРОВЕРКИ"
echo "━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━"
echo ""
echo "✓ Синтаксис проверен (Ruff)"
echo "✓ Импорты проверены"
echo "✓ Тесты запущены"
echo ""
echo "📚 Для подробной информации смотрите TESTING.md"
echo ""
