# tests/conftest.py
"""
Конфигурация pytest.

Добавляем корень проекта в sys.path, чтобы тесты могли
импортировать app.py, bot/bot_app.py и другие модули из корня репозитория.

Это нужно для корректной работы в CI, где cwd != PYTHONPATH.
"""

import sys
from pathlib import Path

# Корень проекта = родительская директория папки tests
PROJECT_ROOT = Path(__file__).resolve().parent.parent

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
