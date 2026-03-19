"""
"Заслонка" для web-зависимых команд.

Проверяет доступность web-сервиса перед выполнением команд.
"""

from __future__ import annotations

from dataclasses import dataclass

from .web_client import WebCheckResult, WebClient


@dataclass(frozen=True)
class GuardDecision:
    allowed: bool
    reason: str
    health: WebCheckResult
    ready: WebCheckResult


class WebGuard:
    def __init__(self, client: WebClient) -> None:
        self.client = client

    async def decide(self) -> GuardDecision:
        health, ready = await self.client.check_health_ready()

        if not health.ok:
            return GuardDecision(
                allowed=False,
                reason="WEB_UNAVAILABLE",
                health=health,
                ready=ready,
            )

        if not ready.ok:
            return GuardDecision(
                allowed=False,
                reason="WEB_NOT_READY",
                health=health,
                ready=ready,
            )

        return GuardDecision(
            allowed=True,
            reason="OK",
            health=health,
            ready=ready,
        )
