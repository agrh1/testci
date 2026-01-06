"""
Роуты управления runtime-конфигом бота.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request

from web.config_validation import ConfigValidationError, validate_config
from web.db import list_history, read_config, rollback_to_version, write_config

bp = Blueprint("config", __name__)


def _get_db_engine():
    return current_app.config.get("DB_ENGINE")


@bp.get("/config")
def get_config() -> Any:
    """
    Возвращает конфиг routing/эскалации для бота.

    Источник:
    - если подключена БД — читаем из Postgres
    - иначе — отдаём "пустой" конфиг (fallback)
    """
    token = current_app.config.get("CONFIG_TOKEN", "")
    if token:
        got = request.headers.get("X-Config-Token", "").strip()
        if got != token:
            return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify(
            {
                "version": 0,
                "routing": {"rules": [], "default_dest": {"chat_id": None, "thread_id": None}},
                "escalation": {"enabled": False},
                "source": "fallback_no_db",
            }
        )

    data, err = read_config(engine)
    if err:
        return jsonify({"error": "config_read_failed", "detail": err}), 500

    data["source"] = "postgres"
    return jsonify(data)


@bp.put("/config")
def put_config():
    """
    Обновление конфига (admin only).
    """
    admin_token = current_app.config.get("CONFIG_ADMIN_TOKEN", "")
    if not admin_token:
        return jsonify({"error": "admin token not configured"}), 403

    got = request.headers.get("X-Admin-Token", "").strip()
    if got != admin_token:
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db disabled"}), 500

    try:
        data = request.get_json(force=True)
        validate_config(data)
    except ConfigValidationError as e:
        return jsonify({"error": "validation_failed", "detail": str(e)}), 400
    except Exception as e:
        return jsonify({"error": "bad_json", "detail": str(e)}), 400

    try:
        new_version = write_config(engine, data)
    except Exception as e:
        return jsonify({"error": "db_write_failed", "detail": str(e)}), 500

    return jsonify({"ok": True, "version": new_version})


@bp.get("/config/history")
def get_config_history():
    admin_token = current_app.config.get("CONFIG_ADMIN_TOKEN", "")
    got = request.headers.get("X-Admin-Token", "").strip()
    if not admin_token or got != admin_token:
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db disabled"}), 500

    return jsonify(list_history(engine))


@bp.post("/config/rollback")
def rollback_config():
    admin_token = current_app.config.get("CONFIG_ADMIN_TOKEN", "")
    got = request.headers.get("X-Admin-Token", "").strip()
    if not admin_token or got != admin_token:
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db disabled"}), 500

    try:
        data = request.get_json(force=True)
        version = int(data.get("version"))
    except Exception:
        return jsonify({"error": "invalid payload"}), 400

    try:
        new_version = rollback_to_version(engine, version)
    except Exception as e:
        return jsonify({"error": str(e)}), 400

    return jsonify({"ok": True, "version": new_version})
