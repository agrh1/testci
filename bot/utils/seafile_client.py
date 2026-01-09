"""
Клиент для создания каталогов и ссылок в Seafile.
"""

from __future__ import annotations

import logging
import secrets
import string
from typing import Optional

import requests

from bot.services.seafile_store import SeafileService

logger = logging.getLogger(__name__)

DOWNLOAD_EXPIRE_DAYS = 7
DOWNLOAD_PASSWORD_LENGTH = 10
_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def _generate_password(length: int = DOWNLOAD_PASSWORD_LENGTH) -> str:
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(length))


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

def _list_share_links(task_id: str, service: SeafileService, token: str) -> Optional[list[dict]]:
    headers = {
        "Authorization": token,
        "Accept": "application/json;charset=utf-8;indent=4",
    }
    path = "/" + task_id  # при необходимости можно сделать "/" + task_id + "/"
    params = {"repo_id": service.repo_id, "path": path}

    try:
        res = requests.get(
            f"{service.base_url.rstrip('/')}/api/v2.1/share-links/",
            headers=headers,
            params=params,
            timeout=10,
        )
    except Exception as e:
        logger.warning("Seafile list share-links error: %s", e)
        return None

    if res.status_code != 200:
        logger.warning(
            "Seafile list share-links unexpected status: %s body=%s",
            res.status_code,
            res.text,
        )
        return None

    try:
        data = res.json()
    except Exception as e:
        logger.warning("Seafile list share-links json error: %s body=%s", e, res.text)
        return None

    if isinstance(data, list):
        return data

    logger.warning("Seafile list share-links unexpected json: %s", data)
    return None

def get_download_link(task_id: str, service: SeafileService) -> dict:
    token = _get_auth_token(service)
    if not token:
        return {"status": "err"}

    exists = _folder_exists(task_id, service, token)
    if exists is None:
        return {"status": "err"}
    if not exists:
        return {"status": "missing"}

    # 1) GET existing
    links = _list_share_links(task_id, service, token)
    if links:
        # берём первую (или можно выбрать “самую свежую” по ctime, если поле есть)
        link = (links[0] or {}).get("link")
        if link:
            # Важно: пароль для уже созданной ссылки API обычно не возвращает.
            return {
                "status": "ok",
                "link": str(link),
                "password": "",
                "expire_days": DOWNLOAD_EXPIRE_DAYS,
                "existing": True,
            }

    # 2) POST create new
    password = _generate_password()
    res = _make_download_link(task_id, service, token, password=password)

    link = res.get("link")
    if link:
        return {
            "status": "ok",
            "link": str(link),
            "password": password,
            "expire_days": DOWNLOAD_EXPIRE_DAYS,
            "existing": False,
        }

    # Если API вернул error_msg — полезно отдать наружу (и залогировать).
    err = res.get("error_msg") if isinstance(res, dict) else None
    if err:
        logger.warning("Seafile create share-link error_msg: %s", err)

    return {"status": "err"}


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


def _make_download_link(
    task_id: str,
    service: SeafileService,
    token: str,
    *,
    password: str,
) -> dict:
    headers = {
        "Authorization": token,
        "Accept": "application/json;charset=utf-8;indent=4",
    }
    path = "/" + task_id
    data = {
        "path": path,
        "repo_id": service.repo_id,
        "expire_days": str(DOWNLOAD_EXPIRE_DAYS),
        "password": password,
    }
    res = requests.post(
        f"{service.base_url.rstrip('/')}/api/v2.1/share-links/",
        data=data,
        headers=headers,
        timeout=10,
    )
    return res.json()


def _folder_exists(task_id: str, service: SeafileService, token: str) -> Optional[bool]:
    headers = {
        "Authorization": token,
        "Accept": "application/json;charset=utf-8;indent=4",
    }
    res = requests.get(
        f"{service.base_url.rstrip('/')}/api2/repos/{service.repo_id}/dir/?p=/{task_id}",
        headers=headers,
        timeout=10,
    )
    if res.status_code == 200:
        return True
    if res.status_code == 404:
        return False
    logger.warning("Seafile folder check unexpected status: %s", res.status_code)
    return None


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
