"""
Утилиты работы с eventlog ServiceDesk (старая версия интерфейса).
"""

from __future__ import annotations

import logging
import re
from typing import Optional

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("bot.eventlog")
REQUEST_TIMEOUT_S = 10.0


def get_item(event_id: int, login: str, password: str, base_url: str) -> Optional[str]:
    """
    Подключаемся к странице сообщения, и если такая страница есть - возвращаем её.
    """
    logger.debug("eventlog get_item start: event_id=%s base_url=%s", event_id, base_url)
    with requests.Session() as session:
        payload = {"login": login, "password": password}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
            )
        }
        rs = session.post(
            f"{base_url.rstrip('/')}/registertask.ivp", headers=headers,
            data=payload, timeout=REQUEST_TIMEOUT_S,
        )
        if not 200 <= rs.status_code < 300:
            logger.warning("eventlog login response: status=%s; checking event page", rs.status_code)
        r = session.get(
            f"{base_url.rstrip('/')}/eventlog.ivp/view/{event_id}",
            cookies=rs.cookies, timeout=REQUEST_TIMEOUT_S,
        )
        logger.debug(
            "eventlog get_item response: event_id=%s status=%s redirect_statuses=%s",
            event_id, r.status_code, [response.status_code for response in r.history],
        )
        if 200 <= r.status_code < 300:
            logger.debug("eventlog get_item ok: event_id=%s size=%s", event_id, len(r.text))
            return r.text
        if r.status_code == 404 and 200 <= rs.status_code < 300:
            return None
        raise RuntimeError(
            f"eventlog fetch failed: login HTTP {rs.status_code}, "
            f"event {event_id} HTTP {r.status_code}"
        )


def get_last_item(login: str, password: str, base_url: str) -> Optional[str]:
    with requests.Session() as session:
        payload = {"login": login, "password": password}
        headers = {
            "User-Agent": (
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/51.0.2704.103 Safari/537.36"
            )
        }
        rs = session.post(
            f"{base_url.rstrip('/')}/registertask.ivp", headers=headers,
            data=payload, timeout=REQUEST_TIMEOUT_S,
        )
        if not 200 <= rs.status_code < 300:
            logger.warning("eventlog login response: status=%s; checking event list", rs.status_code)
        r = session.get(
            f"{base_url.rstrip('/')}/eventlog.ivp/list",
            cookies=rs.cookies, timeout=REQUEST_TIMEOUT_S,
        )
        logger.debug("eventlog get_last_item response: status=%s", r.status_code)
        if not 200 <= r.status_code < 300:
            raise RuntimeError(
                f"eventlog list fetch failed: login HTTP {rs.status_code}, list HTTP {r.status_code}"
            )
        text = r.text.split()
        for line in text:
            if "eventlog.ivp/view/" in line:
                m = re.search(r"eventlog\.ivp/view/(\d+)", line)
                if not m:
                    continue
                last_item = m.group(1)
                logger.debug("eventlog get_last_item found: %s", last_item)
                return last_item
        return None


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

