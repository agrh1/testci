"""Регрессии ожидания HTML eventlog (без сети и внешних сервисов)."""

import asyncio
from unittest.mock import AsyncMock, Mock

import pytest
import requests

from bot.services import eventlog_worker
from bot.services.eventlog_filter_store import EventlogFilter, match_eventlog_filter
from bot.utils import eventlog
from bot.utils.state_store import MemoryStateStore

EVENT_HTML = '''
<div class="formbody">
  <div class="field"><label for="name">Название</label> Тест </div>
  <div class="field"><label for="Date">Дата</label> today </div>
  <div class="field"><label for="Type">Тип</label> Error </div>
  <div class="field"><label for="description">Описание</label><p>Details</p><p>Next line</p></div>
  <div class="field"><label for="other">Другое поле</label> ignored </div>
</div>
'''
ERROR_HTML = '<html><h1>404: событие не найдено</h1></html>'


def response(status, text=""):
    return Mock(status_code=status, text=text, history=[], cookies={})


@pytest.fixture
def web_session(monkeypatch):
    session = Mock()
    session.__enter__ = Mock(return_value=session)
    session.__exit__ = Mock(return_value=False)
    session.post.return_value = response(200)
    monkeypatch.setattr(eventlog.requests, "Session", Mock(return_value=session))
    return session


@pytest.mark.parametrize("status", [200, 201, 206])
def test_get_item_accepts_2xx(web_session, status):
    web_session.get.return_value = response(status, EVENT_HTML)
    assert eventlog.get_item(42, "login", "password", "https://sd/") == EVENT_HTML
    web_session.get.assert_called_once_with(
        "https://sd/eventlog.ivp/view/42", cookies={}, timeout=10.0,
    )
    assert web_session.post.call_args.args == ("https://sd/registertask.ivp",)
    assert web_session.post.call_args.kwargs["timeout"] == 10.0


@pytest.mark.parametrize("status", [302, 401, 403, 404, 500])
def test_get_item_rejects_non_2xx(web_session, status):
    web_session.get.return_value = response(status, ERROR_HTML)
    assert eventlog.get_item(42, "login", "password", "https://sd") is None


def test_login_failure_does_not_fetch_event(web_session):
    web_session.post.return_value = response(403)
    assert eventlog.get_item(42, "login", "password", "https://sd") is None
    web_session.get.assert_not_called()


def test_parse_real_html_with_nested_description():
    message = eventlog.parse_event(EVENT_HTML)
    assert message == {
        "Название": "Тест", "Дата": "today", "Тип": "Error", "Описание": "Details\nNext line",
    }
    assert match_eventlog_filter(EventlogFilter(1, "description", "Details", "contains", True, 0), message)


@pytest.mark.parametrize("html", [
    ERROR_HTML, "", '<form><input name="login"></form>',
    '<div class="formbody"><div class="field"><label for="name">Название</label></div></div>',
    '<div class="formbody">' + ''.join(
        f'<div class="field"><label for="{key}">{key}</label></div>'
        for key in ["name", "Date", "Type", "description"]
    ) + '</div>',
])
def test_parse_error_pages_and_empty_forms(html):
    with pytest.raises(ValueError):
        eventlog.parse_event(html)


@pytest.mark.parametrize("status, html, result_status", [
    (404, ERROR_HTML, "no_item"),
    (200, ERROR_HTML, "parse_error"),
    (204, "", "parse_error"),
    (200, EVENT_HTML, "notified"),
])
def test_poll_once_uses_status_and_valid_html(web_session, monkeypatch, status, html, result_status):
    store = MemoryStateStore()
    store.set_json(eventlog_worker.EVENTLOG_STATE_KEY, {"last_event_id": 41})
    notify = AsyncMock()
    web_session.get.return_value = response(status, html)
    monkeypatch.setattr(eventlog_worker, "get_last_item", Mock(return_value="999"))
    result = asyncio.run(eventlog_worker.eventlog_poll_once(
        notify_eventlog=notify, store=store, filter_store=None,
        login="login", password="password", base_url="https://sd",
    ))
    assert result["status"] == result_status
    assert store.get_json(eventlog_worker.EVENTLOG_STATE_KEY)["last_event_id"] == (
        42 if result_status == "notified" else 41
    )
    if result_status == "notified":
        notify.assert_awaited_once_with("today Error\nТест\nDetails\nNext line", [{"Name": "Error Тест"}])
    else:
        notify.assert_not_awaited()


@pytest.mark.parametrize("status, html", [(404, ERROR_HTML), (200, ERROR_HTML), (204, "")])
def test_loop_waits_for_same_id_until_valid_event(web_session, monkeypatch, status, html):
    store = MemoryStateStore()
    store.set_json(eventlog_worker.EVENTLOG_STATE_KEY, {"last_event_id": 41})
    last_item = Mock(return_value="999")
    monkeypatch.setattr(eventlog_worker, "get_last_item", last_item)
    web_session.get.side_effect = [response(status, html) for _ in range(4)] + [response(200, EVENT_HTML)]
    wait_for = asyncio.wait_for
    polling_waits = []

    async def record_wait(awaitable, timeout):
        polling_waits.append(timeout)
        return await wait_for(awaitable, timeout=timeout)

    monkeypatch.setattr(eventlog_worker.asyncio, "wait_for", record_wait)

    async def run():
        stop = asyncio.Event()
        notify = AsyncMock(side_effect=lambda *_: stop.set())
        await wait_for(eventlog_worker.eventlog_loop(
            stop_event=stop, notify_eventlog=notify, store=store, filter_store=None,
            login="login", password="password", base_url="https://sd",
            poll_interval_s=0.001, keepalive_every=0,
        ), timeout=2)
        notify.assert_awaited_once_with("today Error\nТест\nDetails\nNext line", [{"Name": "Error Тест"}])

    asyncio.run(run())
    assert len(web_session.get.call_args_list) == 5
    assert all(call.args == ("https://sd/eventlog.ivp/view/42",) for call in web_session.get.call_args_list)
    assert store.get_json(eventlog_worker.EVENTLOG_STATE_KEY)["last_event_id"] == 42
    last_item.assert_not_called()
    assert polling_waits == [0.001] * 4


def test_empty_parser_result_is_not_notified(monkeypatch):
    store = MemoryStateStore()
    store.set_json(eventlog_worker.EVENTLOG_STATE_KEY, {"last_event_id": 41})
    notify = AsyncMock()
    monkeypatch.setattr(eventlog_worker, "get_item", Mock(return_value=EVENT_HTML))
    monkeypatch.setattr(eventlog_worker, "parse_event", Mock(return_value={}))
    result = asyncio.run(eventlog_worker.eventlog_poll_once(
        notify_eventlog=notify, store=store, filter_store=None,
        login="login", password="password", base_url="https://sd",
    ))
    assert result["status"] == "parse_error"
    assert store.get_json(eventlog_worker.EVENTLOG_STATE_KEY)["last_event_id"] == 41
    notify.assert_not_awaited()


def test_request_timeout_waits_without_advancing_id(web_session):
    store = MemoryStateStore()
    store.set_json(eventlog_worker.EVENTLOG_STATE_KEY, {"last_event_id": 41})
    web_session.get.side_effect = [requests.Timeout("timeout"), response(200, EVENT_HTML)]

    async def run():
        stop = asyncio.Event()
        notify = AsyncMock(side_effect=lambda *_: stop.set())
        await asyncio.wait_for(eventlog_worker.eventlog_loop(
            stop_event=stop, notify_eventlog=notify, store=store, filter_store=None,
            login="login", password="password", base_url="https://sd",
            poll_interval_s=0.001, keepalive_every=0,
        ), timeout=2)
        notify.assert_awaited_once()

    asyncio.run(run())
    assert len(web_session.get.call_args_list) == 2
    assert all(call.args == ("https://sd/eventlog.ivp/view/42",) for call in web_session.get.call_args_list)
    assert store.get_json(eventlog_worker.EVENTLOG_STATE_KEY)["last_event_id"] == 42


def test_filtered_html_event_is_processed(web_session):
    store = MemoryStateStore()
    store.set_json(eventlog_worker.EVENTLOG_STATE_KEY, {"last_event_id": 41})
    notify = AsyncMock()
    filters = Mock()
    filters.list_enabled = AsyncMock(return_value=[EventlogFilter(1, "type", "Error", "contains", True, 0)])
    filters.increment_hits = AsyncMock()
    web_session.get.return_value = response(200, EVENT_HTML)
    result = asyncio.run(eventlog_worker.eventlog_poll_once(
        notify_eventlog=notify, store=store, filter_store=filters,
        login="login", password="password", base_url="https://sd",
    ))
    assert result["status"] == "filtered"
    assert store.get_json(eventlog_worker.EVENTLOG_STATE_KEY)["last_event_id"] == 42
    filters.increment_hits.assert_awaited_once_with([1])
    notify.assert_not_awaited()


def test_html_list_endpoint_is_preserved(web_session):
    web_session.get.return_value = response(200, '<a href="/eventlog.ivp/view/41">41</a>')
    assert eventlog.get_last_item("login", "password", "https://sd/") == "41"
    web_session.get.assert_called_once_with(
        "https://sd/eventlog.ivp/list", cookies={}, timeout=10.0,
    )
