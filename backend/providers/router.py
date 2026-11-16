"""Model router (spec §7.1.5): picks a provider per request and falls back
gracefully. Routing considers the configured mode, a per-request privacy flag
("local" forces the offline provider) and the daily cost budget (spec §7.7.54).
"""
from __future__ import annotations

from datetime import datetime, timezone

from ..core import config, db
from ..core.logging_setup import get_logger
from .base import ChatMessage, ModelProvider, ProviderError, ProviderReply
from .offline import OfflineProvider
from .openai_compat import OpenAICompatProvider

log = get_logger("router")


class ModelRouter:
    def __init__(self) -> None:
        self.offline = OfflineProvider()
        self.remote = OpenAICompatProvider()

    # -- selection ---------------------------------------------------------
    def describe(self) -> dict:
        return {
            "mode": config.settings.provider_mode,
            "remote_configured": self.remote.available(),
            "remote_model": self.remote.model,
            "offline_model": self.offline.model,
            "daily_cost_limit_usd": config.settings.daily_cost_limit_usd,
            "cost_today_usd": self.cost_today(),
        }

    def _primary(self, privacy: str) -> ModelProvider:
        if privacy == "local":
            return self.offline
        mode = config.settings.provider_mode
        if mode == "offline":
            return self.offline
        if mode == "openai_compat":
            return self.remote
        # auto: remote when configured, offline otherwise
        return self.remote if self.remote.available() else self.offline

    # -- budget ------------------------------------------------------------
    def cost_today(self) -> float:
        today = datetime.now(timezone.utc).date().isoformat()
        row = db.one(
            "SELECT COALESCE(SUM(cost_usd), 0) AS total FROM model_usage WHERE substr(ts, 1, 10) = ?",
            (today,),
        )
        return round(float(row["total"]) if row else 0.0, 6)

    def _budget_available(self) -> bool:
        limit = config.settings.daily_cost_limit_usd
        return limit <= 0 or self.cost_today() < limit

    # -- main entry --------------------------------------------------------
    def chat(
        self,
        messages: list[ChatMessage],
        *,
        privacy: str = "normal",
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> ProviderReply:
        provider = self._primary(privacy)
        notice = ""

        if provider is self.remote and not self._budget_available():
            provider = self.offline
            notice = f"Daily cost limit of ${config.settings.daily_cost_limit_usd:.2f} reached — answered offline."

        if provider is self.remote:
            try:
                reply = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
                self._record_usage(reply)
                reply.notice = notice
                return reply
            except ProviderError as exc:
                log.warning("remote provider failed, falling back offline: %s", exc)
                provider = self.offline
                notice = f"Remote provider unavailable ({exc}); answered offline."

        reply = provider.chat(messages, temperature=temperature, max_tokens=max_tokens)
        reply.degraded = notice != "" or config.settings.provider_mode == "offline"
        reply.notice = notice
        return reply

    def _record_usage(self, reply: ProviderReply) -> None:
        db.execute(
            "INSERT INTO model_usage (ts, provider, model, prompt_tokens, completion_tokens, "
            "cost_usd) VALUES (?, ?, ?, ?, ?, ?)",
            (
                db.now_iso(),
                reply.provider,
                reply.model,
                reply.prompt_tokens,
                reply.completion_tokens,
                reply.cost_usd,
            ),
        )


router = ModelRouter()
