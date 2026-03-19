"""
Минимальная валидация конфига routing / escalation.

Цель:
- не дать записать в БД заведомо кривой JSON,
- не усложнять (без pydantic / jsonschema).

Формат dest для Mattermost:
  {"platform": "mattermost", "destination_id": "<channel_id>"}
  {"platform": "mattermost", "destination_id": "<channel_id>", "thread_id": "<post_id>"}

Пустой dest {} — допустим (означает «нет назначения»).
"""

from typing import Any, Dict


class ConfigValidationError(ValueError):
    pass


def _require(cond: bool, msg: str):
    if not cond:
        raise ConfigValidationError(msg)


def validate_dest(dest: Dict[str, Any], ctx: str):
    _require(isinstance(dest, dict), f"{ctx} must be object")

    # Пустой dest — допустим
    if not dest:
        return

    platform = dest.get("platform", "mattermost")
    _require(isinstance(platform, str), f"{ctx}.platform must be string")
    _require(platform == "mattermost", f"{ctx}.platform must be 'mattermost' (got '{platform}')")

    destination_id = dest.get("destination_id")
    _require(
        destination_id is None or isinstance(destination_id, str),
        f"{ctx}.destination_id must be string|null",
    )
    _require(
        isinstance(destination_id, str) and destination_id.strip() != "",
        f"{ctx}.destination_id is required and must be non-empty string",
    )

    thread_id = dest.get("thread_id")
    _require(
        thread_id is None or isinstance(thread_id, str),
        f"{ctx}.thread_id must be string|null",
    )


def validate_routing(routing: Dict[str, Any]):
    _require(isinstance(routing, dict), "routing must be object")

    rules = routing.get("rules", [])
    _require(isinstance(rules, list), "routing.rules must be array")

    for i, rule in enumerate(rules):
        _require(isinstance(rule, dict), f"routing.rules[{i}] must be object")
        _require(isinstance(rule.get("enabled", True), bool), f"routing.rules[{i}].enabled must be bool")
        dest = rule.get("dest")
        if dest is not None:
            validate_dest(dest, f"routing.rules[{i}].dest")

    default_dest = routing.get("default_dest")
    if default_dest is not None:
        validate_dest(default_dest, "routing.default_dest")


def validate_escalation(escalation: Dict[str, Any]):
    _require(isinstance(escalation, dict), "escalation must be object")
    _require(isinstance(escalation.get("enabled", False), bool), "escalation.enabled must be bool")

    if escalation.get("enabled"):
        _require(isinstance(escalation.get("after_s"), int), "escalation.after_s must be int")
        if "rules" in escalation:
            rules = escalation.get("rules", [])
            _require(isinstance(rules, list), "escalation.rules must be array")
            for i, rule in enumerate(rules):
                _require(isinstance(rule, dict), f"escalation.rules[{i}] must be object")
                if "after_s" in rule:
                    _require(isinstance(rule.get("after_s"), int), f"escalation.rules[{i}].after_s must be int")
                if "dest" in rule:
                    validate_dest(rule["dest"], f"escalation.rules[{i}].dest")
        else:
            dest = escalation.get("dest")
            if dest is not None:
                validate_dest(dest, "escalation.dest")


def validate_eventlog(eventlog: Dict[str, Any]):
    _require(isinstance(eventlog, dict), "eventlog must be object")

    rules = eventlog.get("rules", [])
    _require(isinstance(rules, list), "eventlog.rules must be array")
    for i, rule in enumerate(rules):
        _require(isinstance(rule, dict), f"eventlog.rules[{i}] must be object")
        _require(isinstance(rule.get("enabled", True), bool), f"eventlog.rules[{i}].enabled must be bool")
        dest = rule.get("dest")
        if dest is not None:
            validate_dest(dest, f"eventlog.rules[{i}].dest")

    default_dest = eventlog.get("default_dest")
    if default_dest is not None:
        validate_dest(default_dest, "eventlog.default_dest")


def validate_config(cfg: Dict[str, Any]):
    _require(isinstance(cfg, dict), "config must be object")
    _require("routing" in cfg, "routing missing")
    _require("escalation" in cfg, "escalation missing")

    validate_routing(cfg["routing"])
    validate_escalation(cfg["escalation"])

    if "eventlog" in cfg:
        validate_eventlog(cfg["eventlog"])
