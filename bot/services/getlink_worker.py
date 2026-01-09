"""
Автоматическая обработка заявок с категориями getlink_*.

Логика:
- каждые N секунд берём заявки, изменённые за последнее окно,
- если есть getlink_* категория — создаём upload/download ссылки в Seafile,
- пишем скрытый комментарий и удаляем getlink_* категорию из списка.
"""

from __future__ import annotations

import asyncio
import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, Optional

from bot.services.seafile_store import SeafileService, SeafileServiceStore
from bot.utils.sd_api_client import SdApiClient
from bot.utils.seafile_client import get_download_link, getlink

logger = logging.getLogger(__name__)


GETLINK_PREFIX = "getlink_"


@dataclass(frozen=True)
class CategoryEntry:
    cat_id: str
    name: str


@dataclass(frozen=True)
class ServiceCategory:
    service: SeafileService
    cat_id: Optional[str]
    name: Optional[str]


def _parse_sd_category(raw: str) -> tuple[Optional[str], Optional[str]]:
    value = (raw or "").strip()
    if not value:
        return None, None
    # Supported formats: "110:getlink_ml" or "110|getlink_ml"
    m = re.match(r"^\\s*(\\d+)\\s*[:|]\\s*(.+?)\\s*$", value)
    if m:
        return m.group(1), m.group(2)
    if value.isdigit():
        return value, None
    return None, value


def _split_category_ids(raw: str) -> list[str]:
    return [s.strip() for s in (raw or "").split(",") if s.strip()]


def _split_category_names(raw: str) -> list[str]:
    return [s.strip() for s in (raw or "").split("||") if s.strip()]


def _parse_categories(names_raw: str, ids_raw: str) -> list[CategoryEntry]:
    names = _split_category_names(names_raw)
    ids = _split_category_ids(ids_raw)
    items: list[CategoryEntry] = []
    if names and ids and len(names) == len(ids):
        for idx, cat_id in enumerate(ids):
            name = names[idx] if idx < len(names) else ""
            items.append(CategoryEntry(cat_id=cat_id, name=name))
    elif ids:
        items = [CategoryEntry(cat_id=cat_id, name="") for cat_id in ids]
    elif names:
        items = [CategoryEntry(cat_id="", name=name) for name in names]
    return items


def _is_getlink_name(name: str) -> bool:
    return name.strip().lower().startswith(GETLINK_PREFIX)


def _build_service_categories(services: Iterable[SeafileService]) -> list[ServiceCategory]:
    items: list[ServiceCategory] = []
    for svc in services:
        cat_id, name = _parse_sd_category(svc.sd_category)
        if cat_id or name:
            items.append(ServiceCategory(service=svc, cat_id=cat_id, name=name))
    return items


def _find_getlink_entries(
    categories: list[CategoryEntry],
    service_category_ids: set[str],
) -> list[CategoryEntry]:
    entries: list[CategoryEntry] = []
    for entry in categories:
        if _is_getlink_name(entry.name):
            entries.append(entry)
            continue
        if entry.cat_id and entry.cat_id in service_category_ids:
            entries.append(entry)
    return entries


def _pick_services(
    getlink_entries: list[CategoryEntry],
    service_categories: list[ServiceCategory],
) -> list[SeafileService]:
    matched: list[SeafileService] = []
    for entry in getlink_entries:
        for svc in service_categories:
            if svc.cat_id and entry.cat_id and svc.cat_id == entry.cat_id:
                matched.append(svc.service)
                break
            if svc.name and entry.name and svc.name.lower() == entry.name.lower():
                matched.append(svc.service)
                break
    # unique by service_id preserving order
    seen: set[int] = set()
    result: list[SeafileService] = []
    for svc in matched:
        if svc.service_id in seen:
            continue
        seen.add(svc.service_id)
        result.append(svc)
    return result


def _remove_getlink_category_ids(
    categories: list[CategoryEntry],
    service_category_ids: set[str],
) -> list[str]:
    keep_ids: list[str] = []
    for entry in categories:
        if _is_getlink_name(entry.name):
            continue
        if entry.cat_id and entry.cat_id in service_category_ids:
            continue
        if entry.cat_id:
            keep_ids.append(entry.cat_id)
    return keep_ids


def _format_category_ids(ids: Iterable[str]) -> str:
    return ", ".join([i for i in ids if i])


def _build_success_comment(
    task_id: str,
    upload_link: str,
    download_link: str,
    password: str,
    expire_days: int,
) -> str:
    return (
        "Ссылки для логов:\n"
        f"- загрузка: {upload_link}\n"
        f"- скачивание: {download_link}\n"
        f"Пароль: {password}\n"
        f"Срок действия: {expire_days} дней\n"
        f"ID заявки: {task_id}"
    )


def _build_multi_service_comment(names: list[str]) -> str:
    suffix = ", ".join(names) if names else "—"
    return (
        "Найдено несколько getlink_* категорий для разных сервисов. "
        f"Категории: {suffix}. "
        "Создание ссылок отменено."
    )


def _build_missing_service_comment(names: list[str]) -> str:
    suffix = ", ".join(names) if names else "—"
    return (
        "Не найден сервис Seafile для категории getlink_*. "
        f"Категории: {suffix}."
    )


async def getlink_poll_once(
    *,
    sd_api_client: SdApiClient,
    seafile_store: SeafileServiceStore,
    lookback_s: int,
    pagesize: int = 200,
) -> None:
    services = await seafile_store.list_services(enabled_only=True)
    service_categories = _build_service_categories(services)
    service_category_ids = {c.cat_id for c in service_categories if c.cat_id}

    changed_since = datetime.now() - timedelta(seconds=lookback_s)
    changed_since_str = changed_since.strftime("%Y-%m-%d %H:%M:%S")

    category_ids_filter = ",".join(sorted(service_category_ids)) or None
    try:
        tasks = await asyncio.to_thread(
            sd_api_client.list_tasks_changed_since,
            changed_since_str,
            fields="Id,CategoryIds,Categories",
            category_ids=category_ids_filter,
            pagesize=pagesize,
        )
    except Exception as e:
        logger.warning("getlink_poll list_tasks_changed_since error: %s", e)
        return

    if not tasks:
        return

    for task in tasks:
        task_id_raw = task.get("Id")
        if task_id_raw is None:
            continue
        task_id = str(task_id_raw)
        categories_raw = str(task.get("Categories") or "")
        category_ids_raw = str(task.get("CategoryIds") or "")

        categories = _parse_categories(categories_raw, category_ids_raw)
        getlink_entries = _find_getlink_entries(categories, service_category_ids)
        if not getlink_entries:
            continue

        services_matched = _pick_services(getlink_entries, service_categories)
        getlink_names = [c.name or c.cat_id for c in getlink_entries]
        keep_ids = _remove_getlink_category_ids(categories, service_category_ids)
        new_category_ids = _format_category_ids(keep_ids)

        if len(services_matched) != 1:
            if len(services_matched) == 0:
                comment = _build_missing_service_comment(getlink_names)
            else:
                comment = _build_multi_service_comment(getlink_names)
            try:
                await asyncio.to_thread(
                    sd_api_client.update_task_categories_comment,
                    int(task_id),
                    category_ids=new_category_ids,
                    comment=comment,
                    is_private=True,
                )
            except Exception as e:
                logger.warning("getlink_poll update_task error task_id=%s: %s", task_id, e)
            continue

        service = services_matched[0]
        try:
            upload_res = await asyncio.to_thread(getlink, task_id, service)
        except Exception as e:
            logger.warning("getlink_poll upload error task_id=%s: %s", task_id, e)
            upload_res = "err"

        if upload_res == "err":
            comment = "Не удалось создать ссылку на загрузку."
        else:
            upload_link = upload_res.splitlines()[-1].strip()
            try:
                download_res = await asyncio.to_thread(get_download_link, task_id, service)
            except Exception as e:
                logger.warning("getlink_poll download error task_id=%s: %s", task_id, e)
                download_res = {"status": "err"}

            status = download_res.get("status")
            if status != "ok":
                comment = "Не удалось создать ссылку на скачивание."
            else:
                comment = _build_success_comment(
                    task_id=task_id,
                    upload_link=upload_link,
                    download_link=str(download_res.get("link")),
                    password=str(download_res.get("password")),
                    expire_days=int(download_res.get("expire_days", 7)),
                )

        try:
            await asyncio.to_thread(
                sd_api_client.update_task_categories_comment,
                int(task_id),
                category_ids=new_category_ids,
                comment=comment,
                is_private=True,
            )
        except Exception as e:
            logger.warning("getlink_poll update_task error task_id=%s: %s", task_id, e)


async def getlink_poll_loop(
    *,
    sd_api_client: SdApiClient,
    seafile_store: SeafileServiceStore,
    interval_s: int,
    lookback_s: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await getlink_poll_once(
            sd_api_client=sd_api_client,
            seafile_store=seafile_store,
            lookback_s=lookback_s,
        )
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_s)
        except asyncio.TimeoutError:
            continue
