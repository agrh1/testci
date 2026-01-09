"""
Клиент для прямых вызовов ServiceDesk API (users, reset password).
"""

from __future__ import annotations

import base64
import json
import logging
import random
import string
import time
from dataclasses import dataclass
from typing import Optional

import requests

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SdApiConfig:
    base_url: str
    login: str
    password: str
    timeout_s: float = 10.0


class SdApiClient:
    def __init__(self, cfg: SdApiConfig) -> None:
        self._cfg = cfg

    def _basic_auth_header(self) -> dict[str, str]:
        if not self._cfg.login or not self._cfg.password:
            raise ValueError("Не заданы учетные данные для авторизации.")
        credentials = f"{self._cfg.login}:{self._cfg.password}"
        encoded = base64.b64encode(credentials.encode()).decode()
        return {"Authorization": f"Basic {encoded}"}

    def find_users_by_phone(self, phone_number: str) -> list[dict[str, str]]:
        url = f"{self._cfg.base_url.rstrip('/')}/api/user"
        headers = self._basic_auth_header()
        params = {"fields": "Id,Name,Login,Phone", "Phone": phone_number}
        response = requests.get(url, headers=headers, params=params, timeout=self._cfg.timeout_s)

        if response.status_code == 200:
            users = response.json().get("Users", [])
            return [
                {"Id": str(u.get("Id")), "Name": str(u.get("Name")), "Login": str(u.get("Login"))}
                for u in users
            ]
        logger.warning("find_users_by_phone error: %s %s", response.status_code, response.text)
        return []

    def reset_user_password(self, user_id: int, new_password: Optional[str] = None) -> dict[str, object]:
        start_time = time.time()
        result: dict[str, object] = {
            "success": False,
            "message": "",
            "user_id": user_id,
            "status_code": None,
            "response_time": None,
            "raw_response": None,
            "new_password": None,
            "note": "Пароль был сгенерирован автоматически. Рекомендуется изменить его после входа.",
        }

        if new_password is None:
            new_password = _generate_secure_password()
            result["new_password"] = new_password

        if not self._user_exists(user_id):
            result["message"] = "Пользователь не найден."
            result["status_code"] = 404
            return result

        url = f"{self._cfg.base_url.rstrip('/')}/api/user/{user_id}"
        headers = self._basic_auth_header()
        headers["Content-Type"] = "application/json"
        payload = {"Password": new_password, "ConfirmPassword": new_password}

        try:
            response = requests.put(
                url,
                headers=headers,
                data=json.dumps(payload),
                timeout=self._cfg.timeout_s,
            )
            elapsed_time = round(time.time() - start_time, 2)
            result["status_code"] = response.status_code
            result["response_time"] = f"{elapsed_time} сек."

            if response.status_code == 200:
                result["success"] = True
                result["message"] = "✅ Пароль успешно изменён."
            else:
                result["message"] = f"❌ Ошибка: код {response.status_code}, {response.text}"

            try:
                result["raw_response"] = response.json()
            except json.JSONDecodeError:
                result["raw_response"] = response.text
            return result
        except requests.exceptions.RequestException as e:
            elapsed_time = round(time.time() - start_time, 2)
            result["message"] = f"🚨 Исключение при выполнении запроса: {e}"
            result["response_time"] = f"{elapsed_time} сек."
            return result

    def _user_exists(self, user_id: int) -> bool:
        url = f"{self._cfg.base_url.rstrip('/')}/api/user/{user_id}"
        headers = self._basic_auth_header()
        try:
            response = requests.get(url, headers=headers, timeout=self._cfg.timeout_s)
        except requests.exceptions.RequestException as e:
            logger.warning("user_exists request error: %s", e)
            return False
        if response.status_code == 200:
            return True
        if response.status_code == 404:
            return False
        logger.warning("user_exists error: %s %s", response.status_code, response.text)
        return False


    def list_tasks_changed_since(
        self,
        changed_more_than: str,
        *,
        fields: str = "Id,CategoryIds,Categories",
        category_ids: Optional[str] = None,
        pagesize: int = 200,
    ) -> list[dict[str, object]]:
        url = f"{self._cfg.base_url.rstrip('/')}/api/task"
        headers = self._basic_auth_header()
        headers["Accept"] = "application/json"

        page = 1
        tasks: list[dict[str, object]] = []
        while True:
            params = {
                "ChangedMoreThan": changed_more_than,
                "fields": fields,
                "page": str(page),
                "pagesize": str(pagesize),
            }
            if category_ids:
                params["CategoryIds"] = category_ids

            response = requests.get(url, headers=headers, params=params, timeout=self._cfg.timeout_s)
            if response.status_code >= 400:
                raise RuntimeError(f"ServiceDesk error {response.status_code}: {response.text}")

            data = response.json()
            items = data.get("Tasks") or []
            tasks.extend(items)

            paginator = data.get("Paginator") or {}
            page_count = int(paginator.get("PageCount", page))
            if page >= page_count:
                break
            page += 1

        return tasks

    def update_task_categories_comment(
        self,
        task_id: int,
        *,
        category_ids: str,
        comment: str,
        is_private: bool = True,
    ) -> dict[str, object]:
        url = f"{self._cfg.base_url.rstrip('/')}/api/task/{task_id}"
        headers = self._basic_auth_header()
        headers["Content-Type"] = "application/json"

        payload: dict[str, object] = {
            "CategoryIds": category_ids,
            "Comment": comment,
            "IsPrivateComment": bool(is_private),
        }

        response = requests.put(url, headers=headers, data=json.dumps(payload), timeout=self._cfg.timeout_s)
        if response.status_code >= 400:
            raise RuntimeError(f"ServiceDesk error {response.status_code}: {response.text}")
        try:
            return response.json()
        except json.JSONDecodeError:
            return {"raw_response": response.text}

def _generate_secure_password(length: int = 12) -> str:
    if length < 8:
        raise ValueError("Длина пароля должна быть не менее 8 символов")
    characters = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(random.choice(characters) for _ in range(length))
