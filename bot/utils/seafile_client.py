"""
Клиент для создания каталогов и ссылок в Seafile.
"""

from __future__ import annotations

import logging
from typing import Optional

import requests

from bot.services.seafile_store import SeafileService

logger = logging.getLogger(__name__)


def _get_auth_token(service: SeafileService) -> Optional[str]:
    token = service.auth_token.strip()
    if token:
        return token
    if service.username and service.password:
        try:
            res = requests.post(
                f"{service.base_url.rstrip('/')}/api2/auth-token/",
                data={"username": service.username, "password": service.password},
                timeout=10,
            )
            data = res.json()
            raw = str(data.get("token") or "").strip()
            if raw:
                return f"Token {raw}"
        except Exception as e:
            logger.warning("Seafile auth-token error: %s", e)
    return None


def getlink(task_id: str, service: SeafileService) -> str:
    token = _get_auth_token(service)
    if not token:
        return "err"

    if _check_link(task_id, service, token):
        res = _make_link(task_id, service, token)
        link = res.get("link")
        if link:
            return str(f"{task_id}\n{link}")
    return "err"


def _check_link(task_id: str, service: SeafileService, token: str) -> bool:
    result = _make_link(task_id, service, token)
    try:
        if result["error_msg"]:
            res = _make_folder(task_id, service, token)
            if res == "success":
                return True
    except KeyError:
        return True
    return False


def _make_link(task_id: str, service: SeafileService, token: str) -> dict:
    headers = {
        "Authorization": token,
        "Accept": "application/json;charset=utf-8;indent=4",
    }
    path = "/" + task_id + "/"
    data = {"path": path, "repo_id": service.repo_id}
    res = requests.post(
        f"{service.base_url.rstrip('/')}/api/v2.1/upload-links/",
        data=data,
        headers=headers,
        timeout=10,
    )
    return res.json()


def _make_folder(task_id: str, service: SeafileService, token: str):
    data = {"operation": "mkdir"}
    headers = {
        "Authorization": token,
        "Accept": "application/json;charset=utf-8;indent=4",
    }
    res = requests.post(
        f"{service.base_url.rstrip('/')}/api2/repos/{service.repo_id}/dir/?p=/{task_id}",
        data=data,
        headers=headers,
        timeout=10,
    )
    return res.json()

