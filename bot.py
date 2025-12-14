"""
Простейший "бот"-воркер.

Задача:
- периодически опрашивать эндпоинт /health веб-сервиса.
- писать результат в лог, чтобы было видно, что взаимодействие
  между контейнерами работает (bot -> web по docker-сети).

В реальном проекте на месте этого файла будет, например, телеграм-бот,
воркер, который обрабатывает задачи из очереди, и т.п.
"""

from __future__ import annotations

import os
import time
from typing import Final

import requests

# Значения по умолчанию. Удобно держать в константах.
DEFAULT_WEB_HOST: Final[str] = "web"     # имя сервиса из docker-compose.yml
DEFAULT_WEB_PORT: Final[int] = 8000
DEFAULT_INTERVAL_SECONDS: Final[int] = 5


def build_health_url(host: str, port: int) -> str:
    """
    Собрать URL для доступа к эндпоинту /health веб-сервиса.

    Args:
        host: имя хоста (или сервиса внутри docker-compose сети).
        port: порт, на котором слушает веб-сервис.

    Returns:
        Строка вида "http://host:port/health".
    """
    return f"http://{host}:{port}/health"


def read_config_from_env() -> tuple[str, int, int]:
    """
    Прочитать настройки из переменных окружения.

    Используем ENV, чтобы не хардкодить значения в коде:
    - веб-сервис может жить на другом хосте/порте;
    - интервал можно менять, не перекатывая образ.

    Returns:
        Кортеж (host, port, interval_seconds).
    """
    host = os.getenv("WEB_HOST", DEFAULT_WEB_HOST)

    # os.getenv возвращает строку, поэтому порт и интервал нужно привести к int.
    # Если в переменной окажется что-то некорректное, int(...) выбросит ValueError —
    # это нормально: приложение упадёт, и Docker/оркестратор его перезапустит.
    port_str = os.getenv("WEB_PORT", str(DEFAULT_WEB_PORT))
    interval_str = os.getenv("BOT_INTERVAL", str(DEFAULT_INTERVAL_SECONDS))

    port = int(port_str)
    interval_seconds = int(interval_str)

    return host, port, interval_seconds


def check_health_once(url: str, timeout_seconds: float = 2.0) -> None:
    """
    Один запрос к /health с логированием результата.

    Args:
        url: Полный URL до эндпоинта /health.
        timeout_seconds: Таймаут HTTP-запроса в секундах.
    """
    try:
        response = requests.get(url, timeout=timeout_seconds)
        body = response.text.strip()  # убираем лишние переносы строки
        print(f"[bot] {response.status_code} {body}", flush=True)
    except Exception as exc:  # реальный код лучше бы делил типы ошибок
        print(f"[bot] ERROR: {exc!r}", flush=True)


def main_loop() -> None:
    """
    Основной бесконечный цикл бота.

    - читает конфиг из ENV;
    - собирает URL;
    - в цикле раз в N секунд дергает /health и пишет результат в лог.
    """
    host, port, interval_seconds = read_config_from_env()
    url = build_health_url(host, port)

    print(
        f"[bot] Starting healthcheck loop for {url}, "
        f"interval={interval_seconds}s",
        flush=True,
    )

    while True:
        check_health_once(url)
        time.sleep(interval_seconds)


if __name__ == "__main__":
    # Точка входа при запуске через:
    #   python bot.py
    #
    # В Docker этот файл запускается через команду:
    #   command: ["python", "bot.py"]
    main_loop()
