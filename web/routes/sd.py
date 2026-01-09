"""
Интеграция с IntraService (ServiceDesk) через HTTP.
"""

from __future__ import annotations

from typing import Any

from flask import Blueprint, current_app, g, jsonify, request

from web.intraservice import list_tasks_by_status

bp = Blueprint("sd", __name__, url_prefix="/sd")


@bp.get("/open")
def sd_open() -> tuple[Any, int]:
    """
    Возвращает заявки IntraService в статусе "Открыта" (StatusId=31).

    query params:
    - limit: сколько заявок вернуть суммарно (1..500), по умолчанию 50
    - pagesize: размер страницы IntraService (1..2000), по умолчанию 50
    - fields: список полей IntraService через запятую
    """
    status_id = 31

    # Защита от "случайно вернуть слишком много"
    try:
        limit = int(request.args.get("limit", "50"))
    except Exception:
        limit = 50
    limit = max(1, min(limit, 500))

    try:
        pagesize = int(request.args.get("pagesize", "50"))
    except Exception:
        pagesize = 50
    pagesize = max(1, min(pagesize, 2000))

    fields = (request.args.get("fields") or "Id,Name,Created,Creator,StatusId,ServiceId").strip()
    include = (request.args.get("include") or "service").strip()
    sort = (request.args.get("sort") or "Id desc").strip()

    items: list[dict[str, Any]] = []
    services_map: dict[int, dict[str, Any]] = {}
    page = 1
    paginator: dict[str, Any] | None = None

    try:
        while len(items) < limit:
            data = list_tasks_by_status(
                status_id=status_id,
                page=page,
                pagesize=pagesize,
                fields=fields,
                include=include or None,
                sort=sort or None,
                request_id=getattr(g, "request_id", None),
            )

            tasks = data.get("Tasks") or []
            paginator = data.get("Paginator") or {}

            services = data.get("Services") or []
            for s in services:
                try:
                    services_map[int(s.get("Id"))] = s
                except Exception:
                    continue

            for t in tasks:
                sid = t.get("ServiceId")
                try:
                    svc = services_map.get(int(sid)) if sid is not None else None
                except Exception:
                    svc = None
                if svc:
                    t["ServiceCode"] = svc.get("Code")
                    t["ServiceName"] = svc.get("Name")

            items.extend(tasks)

            # Если страницы кончились — выходим
            page_count = int(paginator.get("PageCount", page))
            if page >= page_count:
                break
            page += 1

        items = items[:limit]

        return jsonify(
            {
                "status_id": status_id,
                "count_returned": len(items),
                "items": items,
                "paginator": paginator,
            }
        ), 200

    except Exception as e:
        logger = current_app.config.get("APP_LOGGER", current_app.logger)
        logger.exception("sd_open failed request_id=%s err=%s", getattr(g, "request_id", "unknown"), str(e))
        return jsonify(
            {
                "status": "error",
                "error": str(e),
                "request_id": getattr(g, "request_id", "unknown"),
            }
        ), 502
