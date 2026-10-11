"""
Утилиты работы с eventlog ServiceDesk (старая версия интерфейса).
"""

from __future__ import annotations

import logging
import re
from typing import Optional
from urllib.parse import urljoin, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("bot.eventlog")
REQUEST_TIMEOUT_S = 10.0


def _is_login_page(response: requests.Response) -> bool:
    path = urlsplit(response.url).path.rstrip("/").lower()
    if path.endswith("/login") or "/login/" in path:
        return True
    soup = BeautifulSoup(response.text, "html.parser")
    return soup.select_one('form input[type="password"]') is not None


def _login(session: requests.Session, login: str, password: str, base_url: str) -> str:
    """Загружает форму входа и отправляет её на фактический action."""
    page = session.get(f"{base_url.rstrip('/')}/login", timeout=REQUEST_TIMEOUT_S)
    if not 200 <= page.status_code < 300:
        raise RuntimeError(f"eventlog login page: HTTP {page.status_code}")
    soup = BeautifulSoup(page.text, "html.parser")
    password_input = soup.select_one('form input[type="password"]')
    if password_input is None:
        raise RuntimeError("eventlog login page: password form not found")
    form = password_input.find_parent("form")
    if str(form.get("method", "get")).lower() != "post":
        raise RuntimeError("eventlog login page: expected POST form")
    action = urljoin(page.url, form.get("action") or page.url)
    page_url = urlsplit(page.url)
    action_url = urlsplit(action)
    if (action_url.scheme, action_url.netloc) != (page_url.scheme, page_url.netloc):
        raise RuntimeError("eventlog login page: form action points to another origin")
    login_input = form.find("input", attrs={"name": re.compile("^login$", re.I)})
    if login_input is None:
        login_input = form.find("input", attrs={"id": re.compile("^login$", re.I)})
    if login_input is None:
        raise RuntimeError("eventlog login page: login field not found")
    # На ServiceDesk 4.60 JavaScript присваивает name из id перед submit.
    login_field = login_input.get("name") or login_input.get("id")
    password_field = password_input.get("name") or password_input.get("id")
    if not password_field:
        raise RuntimeError("eventlog login page: password field has no name or id")
    payload = {
        field["name"]: field.get("value", "")
        for field in form.select('input[type="hidden"][name]')
    }
    payload.update({login_field: login, password_field: password})
    response = session.post(action, data=payload, timeout=REQUEST_TIMEOUT_S)
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"eventlog login failed: HTTP {response.status_code}")
    if _is_login_page(response):
        raise RuntimeError("eventlog login failed: server returned the login form; check EVENTLOG_LOGIN/PASSWORD")
    # Используем HTTPS, если сервер перевёл страницу входа с HTTP на HTTPS.
    configured = urlsplit(base_url.rstrip("/"))
    return urlunsplit((page_url.scheme, page_url.netloc, configured.path, "", ""))


def _get_page(session: requests.Session, url: str, *, missing_ok: bool = False) -> Optional[str]:
    response = session.get(url, timeout=REQUEST_TIMEOUT_S)
    logger.debug(
        "eventlog page response: status=%s redirect_statuses=%s",
        response.status_code, [item.status_code for item in response.history],
    )
    if _is_login_page(response):
        raise RuntimeError("eventlog authentication required: event page redirected to login")
    if missing_ok and response.status_code == 404:
        return None
    if not 200 <= response.status_code < 300:
        raise RuntimeError(f"eventlog page fetch failed: HTTP {response.status_code}")
    return response.text


def get_item(event_id: int, login: str, password: str, base_url: str) -> Optional[str]:
    """Возвращает HTML события; только HTTP 404 события означает отсутствие."""
    with requests.Session() as session:
        authenticated_base = _login(session, login, password, base_url)
        return _get_page(session, f"{authenticated_base}/eventlog.ivp/view/{event_id}", missing_ok=True)


def get_last_item(login: str, password: str, base_url: str) -> Optional[str]:
    with requests.Session() as session:
        authenticated_base = _login(session, login, password, base_url)
        text = _get_page(session, f"{authenticated_base}/eventlog.ivp/list")
        match = re.search(r"eventlog\.ivp/view/(\d+)", text)
        return match.group(1) if match else None


def parse_event(text: str) -> dict[str, str]:
    logger.debug("eventlog parse_event start")
    event_info: dict[str, str] = {}
    soup = BeautifulSoup(text, "html.parser")
    event_body = soup.find("div", {"class": "formbody"})
    if event_body is None:
        raise ValueError("eventlog: response has no event form")
    field_names = {
        "name": "Название", "date": "Дата", "type": "Тип", "description": "Описание",
    }
    fields = event_body.find_all("div", {"class": "field"})
    for field in fields:
        label = field.find(
            "label", attrs={"for": lambda value: bool(value) and value.lower() in field_names},
        )
        if label is None:
            continue
        key = field_names[label["for"].lower()]
        label.extract()
        event_info[key] = field.get_text(separator="\n", strip=True)
    if not any(event_info.values()):
        raise ValueError("eventlog: event form is empty")
    logger.debug("eventlog parse_event done: keys=%s", list(event_info.keys()))
    return event_info

