"""
Проверка наличия ключевых переменных в эталонном env-файле.

Используется как быстрый sanity-check: если в env_example чего-то нет,
значит конфигурация проекта неполная.
"""

from __future__ import annotations

from pathlib import Path

REQUIRED_KEYS = {
    "ENVIRONMENT",
    "STRICT_READINESS",
    "MATTERMOST_API_URL",
    "MATTERMOST_BOT_TOKEN",
    "WEB_BASE_URL",
    "SERVICEDESK_BASE_URL",
    "SERVICEDESK_LOGIN",
    "SERVICEDESK_PASSWORD",
}

TEMPLATE_FILES = [
    Path("env_example"),
]


def parse_keys(text: str) -> set[str]:
    keys: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "=" not in line:
            continue
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def main() -> None:
    errors: list[str] = []

    for p in TEMPLATE_FILES:
        if not p.exists():
            errors.append(f"Template file not found: {p}")
            continue
        keys = parse_keys(p.read_text(encoding="utf-8"))
        missing = REQUIRED_KEYS - keys
        if missing:
            errors.append(f"{p}: missing keys: {', '.join(sorted(missing))}")

    if errors:
        raise SystemExit("ENV template check failed:\n" + "\n".join(errors))

    print("ENV template check passed.")


if __name__ == "__main__":
    main()
