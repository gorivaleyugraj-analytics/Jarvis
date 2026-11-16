"""OpenAI-compatible chat adapter.

Works with any endpoint that implements POST /chat/completions (OpenAI,
OpenRouter, llama.cpp server, vLLM, Ollama's compat layer...). Enabled only
when an API key is configured; all failures raise ProviderError so the router
can fall back to the offline provider.
"""
from __future__ import annotations

import httpx

from ..core import config
from ..core.logging_setup import get_logger
from .base import ChatMessage, ModelProvider, ProviderError, ProviderReply

log = get_logger("providers.openai_compat")

# Rough published prices (USD per 1K tokens) for cost awareness (spec §17).
# Unknown models are treated as free — the estimate is conservative, not a bill.
_PRICE_PER_1K: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
    "gpt-4.1": (0.002, 0.008),
}


def _estimate_cost(model: str, prompt_tokens: int, completion_tokens: int) -> float:
    pin, pout = _PRICE_PER_1K.get(model.lower(), (0.0, 0.0))
    return round((prompt_tokens * pin + completion_tokens * pout) / 1000, 6)


class OpenAICompatProvider(ModelProvider):
    name = "openai_compat"

    def __init__(self) -> None:
        self.model = config.settings.openai_model

    def available(self) -> bool:
        return config.provider_configured()

    def chat(
        self,
        messages: list[ChatMessage],
        *,
        temperature: float = 0.4,
        max_tokens: int = 1024,
    ) -> ProviderReply:
        if not self.available():
            raise ProviderError("no API key configured for openai_compat provider")

        payload = {
            "model": self.model,
            "messages": [m.as_dict() for m in messages],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {
            "Authorization": f"Bearer {config.settings.openai_api_key}",
            "Content-Type": "application/json",
        }
        url = f"{config.settings.openai_base_url}/chat/completions"
        try:
            response = httpx.post(
                url,
                json=payload,
                headers=headers,
                timeout=config.settings.provider_timeout_s,
            )
            response.raise_for_status()
            data = response.json()
        except httpx.HTTPError as exc:
            log.warning("provider request failed: %s", exc)
            raise ProviderError(f"provider request failed: {exc}") from exc
        except ValueError as exc:
            raise ProviderError(f"provider returned invalid JSON: {exc}") from exc

        try:
            choice = data["choices"][0]
            text = choice["message"]["content"] or ""
            usage = data.get("usage", {}) or {}
        except (KeyError, IndexError, TypeError) as exc:
            raise ProviderError(f"unexpected provider response shape: {exc}") from exc

        prompt_tokens = int(usage.get("prompt_tokens") or 0)
        completion_tokens = int(usage.get("completion_tokens") or 0)
        return ProviderReply(
            text=text.strip(),
            provider=self.name,
            model=data.get("model", self.model),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_usd=_estimate_cost(data.get("model", self.model), prompt_tokens, completion_tokens),
        )
