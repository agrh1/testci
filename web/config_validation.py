"""
Минимальная валидация конфига routing / escalation.

Цель:
- не дать записать в БД заведомо кривой JSON,
- не усложнять (без pydantic / jsonschema).
"""

from typing import Any, Dict


class ConfigValidationError(ValueError):
    pass


def _require(cond: bool, msg: str):
    if not cond:
        raise ConfigValidationError(msg)


def validate_dest(dest: Dict[str, Any], ctx: str):
    _require(isinstance(dest, dict), f"{ctx}.dest must be object")

    chat_id = dest.get("chat_id")
    thread_id = dest.get("thread_id")

    _require(chat_id is None or isinstance(chat_id, int), f"{ctx}.dest.chat_id must be int|null")
    _require(thread_id is None or isinstance(thread_id, int), f"{ctx}.dest.thread_id must be int|null")


def validate_routing(routing: Dict[str, Any]):
    _require(isinstance(routing, dict), "routing must be object")

    rules = routing.get("rules", [])
    _require(isinstance(rules, list), "routing.rules must be array")

    for i, rule in enumerate(rules):
        _require(isinstance(rule, dict), f"routing.rules[{i}] must be object")
        _require(isinstance(rule.get("enabled", True), bool), f"routing.rules[{i}].enabled must be bool")
        validate_dest(rule.get("dest", {}), f"routing.rules[{i}]")

    validate_dest(routing.get("default_dest", {}), "routing.default_dest")


def validate_escalation(escalation: Dict[str, Any]):
    _require(isinstance(escalation, dict), "escalation must be object")
    _require(isinstance(escalation.get("enabled", False), bool), "escalation.enabled must be bool")

    if escalation.get("enabled"):
        _require(isinstance(escalation.get("after_s"), int), "escalation.after_s must be int")
        validate_dest(escalation.get("dest", {}), "escalation.dest")


def validate_eventlog(eventlog: Dict[str, Any]):
    _require(isinstance(eventlog, dict), "eventlog must be object")

    rules = eventlog.get("rules", [])
    _require(isinstance(rules, list), "eventlog.rules must be array")
    for i, rule in enumerate(rules):
        _require(isinstance(rule, dict), f"eventlog.rules[{i}] must be object")
        _require(isinstance(rule.get("enabled", True), bool), f"eventlog.rules[{i}].enabled must be bool")
        validate_dest(rule.get("dest", {}), f"eventlog.rules[{i}]")

    validate_dest(eventlog.get("default_dest", {}), "eventlog.default_dest")


def validate_config(cfg: Dict[str, Any]):
    _require(isinstance(cfg, dict), "config must be object")
    _require("routing" in cfg, "routing missing")
    _require("escalation" in cfg, "escalation missing")

    validate_routing(cfg["routing"])
    validate_escalation(cfg["escalation"])

    if "eventlog" in cfg:
        validate_eventlog(cfg["eventlog"])
