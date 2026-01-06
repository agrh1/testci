"""
Тонкий entrypoint для web-сервиса.

Логика и роуты вынесены в web/app.py через app factory.
"""

from __future__ import annotations

import os

from web.app import create_app

app = create_app()


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
