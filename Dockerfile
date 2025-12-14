# ============================================================================
# Dockerfile — единый образ для web (Flask-приложение) и bot (воркер).
#
# Важные моменты:
#   - Ставим Python 3.12-slim → меньше лишнего мусора.
#   - Устанавливаем зависимости через requirements.txt.
#   - Ставим curl, потому что docker-compose healthcheck использует curl.
#   - По умолчанию запускаем веб-сервер (gunicorn).
#   - Для bot контейнер переопределяет CMD → python bot.py
# ============================================================================

# 1. Базовый образ Python
FROM python:3.12-slim AS runtime

# 2. Отключаем .pyc и включаем немедленный вывод stdout/stderr
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

# 3. Рабочая директория внутри контейнера
WORKDIR /app

# 4. Копируем только список зависимостей перед install —
#    так Docker кэширует слой и ускоряет rebuild образа.
COPY requirements.txt .

# 5. Устанавливаем зависимости
RUN pip install --no-cache-dir -r requirements.txt \
    # curl нужен для HEALTHCHECK
    && apt-get update \
    && apt-get install -y curl \
    && rm -rf /var/lib/apt/lists/*

# 6. Копируем весь проект
COPY . .

# 7. Порт для Flask/gunicorn
EXPOSE 8000

# 8. Команда по умолчанию — веб-сервис через gunicorn.
#    В docker-compose для bot мы переопределим CMD на ["python", "bot.py"]
CMD ["gunicorn", "-b", "0.0.0.0:8000", "app:app"]
