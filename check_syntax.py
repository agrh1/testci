#!/usr/bin/env python3
"""
Скрипт для проверки синтаксиса и импортов новых файлов.
Запуск: python check_syntax.py
"""

import os
import subprocess
import sys
from pathlib import Path

# Установить корректный путь проекта
PROJECT_DIR = Path("/Users/alexeyrukavishnikov/Documents/pyProjects/_tests/testCI")
os.chdir(PROJECT_DIR)
sys.path.insert(0, str(PROJECT_DIR))

def print_header(text):
    """Печать красивого заголовка"""
    print("\n" + "="*60)
    print(f"  {text}")
    print("="*60 + "\n")

def print_success(text):
    """Печать успешного сообщения"""
    print(f"  ✓ {text}")

def print_error(text):
    """Печать сообщения об ошибке"""
    print(f"  ❌ {text}")

def print_warning(text):
    """Печать предупреждения"""
    print(f"  ⚠️  {text}")

def check_file_exists(filepath):
    """Проверить что файл существует"""
    path = PROJECT_DIR / filepath
    if path.exists():
        print_success(f"Found: {filepath}")
        return True
    else:
        print_error(f"Not found: {filepath}")
        return False

def check_syntax_with_ruff(filepath):
    """Проверить синтаксис с помощью Ruff"""
    try:
        result = subprocess.run(
            ["ruff", "check", str(filepath)],
            capture_output=True,
            text=True,
            timeout=10
        )
        if result.returncode == 0:
            print_success(f"Syntax OK: {filepath}")
            return True
        else:
            print_error(f"Syntax errors in: {filepath}")
            if result.stdout:
                print(f"    {result.stdout}")
            return False
    except FileNotFoundError:
        print_warning("Ruff not installed, installing...")
        subprocess.run([sys.executable, "-m", "pip", "install", "-q", "ruff"])
        return check_syntax_with_ruff(filepath)
    except subprocess.TimeoutExpired:
        print_warning(f"Timeout checking {filepath}")
        return False
    except Exception as e:
        print_warning(f"Error checking {filepath}: {e}")
        return False

def check_python_compile(filepath):
    """Проверить что файл компилируется"""
    try:
        import py_compile
        py_compile.compile(str(filepath), doraise=True)
        print_success(f"Compiles: {filepath}")
        return True
    except Exception as e:
        print_error(f"Compilation error in {filepath}: {e}")
        return False

def check_imports():
    """Проверить импорты"""
    print_header("2️⃣  ПРОВЕРКА ИМПОРТОВ")

    imports_to_check = [
        ("bot.adapters", ["MattermostMessageAdapter", "UserIdentity"]),
        ("bot.config.settings", ["BotSettings"]),
        ("web.db", ["PlatformUser", "PlatformDestination", "PlatformSyncLog"]),
        ("bot.adapters.base", ["MessageAdapter", "StateManager"]),
    ]

    success_count = 0
    for module_name, items in imports_to_check:
        try:
            module = __import__(module_name, fromlist=items)
            for item in items:
                if hasattr(module, item):
                    print_success(f"Import OK: {module_name}.{item}")
                    success_count += 1
                else:
                    print_error(f"Not found: {module_name}.{item}")
        except ImportError as e:
            print_error(f"Cannot import {module_name}: {e}")
        except Exception as e:
            print_error(f"Error importing {module_name}: {e}")

    return success_count == len(imports_to_check) * 2

def main():
    """Основная функция"""
    print("\n" + "█"*60)
    print("█  🔍 ПРОВЕРКА СИНТАКСИСА И ИМПОРТОВ")
    print("█"*60)

    # 1. Проверка файлов
    print_header("1️⃣  ПРОВЕРКА ФАЙЛОВ")

    files_to_check = [
        "bot/adapters/__init__.py",
        "bot/adapters/base.py",
        "bot/adapters/telegram.py",
        "bot/adapters/mattermost.py",
        "bot/config/settings.py",
        "web/db.py",
        "web/migrations/002_platform_users.sql",
        "MATTERMOST_DEPLOYMENT.md",
        "IMPLEMENTATION_SUMMARY.md",
        "TESTING.md",
    ]

    files_found = sum(1 for f in files_to_check if check_file_exists(f))
    print(f"\n  📊 Found: {files_found}/{len(files_to_check)} files")

    # 2. Проверка синтаксиса Python файлов
    print_header("2️⃣  ПРОВЕРКА СИНТАКСИСА (Ruff)")

    python_files = [f for f in files_to_check if f.endswith(".py")]
    syntax_ok = sum(1 for f in python_files if check_syntax_with_ruff(f))
    print(f"\n  📊 Syntax OK: {syntax_ok}/{len(python_files)} files")

    # 3. Проверка компиляции
    print_header("3️⃣  ПРОВЕРКА КОМПИЛЯЦИИ")

    compile_ok = sum(1 for f in python_files if check_python_compile(f))
    print(f"\n  📊 Compiles: {compile_ok}/{len(python_files)} files")

    # 4. Проверка импортов
    imports_ok = check_imports()

    # Итоги
    print_header("🎯 ИТОГИ")

    if files_found == len(files_to_check) and syntax_ok == len(python_files) and compile_ok == len(python_files) and imports_ok:
        print("  ✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ УСПЕШНО!")
        print("")
        print("  📝 Созданы файлы:")
        print("     • bot/adapters/base.py - базовые ин терфейсы")
        print("     • bot/adapters/telegram.py - Telegram адаптер")
        print("     • bot/adapters/mattermost.py - Mattermost адаптер")
        print("     • web/db.py - SQLAlchemy модели")
        print("     • bot/config/settings.py - конфигурация")
        print("")
        print("  📚 Документация:")
        print("     • MATTERMOST_DEPLOYMENT.md - инструкция развертывания")
        print("     • IMPLEMENTATION_SUMMARY.md - итоговый отчет")
        print("     • TESTING.md - инструкция тестирования")
        print("")
        print("  🚀 Готово к использованию!")
        return 0
    else:
        print("  ⚠️  Некоторые проверки не пройдены")
        print("")
        print("  📊 Результаты:")
        print(f"     • Файлы найдены: {files_found}/{len(files_to_check)}")
        print(f"     • Синтаксис OK: {syntax_ok}/{len(python_files)}")
        print(f"     • Компиляция OK: {compile_ok}/{len(python_files)}")
        print(f"     • Импорты OK: {'✓' if imports_ok else '✗'}")
        return 1

if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\n⚠️  Прервано пользователем")
        sys.exit(1)
    except Exception as e:
        print(f"\n\n❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
