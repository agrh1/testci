"""
App factory для web-сервиса.

Сюда вынесена сборка Flask-приложения:
- конфигурация из env;
- логирование;
- инициализация БД;
- регистрация роутов.
"""

from __future__ import annotations

from flask import Flask

from web.db import create_db_engine, db_enabled, init_db
from web.logging_setup import setup_logging
from web.routes import config as config_routes
from web.routes import health as health_routes
from web.routes import sd as sd_routes
from web.settings import build_flask_config


def create_app() -> Flask:
    """
    Создаёт и конфигурирует Flask-приложение.
    """
    app = Flask(__name__)

    # 1) Загружаем конфиг из env.
    cfg = build_flask_config()
    app.config.update(cfg)

    # 2) Настраиваем логирование и сохраняем адаптер в app.config.
    logger = setup_logging(
        environment=app.config.get("ENVIRONMENT", "unknown"),
        git_sha=app.config.get("GIT_SHA", "unknown"),
    )
    app.config["APP_LOGGER"] = logger

    # 3) Инициализируем БД (если включена).
    db_engine = None
    if db_enabled():
        try:
            db_engine = create_db_engine()
            init_db(db_engine)
            logger.info("db init ok (DATABASE_URL задан)")
        except Exception as e:
            # Важно: web НЕ должен падать из-за БД на первом этапе.
            db_engine = None
            logger.error("db init failed: %s", e)
    else:
        logger.info("db disabled: DATABASE_URL not set")

    app.config["DB_ENGINE"] = db_engine

    # 4) Регистрируем роуты.
    app.register_blueprint(health_routes.bp)
    app.register_blueprint(sd_routes.bp)
    app.register_blueprint(config_routes.bp)

    return app
