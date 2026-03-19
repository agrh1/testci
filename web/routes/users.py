"""
CRUD-эндпоинты для управления пользователями (platform_users).

Все эндпоинты защищены X-Admin-Token.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, jsonify, request
from sqlalchemy import text

bp = Blueprint("users", __name__)


def _get_db_engine():
    return current_app.config.get("DB_ENGINE")


def _require_admin_token() -> bool:
    """Проверяет X-Admin-Token. Возвращает True если ОК."""
    token = current_app.config.get("CONFIG_ADMIN_TOKEN", "")
    if not token:
        return True  # Если токен не задан, пропускаем
    got = request.headers.get("X-Admin-Token", "").strip()
    return got == token


@bp.get("/users")
def list_users() -> Any:
    """Список пользователей с пагинацией."""
    if not _require_admin_token():
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db_not_configured"}), 503

    limit = request.args.get("limit", "50", type=str)
    offset = request.args.get("offset", "0", type=str)
    role = request.args.get("role", "", type=str).strip()

    try:
        limit = min(int(limit), 200)
        offset = int(offset)
    except (ValueError, TypeError):
        limit, offset = 50, 0

    query = """
        SELECT id, mattermost_user_id, role, username, full_name,
               last_command, last_command_at, sync_status, created_at, updated_at
        FROM platform_users
    """
    params: dict[str, Any] = {"limit": limit, "offset": offset}

    if role:
        query += " WHERE role = :role"
        params["role"] = role

    query += " ORDER BY role DESC, created_at DESC LIMIT :limit OFFSET :offset"

    with engine.connect() as conn:
        rows = conn.execute(text(query), params).mappings().all()
        total_row = conn.execute(
            text("SELECT COUNT(*) AS cnt FROM platform_users" + (" WHERE role = :role" if role else "")),
            {"role": role} if role else {},
        ).mappings().first()
        total = total_row["cnt"] if total_row else 0

    users = [
        {
            "id": r["id"],
            "mattermost_user_id": r["mattermost_user_id"],
            "role": r["role"],
            "username": r["username"],
            "full_name": r["full_name"],
            "last_command": r["last_command"],
            "last_command_at": r["last_command_at"].isoformat() if r["last_command_at"] else None,
            "sync_status": r["sync_status"],
            "created_at": r["created_at"].isoformat() if r["created_at"] else None,
            "updated_at": r["updated_at"].isoformat() if r["updated_at"] else None,
        }
        for r in rows
    ]

    return jsonify({"ok": True, "data": users, "total": total, "limit": limit, "offset": offset})


@bp.get("/users/<mm_user_id>")
def get_user(mm_user_id: str) -> Any:
    """Получить пользователя по mattermost_user_id."""
    if not _require_admin_token():
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db_not_configured"}), 503

    with engine.connect() as conn:
        row = conn.execute(
            text("""
                SELECT id, mattermost_user_id, role, username, full_name,
                       last_command, last_command_at, sync_status, created_at, updated_at
                FROM platform_users WHERE mattermost_user_id = :mm_id
            """),
            {"mm_id": mm_user_id},
        ).mappings().first()

    if row is None:
        return jsonify({"error": "not_found"}), 404

    return jsonify({
        "ok": True,
        "data": {
            "id": row["id"],
            "mattermost_user_id": row["mattermost_user_id"],
            "role": row["role"],
            "username": row["username"],
            "full_name": row["full_name"],
            "last_command": row["last_command"],
            "last_command_at": row["last_command_at"].isoformat() if row["last_command_at"] else None,
            "sync_status": row["sync_status"],
            "created_at": row["created_at"].isoformat() if row["created_at"] else None,
            "updated_at": row["updated_at"].isoformat() if row["updated_at"] else None,
        },
    })


@bp.post("/users")
def create_user() -> Any:
    """Создать нового пользователя."""
    if not _require_admin_token():
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db_not_configured"}), 503

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "invalid_json"}), 400

    mm_user_id = (body.get("mattermost_user_id") or "").strip()
    if not mm_user_id:
        return jsonify({"error": "mattermost_user_id is required"}), 400

    role = (body.get("role") or "user").strip()
    if role not in ("admin", "user"):
        return jsonify({"error": "role must be 'admin' or 'user'"}), 400

    username = (body.get("username") or "").strip()
    full_name = (body.get("full_name") or "").strip()

    with engine.begin() as conn:
        existing = conn.execute(
            text("SELECT id FROM platform_users WHERE mattermost_user_id = :mm_id"),
            {"mm_id": mm_user_id},
        ).first()

        if existing:
            return jsonify({"error": "user_already_exists"}), 409

        conn.execute(
            text("""
                INSERT INTO platform_users (mattermost_user_id, role, username, full_name, sync_status, created_at, updated_at)
                VALUES (:mm_id, :role, NULLIF(:username, ''), NULLIF(:full_name, ''), 'active', now(), now())
            """),
            {"mm_id": mm_user_id, "role": role, "username": username, "full_name": full_name},
        )

    return jsonify({"ok": True, "mattermost_user_id": mm_user_id, "role": role}), 201


@bp.put("/users/<mm_user_id>")
def update_user(mm_user_id: str) -> Any:
    """Обновить пользователя (role, username, full_name)."""
    if not _require_admin_token():
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db_not_configured"}), 503

    body = request.get_json(silent=True)
    if not body:
        return jsonify({"error": "invalid_json"}), 400

    updates = []
    params: dict[str, Any] = {"mm_id": mm_user_id}

    if "role" in body:
        role = (body["role"] or "").strip()
        if role not in ("admin", "user"):
            return jsonify({"error": "role must be 'admin' or 'user'"}), 400
        updates.append("role = :role")
        params["role"] = role

    if "username" in body:
        updates.append("username = :username")
        params["username"] = (body["username"] or "").strip() or None

    if "full_name" in body:
        updates.append("full_name = :full_name")
        params["full_name"] = (body["full_name"] or "").strip() or None

    if not updates:
        return jsonify({"error": "no_fields_to_update"}), 400

    updates.append("updated_at = now()")

    with engine.begin() as conn:
        result = conn.execute(
            text(f"UPDATE platform_users SET {', '.join(updates)} WHERE mattermost_user_id = :mm_id"),
            params,
        )
        if result.rowcount == 0:
            return jsonify({"error": "not_found"}), 404

    return jsonify({"ok": True, "mattermost_user_id": mm_user_id})


@bp.delete("/users/<mm_user_id>")
def delete_user(mm_user_id: str) -> Any:
    """Удалить пользователя."""
    if not _require_admin_token():
        return jsonify({"error": "unauthorized"}), 401

    engine = _get_db_engine()
    if engine is None:
        return jsonify({"error": "db_not_configured"}), 503

    with engine.begin() as conn:
        result = conn.execute(
            text("DELETE FROM platform_users WHERE mattermost_user_id = :mm_id"),
            {"mm_id": mm_user_id},
        )
        if result.rowcount == 0:
            return jsonify({"error": "not_found"}), 404

    return jsonify({"ok": True, "mattermost_user_id": mm_user_id})
